import os
import sqlite3
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
