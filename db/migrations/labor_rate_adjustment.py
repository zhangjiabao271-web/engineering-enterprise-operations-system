from datetime import datetime
from uuid import uuid4


def _columns(conn, table):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn, table, definition):
    name = definition.split()[0]
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def add_labor_rate_adjustment(conn):
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    _add_column(conn, "work_logs", "rate_locked INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "work_logs", "rate_lock_reason TEXT")
    _add_column(conn, "work_logs", "rate_locked_at TEXT")

    conn.execute(
        """CREATE TABLE IF NOT EXISTS worker_rate_versions (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               public_id TEXT NOT NULL UNIQUE,
               worker_id INTEGER NOT NULL,
               rate_minor INTEGER NOT NULL CHECK(rate_minor >= 0),
               effective_from TEXT NOT NULL,
               effective_to TEXT,
               reason TEXT,
               source TEXT NOT NULL DEFAULT 'adjustment',
               status TEXT NOT NULL DEFAULT 'active'
                   CHECK(status IN ('active', 'superseded')),
               created_at TEXT NOT NULL,
               FOREIGN KEY(worker_id) REFERENCES workers(id) ON DELETE RESTRICT
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_worker_rate_versions_lookup
           ON worker_rate_versions(worker_id, status, effective_from, id)"""
    )

    conn.execute(
        """CREATE TABLE IF NOT EXISTS labor_rate_adjustments (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               public_id TEXT NOT NULL UNIQUE,
               worker_id INTEGER NOT NULL,
               old_rate_minor INTEGER NOT NULL CHECK(old_rate_minor >= 0),
               new_rate_minor INTEGER NOT NULL CHECK(new_rate_minor >= 0),
               effective_from TEXT NOT NULL,
               range_end TEXT,
               scope_mode TEXT NOT NULL
                   CHECK(scope_mode IN ('future_only', 'through_today', 'custom')),
               project_id INTEGER,
               reason TEXT NOT NULL,
               affected_count INTEGER NOT NULL DEFAULT 0,
               skipped_locked_count INTEGER NOT NULL DEFAULT 0,
               total_days REAL NOT NULL DEFAULT 0,
               old_amount_minor INTEGER NOT NULL DEFAULT 0,
               new_amount_minor INTEGER NOT NULL DEFAULT 0,
               delta_minor INTEGER NOT NULL DEFAULT 0,
               status TEXT NOT NULL DEFAULT 'applied'
                   CHECK(status IN ('applied', 'reversed')),
               created_at TEXT NOT NULL,
               FOREIGN KEY(worker_id) REFERENCES workers(id) ON DELETE RESTRICT,
               FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE RESTRICT
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_labor_adjustments_worker
           ON labor_rate_adjustments(worker_id, effective_from, id)"""
    )

    conn.execute(
        """CREATE TABLE IF NOT EXISTS labor_rate_adjustment_items (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               adjustment_id INTEGER NOT NULL,
               work_log_id INTEGER NOT NULL,
               project_id INTEGER,
               old_daily_rate_minor INTEGER NOT NULL,
               new_daily_rate_minor INTEGER NOT NULL,
               old_amount_minor INTEGER NOT NULL,
               new_amount_minor INTEGER NOT NULL,
               created_at TEXT NOT NULL,
               FOREIGN KEY(adjustment_id) REFERENCES labor_rate_adjustments(id)
                   ON DELETE RESTRICT,
               FOREIGN KEY(work_log_id) REFERENCES work_logs(id) ON DELETE RESTRICT,
               FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE RESTRICT,
               UNIQUE(adjustment_id, work_log_id)
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_labor_adjustment_items_log
           ON labor_rate_adjustment_items(work_log_id, adjustment_id)"""
    )

    conn.execute(
        """CREATE TABLE IF NOT EXISTS labor_rate_lock_events (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               public_id TEXT NOT NULL UNIQUE,
               work_log_id INTEGER NOT NULL,
               action TEXT NOT NULL CHECK(action IN ('lock', 'unlock')),
               reason TEXT,
               created_at TEXT NOT NULL,
               FOREIGN KEY(work_log_id) REFERENCES work_logs(id) ON DELETE RESTRICT
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_labor_lock_events_log
           ON labor_rate_lock_events(work_log_id, id)"""
    )

    for worker in conn.execute(
        """SELECT w.id, w.daily_rate
           FROM workers w
           WHERE NOT EXISTS (
               SELECT 1 FROM worker_rate_versions wrv WHERE wrv.worker_id=w.id
           )"""
    ).fetchall():
        rate_minor = round(float(worker["daily_rate"] or 0) * 100)
        conn.execute(
            """INSERT INTO worker_rate_versions (
                   public_id, worker_id, rate_minor, effective_from,
                   reason, source, status, created_at
               ) VALUES (?, ?, ?, '1900-01-01', ?, 'migration', 'active', ?)""",
            (
                str(uuid4()),
                worker["id"],
                rate_minor,
                "现有默认日工资迁移基线；历史工天仍以各自金额快照为准",
                now,
            ),
        )


MIGRATIONS = [
    (220, "工人工资版本、批量调薪、锁定与审计", add_labor_rate_adjustment),
]
