"""Add an optional agreed-income boundary for cash engineering projects."""


def add_cash_project_agreed_amount(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(projects)")}
    if "cash_agreed_amount_minor" in columns:
        return
    conn.execute(
        """ALTER TABLE projects ADD COLUMN cash_agreed_amount_minor INTEGER
           CHECK(cash_agreed_amount_minor IS NULL OR cash_agreed_amount_minor > 0)"""
    )


MIGRATIONS = [
    (400, "零星现金工程约定总额与完工确认上限", add_cash_project_agreed_amount),
]
