"""Optional planning data, independent of account balances and business facts."""


def migrate(conn):
    for sql in (
        """CREATE TABLE cash_budget_snapshots (
            id INTEGER PRIMARY KEY, balance_date TEXT NOT NULL,
            amount_minor INTEGER NOT NULL CHECK(amount_minor>=0),
            reserve_minor INTEGER NOT NULL CHECK(reserve_minor>=0),
            notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)""",
        """CREATE TABLE cash_budget_rules (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL UNIQUE,
            amount_minor INTEGER NOT NULL CHECK(amount_minor>0),
            cadence TEXT NOT NULL CHECK(cadence IN ('daily','monthly','yearly')),
            next_date TEXT NOT NULL, end_date TEXT,
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
            use_average INTEGER NOT NULL DEFAULT 0 CHECK(use_average IN (0,1)),
            notes TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL)""",
        """CREATE TABLE cash_budget_events (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL, direction TEXT NOT NULL CHECK(direction IN ('in','out')),
            amount_minor INTEGER NOT NULL CHECK(amount_minor>0), due_date TEXT,
            source_kind TEXT NOT NULL DEFAULT '', source_id INTEGER,
            source_period TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned','done','cancelled')),
            notes TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL)""",
        """CREATE UNIQUE INDEX cash_budget_source ON cash_budget_events(source_kind,source_id,source_period)
            WHERE source_kind<>'' AND status='planned'""",
        """CREATE TABLE cash_budget_months (
            month TEXT PRIMARY KEY, confirmed_at TEXT NOT NULL)""",
        """CREATE TABLE cash_budget_audit (
            id INTEGER PRIMARY KEY, entity TEXT NOT NULL, entity_id INTEGER,
            before_json TEXT NOT NULL, after_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
    ):
        conn.execute(sql)


MIGRATIONS = [(520, 'Cash runway budgets independent of historical ledgers', migrate)]
