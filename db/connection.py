import os
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("SUPPLY_CHAIN_DB_PATH", PROJECT_ROOT / "supplier_data.db"))
_READ_SNAPSHOT = ContextVar('business_read_snapshot', default=None)
_WRITE_TRANSACTION = ContextVar('business_write_transaction', default=None)


@contextmanager
def use_write_transaction(conn):
    """Compose existing services inside one caller-owned atomic transaction."""
    if _READ_SNAPSHOT.get() is not None or not conn.in_transaction:
        raise ValueError('共享写入必须使用已开启的独立事务')
    if conn.execute('PRAGMA query_only').fetchone()[0]:
        raise ValueError('只读连接不能共享写入')
    token = _WRITE_TRANSACTION.set(conn)
    try:
        yield
    finally:
        _WRITE_TRANSACTION.reset(token)


@contextmanager
def use_read_snapshot(conn):
    """Reuse one caller-owned, read-only snapshot across nested business services."""
    if not conn.in_transaction or conn.execute('PRAGMA query_only').fetchone()[0] != 1:
        raise ValueError('共享查询必须使用已开启事务的只读快照')
    token = _READ_SNAPSHOT.set(conn)
    try:
        yield
    finally:
        _READ_SNAPSHOT.reset(token)


def get_connection(db_path=None):
    """Return an isolated, constraint-enabled SQLite connection.

    Connections are intentionally not shared across threads. Callers own and close
    the returned connection, which matches the existing repository functions.
    """
    if _READ_SNAPSHOT.get() is not None:
        raise RuntimeError('只读查询中不能打开写连接')
    path = Path(db_path) if db_path else DB_PATH
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=FULL")
    return conn


@contextmanager
def db_transaction(db_path=None, *, immediate=False):
    """统一的写事务样板：提交/回滚/关闭一处生效。

    with db_transaction() as conn:
        conn.execute(...)  # 正常返回即提交，抛异常即回滚
    immediate=True 时用 BEGIN IMMEDIATE 预先拿写锁，避免多写者升级死锁。
    """
    shared = _WRITE_TRANSACTION.get()
    if shared is not None:
        if db_path is not None:
            raise ValueError('共享写入不能切换数据库')
        yield shared
        return
    conn = get_connection(db_path)
    try:
        if immediate:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def db_read(db_path=None):
    """OS-level read-only connection and a consistent snapshot across SELECTs."""
    shared = _READ_SNAPSHOT.get() or _WRITE_TRANSACTION.get()
    if shared is not None:
        if db_path is not None:
            raise ValueError('共享只读查询不能切换数据库')
        yield shared
        return
    path = Path(db_path) if db_path else DB_PATH
    conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA query_only=ON')
        conn.execute('PRAGMA busy_timeout=5000')
        conn.execute('BEGIN')
        yield conn
    finally:
        conn.close()
