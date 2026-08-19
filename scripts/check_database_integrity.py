import sqlite3
from pathlib import Path


def main():
    database_path = Path(__file__).resolve().parent.parent / "supplier_data.db"
    conn = sqlite3.connect(database_path)
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
    version = conn.execute(
        "SELECT MAX(version) FROM schema_migrations"
    ).fetchone()[0]
    customer_profiles = conn.execute(
        "SELECT COUNT(*) FROM customer_profiles"
    ).fetchone()[0] if version >= 330 else 0
    partner_gaps = {
        "partners_without_roles": conn.execute(
            """SELECT COUNT(*) FROM business_partners bp
               WHERE NOT EXISTS (
                   SELECT 1 FROM partner_roles pr WHERE pr.partner_id=bp.id
               )"""
        ).fetchone()[0],
        "customers_without_profiles": conn.execute(
            """SELECT COUNT(*) FROM partner_roles pr
               LEFT JOIN customer_profiles cp ON cp.partner_id=pr.partner_id
               WHERE pr.role_code='customer' AND cp.partner_id IS NULL"""
        ).fetchone()[0] if version >= 330 else None,
    }
    tables = {
        row[0]
        for row in conn.execute(
            """SELECT name FROM sqlite_master
               WHERE name IN ('ai_conversations', 'ai_messages')"""
        )
    }
    conn.close()
    print(
        {
            "integrity": integrity,
            "foreign_key_violations": len(foreign_keys),
            "schema_version": version,
            "ai_tables": sorted(tables),
            "customer_profiles": customer_profiles,
            "partner_gaps": partner_gaps,
        }
    )
    if integrity != "ok" or foreign_keys:
        raise SystemExit(1)
    if version < 330 or tables != {"ai_conversations", "ai_messages"}:
        raise SystemExit(2)
    if any(value for value in partner_gaps.values() if value is not None):
        raise SystemExit(3)


if __name__ == "__main__":
    main()
