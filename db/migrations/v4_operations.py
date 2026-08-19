from datetime import datetime
from uuid import uuid4


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _execute_script(conn, script):
    """Execute schema statements without sqlite3.executescript's implicit commit.

    sqlite3.executescript() issues an implicit COMMIT before running, which
    breaks migration_runner's BEGIN IMMEDIATE transaction: DDL would be
    committed even if a later DML step fails. Splitting on ';' and using
    conn.execute() keeps every statement inside the caller's transaction.
    """
    for statement in script.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)


def _reference(prefix, row):
    value = (row["reference_no"] or "").strip()
    return value or f"{prefix}-{row['id']:06d}"


def create_contract_and_settlement_facts(conn):
    _execute_script(
        conn,
        """
        CREATE TABLE IF NOT EXISTS contracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            organization_id INTEGER NOT NULL,
            contract_no TEXT NOT NULL,
            name TEXT NOT NULL,
            customer_partner_id INTEGER,
            customer_name_snapshot TEXT NOT NULL DEFAULT '',
            contract_type TEXT NOT NULL DEFAULT 'project'
                CHECK(contract_type IN ('annual', 'project', 'supplement')),
            sign_date TEXT NOT NULL,
            start_date TEXT,
            end_date TEXT,
            tax_inclusive_amount_minor INTEGER NOT NULL
                CHECK(tax_inclusive_amount_minor >= 0),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('draft', 'active', 'completed', 'void')),
            notes TEXT,
            source_legacy_entry_id INTEGER UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (organization_id) REFERENCES organizations(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (customer_partner_id) REFERENCES business_partners(id)
                ON DELETE RESTRICT,
            UNIQUE (organization_id, contract_no)
        );

        CREATE TABLE IF NOT EXISTS contract_project_allocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            contract_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            allocated_amount_minor INTEGER NOT NULL
                CHECK(allocated_amount_minor > 0),
            notes TEXT,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'void')),
            source_legacy_entry_id INTEGER UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (contract_id) REFERENCES contracts(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (project_id) REFERENCES projects(id)
                ON DELETE RESTRICT
        );

        CREATE UNIQUE INDEX IF NOT EXISTS
            idx_contract_allocations_active_project
        ON contract_project_allocations(contract_id, project_id)
        WHERE status='active';

        CREATE INDEX IF NOT EXISTS idx_contracts_status
        ON contracts(status, sign_date);

        CREATE INDEX IF NOT EXISTS idx_contract_allocations_project
        ON contract_project_allocations(project_id, status);

        CREATE TABLE IF NOT EXISTS settlements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            organization_id INTEGER NOT NULL,
            settlement_no TEXT NOT NULL,
            contract_id INTEGER,
            project_id INTEGER NOT NULL,
            settlement_date TEXT NOT NULL,
            period_start TEXT,
            period_end TEXT,
            amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
            basis TEXT,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'void')),
            source_legacy_entry_id INTEGER UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (organization_id) REFERENCES organizations(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (contract_id) REFERENCES contracts(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (project_id) REFERENCES projects(id)
                ON DELETE RESTRICT,
            UNIQUE (organization_id, settlement_no)
        );

        CREATE INDEX IF NOT EXISTS idx_settlements_project
        ON settlements(project_id, settlement_date, status);
        """
    )

    now = _now()
    rows = conn.execute(
        """SELECT poe.*, p.customer_partner_id,
                  COALESCE(bp.legal_name, p.customer_name, '') AS customer_name
           FROM project_operating_entries poe
           JOIN projects p ON p.id=poe.project_id
           LEFT JOIN business_partners bp ON bp.id=p.customer_partner_id
           WHERE poe.status='active'
             AND poe.entry_type IN ('contract_value', 'settlement')
           ORDER BY poe.id"""
    ).fetchall()
    for row in rows:
        if row["entry_type"] == "contract_value":
            contract_no = _reference("LEGACY-CONTRACT", row)
            contract_id = conn.execute(
                """INSERT OR IGNORE INTO contracts (
                       public_id, organization_id, contract_no, name,
                       customer_partner_id, customer_name_snapshot,
                       contract_type, sign_date, tax_inclusive_amount_minor,
                       status, notes, source_legacy_entry_id,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, 'project', ?, ?, 'active',
                             ?, ?, ?, ?)""",
                (
                    str(uuid4()),
                    row["organization_id"],
                    contract_no,
                    f"历史项目合同分配 · {contract_no}",
                    row["customer_partner_id"],
                    row["customer_name"],
                    row["entry_date"],
                    row["amount_minor"],
                    row["notes"],
                    row["id"],
                    now,
                    now,
                ),
            ).lastrowid
            if not contract_id:
                contract_id = conn.execute(
                    "SELECT id FROM contracts WHERE source_legacy_entry_id=?",
                    (row["id"],),
                ).fetchone()["id"]
            conn.execute(
                """INSERT OR IGNORE INTO contract_project_allocations (
                       public_id, contract_id, project_id,
                       allocated_amount_minor, notes, status,
                       source_legacy_entry_id, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
                (
                    str(uuid4()),
                    contract_id,
                    row["project_id"],
                    row["amount_minor"],
                    row["notes"],
                    row["id"],
                    now,
                    now,
                ),
            )
        else:
            conn.execute(
                """INSERT OR IGNORE INTO settlements (
                       public_id, organization_id, settlement_no, project_id,
                       settlement_date, amount_minor, basis, status,
                       source_legacy_entry_id, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
                (
                    str(uuid4()),
                    row["organization_id"],
                    _reference("LEGACY-SETTLEMENT", row),
                    row["project_id"],
                    row["entry_date"],
                    row["amount_minor"],
                    row["notes"],
                    row["id"],
                    now,
                    now,
                ),
            )


def create_invoice_and_receipt_facts(conn):
    _execute_script(
        conn,
        """
        CREATE TABLE IF NOT EXISTS sales_invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            organization_id INTEGER NOT NULL,
            invoice_no TEXT NOT NULL,
            project_id INTEGER NOT NULL,
            contract_id INTEGER,
            invoice_date TEXT NOT NULL,
            amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
            tax_rate_bps INTEGER NOT NULL DEFAULT 0
                CHECK(tax_rate_bps BETWEEN 0 AND 10000),
            buyer_name_snapshot TEXT NOT NULL DEFAULT '',
            notes TEXT,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'void')),
            source_legacy_entry_id INTEGER UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (organization_id) REFERENCES organizations(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (project_id) REFERENCES projects(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (contract_id) REFERENCES contracts(id)
                ON DELETE RESTRICT,
            UNIQUE (organization_id, invoice_no)
        );

        CREATE TABLE IF NOT EXISTS receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            organization_id INTEGER NOT NULL,
            receipt_no TEXT NOT NULL,
            receipt_date TEXT NOT NULL,
            payer_name_snapshot TEXT NOT NULL DEFAULT '',
            amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
            payment_method TEXT NOT NULL DEFAULT '银行转账',
            notes TEXT,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'void')),
            source_legacy_entry_id INTEGER UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (organization_id) REFERENCES organizations(id)
                ON DELETE RESTRICT,
            UNIQUE (organization_id, receipt_no)
        );

        CREATE TABLE IF NOT EXISTS receipt_allocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            receipt_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            contract_id INTEGER,
            invoice_id INTEGER,
            allocated_amount_minor INTEGER NOT NULL
                CHECK(allocated_amount_minor > 0),
            notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (receipt_id) REFERENCES receipts(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (project_id) REFERENCES projects(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (contract_id) REFERENCES contracts(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (invoice_id) REFERENCES sales_invoices(id)
                ON DELETE RESTRICT
        );

        CREATE INDEX IF NOT EXISTS idx_sales_invoices_project
        ON sales_invoices(project_id, invoice_date, status);

        CREATE INDEX IF NOT EXISTS idx_receipt_allocations_project
        ON receipt_allocations(project_id);
        """
    )

    now = _now()
    rows = conn.execute(
        """SELECT * FROM project_operating_entries
           WHERE status='active' AND entry_type IN ('invoice', 'receipt')
           ORDER BY id"""
    ).fetchall()
    for row in rows:
        if row["entry_type"] == "invoice":
            conn.execute(
                """INSERT OR IGNORE INTO sales_invoices (
                       public_id, organization_id, invoice_no, project_id,
                       invoice_date, amount_minor, buyer_name_snapshot, notes,
                       status, source_legacy_entry_id, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
                (
                    str(uuid4()),
                    row["organization_id"],
                    _reference("LEGACY-INVOICE", row),
                    row["project_id"],
                    row["entry_date"],
                    row["amount_minor"],
                    row["counterparty_name"] or "",
                    row["notes"],
                    row["id"],
                    now,
                    now,
                ),
            )
        else:
            receipt_id = conn.execute(
                """INSERT OR IGNORE INTO receipts (
                       public_id, organization_id, receipt_no, receipt_date,
                       payer_name_snapshot, amount_minor, notes, status,
                       source_legacy_entry_id, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
                (
                    str(uuid4()),
                    row["organization_id"],
                    _reference("LEGACY-RECEIPT", row),
                    row["entry_date"],
                    row["counterparty_name"] or "",
                    row["amount_minor"],
                    row["notes"],
                    row["id"],
                    now,
                    now,
                ),
            ).lastrowid
            if not receipt_id:
                receipt_id = conn.execute(
                    "SELECT id FROM receipts WHERE source_legacy_entry_id=?",
                    (row["id"],),
                ).fetchone()["id"]
            conn.execute(
                """INSERT OR IGNORE INTO receipt_allocations (
                       public_id, receipt_id, project_id,
                       allocated_amount_minor, notes, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid4()),
                    receipt_id,
                    row["project_id"],
                    row["amount_minor"],
                    row["notes"],
                    now,
                ),
            )


def create_cost_and_payment_facts(conn):
    _execute_script(
        conn,
        """
        CREATE TABLE IF NOT EXISTS cost_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            organization_id INTEGER NOT NULL,
            cost_no TEXT NOT NULL,
            project_id INTEGER,
            cost_date TEXT NOT NULL,
            category TEXT NOT NULL,
            amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
            counterparty_name_snapshot TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL DEFAULT 'manual',
            source_id TEXT,
            allocation_status TEXT NOT NULL DEFAULT 'assigned'
                CHECK(allocation_status IN ('assigned', 'unassigned')),
            notes TEXT,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'void')),
            source_legacy_entry_id INTEGER UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (organization_id) REFERENCES organizations(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (project_id) REFERENCES projects(id)
                ON DELETE RESTRICT,
            UNIQUE (organization_id, cost_no)
        );

        CREATE TABLE IF NOT EXISTS payment_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            organization_id INTEGER NOT NULL,
            payment_no TEXT NOT NULL,
            project_id INTEGER,
            contract_id INTEGER,
            payment_date TEXT NOT NULL,
            category TEXT NOT NULL,
            amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
            payee_name_snapshot TEXT NOT NULL DEFAULT '',
            payment_method TEXT NOT NULL DEFAULT '银行转账',
            source_type TEXT NOT NULL DEFAULT 'manual',
            source_id TEXT,
            notes TEXT,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'void')),
            source_legacy_entry_id INTEGER UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (organization_id) REFERENCES organizations(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (project_id) REFERENCES projects(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (contract_id) REFERENCES contracts(id)
                ON DELETE RESTRICT,
            UNIQUE (organization_id, payment_no)
        );

        CREATE INDEX IF NOT EXISTS idx_cost_entries_project
        ON cost_entries(project_id, cost_date, status);

        CREATE INDEX IF NOT EXISTS idx_payment_entries_project
        ON payment_entries(project_id, payment_date, status);
        """
    )

    now = _now()
    rows = conn.execute(
        """SELECT * FROM project_operating_entries
           WHERE status='active'
             AND entry_type IN ('other_cost', 'other_payment')
           ORDER BY id"""
    ).fetchall()
    for row in rows:
        if row["entry_type"] == "other_cost":
            conn.execute(
                """INSERT OR IGNORE INTO cost_entries (
                       public_id, organization_id, cost_no, project_id,
                       cost_date, category, amount_minor,
                       counterparty_name_snapshot, source_type, notes, status,
                       source_legacy_entry_id, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'legacy_manual', ?,
                             'active', ?, ?, ?)""",
                (
                    str(uuid4()),
                    row["organization_id"],
                    _reference("LEGACY-COST", row),
                    row["project_id"],
                    row["entry_date"],
                    row["category"] or "其他成本",
                    row["amount_minor"],
                    row["counterparty_name"] or "",
                    row["notes"],
                    row["id"],
                    now,
                    now,
                ),
            )
        else:
            conn.execute(
                """INSERT OR IGNORE INTO payment_entries (
                       public_id, organization_id, payment_no, project_id,
                       payment_date, category, amount_minor,
                       payee_name_snapshot, source_type, notes, status,
                       source_legacy_entry_id, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'legacy_manual', ?,
                             'active', ?, ?, ?)""",
                (
                    str(uuid4()),
                    row["organization_id"],
                    _reference("LEGACY-PAYMENT", row),
                    row["project_id"],
                    row["entry_date"],
                    row["category"] or "其他付款",
                    row["amount_minor"],
                    row["counterparty_name"] or "",
                    row["notes"],
                    row["id"],
                    now,
                    now,
                ),
            )

    conn.execute(
        """UPDATE project_operating_entries
           SET status='void', updated_at=?
           WHERE status='active'""",
        (now,),
    )


def extend_labor_facts(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(work_logs)")}
    additions = (
        ("public_id", "TEXT"),
        ("organization_id", "INTEGER REFERENCES organizations(id)"),
        ("project_id", "INTEGER REFERENCES projects(id)"),
        ("project_site_id", "INTEGER REFERENCES project_sites(id)"),
        ("daily_rate_minor", "INTEGER"),
        ("amount_minor", "INTEGER"),
        ("status", "TEXT NOT NULL DEFAULT 'active'"),
        ("updated_at", "TEXT"),
    )
    for name, definition in additions:
        if name not in columns:
            conn.execute(f"ALTER TABLE work_logs ADD COLUMN {name} {definition}")

    organization_id = conn.execute(
        "SELECT id FROM organizations WHERE organization_code='DEFAULT'"
    ).fetchone()[0]
    now = _now()
    rows = conn.execute(
        """SELECT id, daily_rate, amount, construction_site, public_id
           FROM work_logs ORDER BY id"""
    ).fetchall()

    aliases = {}

    def add_alias(project_id, value, site_id=None):
        key = (value or "").strip().casefold()
        if key:
            aliases.setdefault(key, set()).add((project_id, site_id))

    for row in conn.execute(
        "SELECT id, project_code, name FROM projects"
    ).fetchall():
        add_alias(row["id"], row["project_code"])
        add_alias(row["id"], row["name"])
    for row in conn.execute(
        "SELECT id, project_id, name FROM project_sites"
    ).fetchall():
        add_alias(row["project_id"], row["name"], row["id"])
    for row in conn.execute(
        """SELECT cs.site_name, cs.project_id, ps.id AS project_site_id
           FROM construction_sites cs
           LEFT JOIN project_sites ps
             ON ps.legacy_construction_site_id=cs.id"""
    ).fetchall():
        add_alias(row["project_id"], row["site_name"], row["project_site_id"])

    for row in rows:
        matches = aliases.get(
            (row["construction_site"] or "").strip().casefold(), set()
        )
        projects = {match[0] for match in matches}
        project_id = next(iter(projects)) if len(projects) == 1 else None
        site_ids = {match[1] for match in matches if match[1] is not None}
        site_id = next(iter(site_ids)) if len(site_ids) == 1 else None
        conn.execute(
            """UPDATE work_logs
               SET public_id=COALESCE(public_id, ?),
                   organization_id=COALESCE(organization_id, ?),
                   project_id=COALESCE(project_id, ?),
                   project_site_id=COALESCE(project_site_id, ?),
                   daily_rate_minor=COALESCE(
                       daily_rate_minor, CAST(ROUND(COALESCE(daily_rate, 0) * 100) AS INTEGER)
                   ),
                   amount_minor=COALESCE(
                       amount_minor, CAST(ROUND(COALESCE(amount, 0) * 100) AS INTEGER)
                   ),
                   updated_at=COALESCE(updated_at, ?)
               WHERE id=?""",
            (
                str(uuid4()),
                organization_id,
                project_id,
                site_id,
                now,
                row["id"],
            ),
        )

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_work_logs_public_id ON work_logs(public_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_work_logs_project ON work_logs(project_id, work_date)"
    )


def create_contract_links_and_business_attachments(conn):
    contract_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(contracts)")
    }
    if "parent_contract_id" not in contract_columns:
        conn.execute(
        """ALTER TABLE contracts ADD COLUMN parent_contract_id
           INTEGER REFERENCES contracts(id)"""
    )
    _execute_script(
        conn,
        """
        CREATE INDEX IF NOT EXISTS idx_contracts_parent
        ON contracts(parent_contract_id);

        CREATE TABLE IF NOT EXISTS business_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            organization_id INTEGER NOT NULL,
            contract_id INTEGER,
            settlement_id INTEGER,
            invoice_id INTEGER,
            receipt_id INTEGER,
            cost_entry_id INTEGER,
            payment_entry_id INTEGER,
            category TEXT NOT NULL DEFAULT '业务附件',
            file_path TEXT NOT NULL,
            original_name TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'void')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (organization_id) REFERENCES organizations(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (contract_id) REFERENCES contracts(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (settlement_id) REFERENCES settlements(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (invoice_id) REFERENCES sales_invoices(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (receipt_id) REFERENCES receipts(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (cost_entry_id) REFERENCES cost_entries(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (payment_entry_id) REFERENCES payment_entries(id)
                ON DELETE RESTRICT,
            CHECK (
                (contract_id IS NOT NULL)
              + (settlement_id IS NOT NULL)
              + (invoice_id IS NOT NULL)
              + (receipt_id IS NOT NULL)
              + (cost_entry_id IS NOT NULL)
              + (payment_entry_id IS NOT NULL)
              = 1
            )
        );

        CREATE INDEX IF NOT EXISTS idx_business_attachments_contract
        ON business_attachments(contract_id, status);
        CREATE INDEX IF NOT EXISTS idx_business_attachments_settlement
        ON business_attachments(settlement_id, status);
        CREATE INDEX IF NOT EXISTS idx_business_attachments_invoice
        ON business_attachments(invoice_id, status);
        CREATE INDEX IF NOT EXISTS idx_business_attachments_receipt
        ON business_attachments(receipt_id, status);
        CREATE INDEX IF NOT EXISTS idx_business_attachments_cost
        ON business_attachments(cost_entry_id, status);
        CREATE INDEX IF NOT EXISTS idx_business_attachments_payment
        ON business_attachments(payment_entry_id, status);
        """
    )


MIGRATIONS = [
    (160, "V4 合同、项目分配与结算事实", create_contract_and_settlement_facts),
    (170, "V4 销项发票、回款与项目核销事实", create_invoice_and_receipt_facts),
    (180, "V4 成本与付款事实台账", create_cost_and_payment_facts),
    (190, "V4 人工记录项目归属与金额快照", extend_labor_facts),
    (
        200,
        "V4 补充协议关联与经营事实附件",
        create_contract_links_and_business_attachments,
    ),
]
