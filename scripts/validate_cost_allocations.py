import argparse
import sqlite3
from pathlib import Path


def validate(database):
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
        version = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()[0]
        mismatches = conn.execute(
            """SELECT ce.id, ce.cost_no, ce.amount_minor,
                      COALESCE(SUM(cal.amount_minor), 0) AS allocated_minor
               FROM cost_entries ce
               JOIN cost_allocation_lines cal
                 ON cal.cost_entry_id=ce.id AND cal.status='active'
               WHERE ce.status='active'
               GROUP BY ce.id
               HAVING allocated_minor <> ce.amount_minor"""
        ).fetchall()
        duplicate_projects = conn.execute(
            """SELECT cost_entry_id, project_id, COUNT(*) AS line_count
               FROM cost_allocation_lines
               WHERE status='active'
               GROUP BY cost_entry_id, project_id
               HAVING COUNT(*) > 1"""
        ).fetchall()
        active_sources = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount_minor), 0) FROM cost_entries WHERE status='active'"
        ).fetchone()
        active_allocations = conn.execute(
            """SELECT COUNT(*), COALESCE(SUM(cal.amount_minor), 0)
               FROM cost_allocation_lines cal
               JOIN cost_entries ce ON ce.id=cal.cost_entry_id
               WHERE cal.status='active' AND ce.status='active'"""
        ).fetchone()
        result = {
            "schema_version": version,
            "integrity": integrity,
            "foreign_key_violations": len(foreign_keys),
            "allocation_total_mismatches": len(mismatches),
            "duplicate_active_project_lines": len(duplicate_projects),
            "active_cost_documents": active_sources[0],
            "active_cost_document_minor": active_sources[1],
            "active_allocation_lines": active_allocations[0],
            "active_allocated_minor": active_allocations[1],
        }
        failed = (
            version < 240
            or integrity != "ok"
            or foreign_keys
            or mismatches
            or duplicate_projects
        )
        return result, failed
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Validate cost allocation integrity")
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    result, failed = validate(args.database)
    for key, value in result.items():
        print(f"{key}: {value}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
