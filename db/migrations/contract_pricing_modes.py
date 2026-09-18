"""Add pricing semantics for fixed, provisional, and actual contracts."""


def add_contract_pricing_modes(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(contracts)")}
    if "pricing_mode" not in columns:
        conn.execute(
            """ALTER TABLE contracts ADD COLUMN pricing_mode TEXT NOT NULL
               DEFAULT 'fixed'
               CHECK(pricing_mode IN ('fixed', 'provisional', 'actual'))"""
        )
    if "control_limit_minor" not in columns:
        conn.execute(
            """ALTER TABLE contracts ADD COLUMN control_limit_minor INTEGER
               CHECK(control_limit_minor IS NULL OR control_limit_minor > 0)"""
        )


MIGRATIONS = [
    (380, "合同固定总价、暂定总价与单价据实结算", add_contract_pricing_modes),
]
