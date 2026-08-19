import argparse
import sqlite3
import sys
from pathlib import Path


ALIASES = ("澄湖", "澄湖药业", "屹峰", "屹峰药业")


def quote_identifier(value):
    return '"' + value.replace('"', '""') + '"'


def main():
    parser = argparse.ArgumentParser(
        description="Audit project and site aliases without changing data"
    )
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    conn = sqlite3.connect(args.database)
    conn.row_factory = sqlite3.Row
    try:
        print("PROJECTS")
        for row in conn.execute(
            "SELECT id, project_code, name, status FROM projects ORDER BY id"
        ):
            if "澄湖" in row["name"] or "屹峰" in row["name"]:
                print(dict(row))

        print("CONSTRUCTION_SITES")
        for row in conn.execute(
            """SELECT cs.id, cs.site_name, cs.project_id, p.name project_name
               FROM construction_sites cs
               JOIN projects p ON p.id=cs.project_id
               WHERE cs.site_name LIKE '%澄湖%' OR cs.site_name LIKE '%屹峰%'
               ORDER BY cs.id"""
        ):
            print(dict(row))

        print("WORK_LOGS")
        for row in conn.execute(
            """SELECT wl.construction_site, wl.project_id,
                      COALESCE(p.name, '未归属') project_name,
                      COUNT(*) record_count,
                      COALESCE(SUM(wl.amount_minor), 0) amount_minor
               FROM work_logs wl
               LEFT JOIN projects p ON p.id=wl.project_id
               WHERE wl.construction_site IN ('澄湖', '澄湖药业', '屹峰', '屹峰药业')
               GROUP BY wl.construction_site, wl.project_id, p.name
               ORDER BY wl.construction_site, wl.project_id"""
        ):
            print(dict(row))

        print("EXACT_TEXT_MATCHES")
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            if not row[0].startswith("sqlite_")
        ]
        for table in tables:
            columns = conn.execute(
                f"PRAGMA table_info({quote_identifier(table)})"
            ).fetchall()
            for column in columns:
                if "TEXT" not in (column[2] or "").upper():
                    continue
                column_name = column[1]
                placeholders = ",".join("?" for _ in ALIASES)
                count = conn.execute(
                    f"""SELECT COUNT(*) FROM {quote_identifier(table)}
                        WHERE {quote_identifier(column_name)} IN ({placeholders})""",
                    ALIASES,
                ).fetchone()[0]
                if count:
                    values = conn.execute(
                        f"""SELECT {quote_identifier(column_name)}, COUNT(*)
                            FROM {quote_identifier(table)}
                            WHERE {quote_identifier(column_name)} IN ({placeholders})
                            GROUP BY {quote_identifier(column_name)}
                            ORDER BY {quote_identifier(column_name)}""",
                        ALIASES,
                    ).fetchall()
                    detail = ", ".join(
                        f"{value[0]}={value[1]}" for value in values
                    )
                    print(f"{table}.{column_name}: {detail}")

        print("NON_CANONICAL_CONTAINING_MATCHES")
        for table in tables:
            columns = conn.execute(
                f"PRAGMA table_info({quote_identifier(table)})"
            ).fetchall()
            for column in columns:
                if "TEXT" not in (column[2] or "").upper():
                    continue
                column_name = column[1]
                rows = conn.execute(
                    f"""SELECT {quote_identifier(column_name)}, COUNT(*)
                        FROM {quote_identifier(table)}
                        WHERE (
                            {quote_identifier(column_name)} LIKE '%澄湖%'
                            AND {quote_identifier(column_name)} NOT LIKE '%澄湖药业%'
                            AND {quote_identifier(column_name)} NOT LIKE '%澄湖环保站%'
                        ) OR (
                            {quote_identifier(column_name)} LIKE '%屹峰%'
                            AND {quote_identifier(column_name)} NOT LIKE '%屹峰药业%'
                        )
                        GROUP BY {quote_identifier(column_name)}
                        ORDER BY COUNT(*) DESC
                        LIMIT 10"""
                ).fetchall()
                if rows:
                    detail = ", ".join(
                        f"{value[0]}={value[1]}" for value in rows
                    )
                    print(f"{table}.{column_name}: {detail}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
