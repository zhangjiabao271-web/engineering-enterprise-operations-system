import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("SUPPLY_CHAIN_DB_PATH", PROJECT_ROOT / "supplier_data.db"))


def get_connection(db_path=None):
    """Return an isolated, constraint-enabled SQLite connection.

    Connections are intentionally not shared across threads. Callers own and close
    the returned connection, which matches the existing repository functions.
    """
    path = Path(db_path) if db_path else DB_PATH
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def db_transaction(db_path=None, *, immediate=False):
    """统一的写事务样板：提交/回滚/关闭一处生效。

    with db_transaction() as conn:
        conn.execute(...)  # 正常返回即提交，抛异常即回滚
    immediate=True 时用 BEGIN IMMEDIATE 预先拿写锁，避免多写者升级死锁。
    """
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
    """只读查询样板：不产生提交，连接用完即关。"""
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()
