"""Independent cash facts; never infer historical payments from cost flags."""


def migrate(conn):
    statements = [
        """CREATE TABLE fund_accounts (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL CHECK(kind IN ('bank','personal','wallet','cash')),
            opening_date TEXT NOT NULL, opening_minor INTEGER NOT NULL CHECK(opening_minor>=0),
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived')),
            notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE fund_source_openings (
            source_kind TEXT NOT NULL CHECK(source_kind IN ('purchase','cost','wage')),
            source_id INTEGER NOT NULL, source_period TEXT NOT NULL DEFAULT '',
            settled_minor INTEGER NOT NULL CHECK(settled_minor>=0),
            payee TEXT NOT NULL, reason TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(source_kind,source_id,source_period)
        )""",
        """CREATE TABLE fund_plans (
            id INTEGER PRIMARY KEY, direction TEXT NOT NULL CHECK(direction IN ('in','out')),
            title TEXT NOT NULL, counterparty TEXT NOT NULL DEFAULT '',
            amount_minor INTEGER NOT NULL CHECK(amount_minor>0), due_date TEXT,
            source_kind TEXT NOT NULL DEFAULT '', source_id INTEGER,
            source_period TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned','cancelled')),
            notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE fund_transactions (
            id INTEGER PRIMARY KEY, request_key TEXT NOT NULL UNIQUE,
            from_account_id INTEGER REFERENCES fund_accounts(id),
            to_account_id INTEGER REFERENCES fund_accounts(id),
            transaction_date TEXT NOT NULL, amount_minor INTEGER NOT NULL CHECK(amount_minor>0),
            category TEXT NOT NULL, counterparty TEXT NOT NULL DEFAULT '',
            source_kind TEXT NOT NULL DEFAULT '', source_id INTEGER,
            source_period TEXT NOT NULL DEFAULT '', source_label TEXT NOT NULL DEFAULT '',
            plan_id INTEGER REFERENCES fund_plans(id),
            notes TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active','void')),
            void_reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, voided_at TEXT,
            CHECK(from_account_id IS NOT NULL OR to_account_id IS NOT NULL),
            CHECK(from_account_id IS NULL OR to_account_id IS NULL OR from_account_id<>to_account_id)
        )""",
        """CREATE INDEX idx_fund_transactions_source ON fund_transactions(
            source_kind,source_id,source_period,status)""",
        "CREATE INDEX idx_fund_transactions_date ON fund_transactions(transaction_date,status)",
        """CREATE TABLE fund_reconciliations (
            id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES fund_accounts(id),
            balance_date TEXT NOT NULL, actual_minor INTEGER NOT NULL CHECK(actual_minor>=0),
            book_minor INTEGER NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL
        )""",
        """CREATE TABLE fund_audit (
            id INTEGER PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
            action TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL,
            reason TEXT NOT NULL, created_at TEXT NOT NULL
        )""",
    ]
    for sql in statements:
        conn.execute(sql)

    # Protect linked money even when business records are changed by an import.
    sources = {
        'receipts': ('receipt', ('amount_minor', 'receipt_date', 'payment_method', 'status')),
        'purchase_orders': ('purchase', ('total_amount_cents', 'purchase_date',
                                        'supplier_partner_id', 'merchant_name_snapshot',
                                        'payment_method', 'status')),
        'cost_entries': ('cost', ('amount_minor', 'cost_date', 'counterparty_name_snapshot', 'status')),
    }
    for table, (kind, fields) in sources.items():
        linked = f"""EXISTS(SELECT 1 FROM fund_transactions
            WHERE source_kind='{kind}' AND source_id=OLD.id AND status='active')"""
        changed = ' OR '.join(f'NEW.{field} IS NOT OLD.{field}' for field in fields)
        conn.execute(f"""CREATE TRIGGER fund_guard_{kind}_update
            BEFORE UPDATE OF {','.join(fields)} ON {table}
            WHEN ({changed}) AND {linked}
            BEGIN SELECT RAISE(ABORT,'已有资金流水关联，请先在资金管理中作废错误流水，再修改原单据'); END""")
        conn.execute(f"""CREATE TRIGGER fund_guard_{kind}_delete
            BEFORE DELETE ON {table} WHEN EXISTS(SELECT 1 FROM fund_transactions
                WHERE source_kind='{kind}' AND source_id=OLD.id)
            BEGIN SELECT RAISE(ABORT,'已关联资金历史，不能删除原单据'); END""")

    wage_link = """EXISTS(SELECT 1 FROM fund_transactions
        WHERE source_kind='wage' AND source_id=OLD.worker_id
          AND source_period=substr(OLD.work_date,1,7) AND status='active')"""
    conn.execute(f"""CREATE TRIGGER fund_guard_wage_update
        BEFORE UPDATE OF amount_minor,amount,worker_id,work_date,status ON work_logs
        WHEN (NEW.amount_minor IS NOT OLD.amount_minor OR NEW.amount IS NOT OLD.amount
          OR NEW.worker_id IS NOT OLD.worker_id OR NEW.work_date IS NOT OLD.work_date
          OR NEW.status IS NOT OLD.status) AND {wage_link}
        BEGIN SELECT RAISE(ABORT,'该月已有工资资金流水，请先作废错误付款，再修改工天金额或月份'); END""")
    conn.execute(f"""CREATE TRIGGER fund_guard_wage_delete BEFORE DELETE ON work_logs
        WHEN {wage_link}
        BEGIN SELECT RAISE(ABORT,'该月已有工资付款，不能删除工天'); END""")


MIGRATIONS = [(510, '资金账户、真实收支、待收待付与余额核对', migrate)]
