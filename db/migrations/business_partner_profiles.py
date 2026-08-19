"""Complete unified customer/supplier master-data profiles."""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def migration_330(conn):
    partner_columns = _columns(conn, "business_partners")
    additions = (
        ("registered_address", "TEXT"),
        ("business_address", "TEXT"),
        ("invoice_phone", "TEXT"),
        ("bank_name", "TEXT"),
        ("bank_account", "TEXT"),
    )
    for name, definition in additions:
        if name not in partner_columns:
            conn.execute(
                f"ALTER TABLE business_partners ADD COLUMN {name} {definition}"
            )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_profiles (
            partner_id INTEGER PRIMARY KEY,
            customer_category TEXT,
            settlement_terms TEXT,
            credit_limit_minor INTEGER NOT NULL DEFAULT 0
                CHECK(credit_limit_minor >= 0),
            updated_at TEXT NOT NULL,
            FOREIGN KEY (partner_id) REFERENCES business_partners(id)
                ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_partner_roles_partner_role
            ON partner_roles(partner_id, role_code)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_partner_contacts_primary
            ON partner_contacts(partner_id, is_primary, is_active)
        """
    )
    conn.execute(
        """INSERT INTO customer_profiles(partner_id, updated_at)
           SELECT pr.partner_id, COALESCE(bp.updated_at, bp.created_at)
           FROM partner_roles pr
           JOIN business_partners bp ON bp.id=pr.partner_id
           LEFT JOIN customer_profiles cp ON cp.partner_id=pr.partner_id
           WHERE pr.role_code='customer' AND cp.partner_id IS NULL"""
    )


MIGRATIONS = [(330, "统一客商客户档案、开票资料与双角色维护", migration_330)]
