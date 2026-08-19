"""Add an overtime marker to work-log facts without changing payroll amounts."""


def add_work_log_overtime(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(work_logs)")}
    if "is_overtime" not in columns:
        conn.execute(
            """ALTER TABLE work_logs
               ADD COLUMN is_overtime INTEGER NOT NULL DEFAULT 0
               CHECK(is_overtime IN (0, 1))"""
        )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_work_logs_overtime
           ON work_logs(is_overtime, work_date)"""
    )


MIGRATIONS = [
    (260, "工天记录加班标记与看板汇总", add_work_log_overtime),
]
