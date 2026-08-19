import argparse
import os
import shutil
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
        shutil.copy2(args.database, test_database)
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
            assert {160, 170, 180, 190, 200, 240} <= versions
        finally:
            conn.close()
        second = run_migrations(test_database)
        assert second["applied"] == []
        assert second["backup"] is None

    print("V4 migration rehearsal and validation passed")


if __name__ == "__main__":
    main()
