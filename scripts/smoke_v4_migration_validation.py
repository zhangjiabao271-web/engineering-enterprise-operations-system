import argparse
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Rehearse and validate the complete V4 migration"
    )
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="v4_migration_validation_") as temp_dir:
        test_database = Path(temp_dir) / "supplier_data.db"
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        from db.backup import backup_database
        backup_database(args.database, test_database)
        fact_tables = (
            "projects", "contracts", "contract_project_allocations",
            "settlements", "sales_invoices", "receipts",
            "receipt_allocations", "purchase_orders", "work_logs",
            "cost_entries",
        )
        before_conn = sqlite3.connect(test_database)
        try:
            before_counts = {
                table: before_conn.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]
                for table in fact_tables
            }
        finally:
            before_conn.close()

        import database
        from db.connection import get_connection
        from db.migration_runner import run_migrations
        from scripts.validate_v3_database import validate

        database.init_db()
        checks, failed = validate(test_database)
        assert not failed, checks
        conn = get_connection()
        try:
            versions = {
                row[0] for row in conn.execute(
                    "SELECT version FROM schema_migrations"
                ).fetchall()
            }
            assert {
                160, 170, 180, 190, 200, 240, 330, 340, 350, 360,
                370, 380, 390, 400, 410, 420, 430,
                440, 450, 460,
            } <= versions
            collection_tables = {
                row[0]
                for row in conn.execute(
                    """SELECT name FROM sqlite_master
                       WHERE type='table' AND name IN (
                           'collection_cases', 'collection_followup_logs'
                       )"""
                ).fetchall()
            }
            assert collection_tables == {
                "collection_cases", "collection_followup_logs"
            }
            after_counts = {
                table: conn.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]
                for table in fact_tables
            }
            assert after_counts == before_counts, {
                "before": before_counts,
                "after": after_counts,
            }
        finally:
            conn.close()
        second = run_migrations(test_database)
        assert second["applied"] == []
        assert second["backup"] is None

    print("V4 migration rehearsal and validation passed")


if __name__ == "__main__":
    main()
