from datetime import datetime
from uuid import uuid4


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _next_site_code(conn, project_id):
    number = conn.execute(
        "SELECT COALESCE(COUNT(*), 0) + 1 FROM project_sites WHERE project_id=?",
        (project_id,),
    ).fetchone()[0]
    while conn.execute(
        "SELECT 1 FROM project_sites WHERE project_id=? AND site_code=?",
        (project_id, f"SITE-{number:03d}"),
    ).fetchone():
        number += 1
    return f"SITE-{number:03d}"


def repair_automatic_project_site_mappings(conn):
    """Map construction sites created after the original V3 data migration."""
    now = _now()
    rows = conn.execute(
        """SELECT cs.id, cs.project_id, cs.site_name, cs.address, cs.is_active,
                  cs.created_at, p.project_code, p.name AS project_name
           FROM construction_sites cs
           JOIN projects p ON p.id=cs.project_id
           LEFT JOIN project_sites ps
             ON ps.legacy_construction_site_id=cs.id
           WHERE ps.id IS NULL
           ORDER BY cs.id"""
    ).fetchall()
    for row in rows:
        prefixed_project_name = f"{row['project_code']} · {row['project_name']}"
        display_name = (
            row["project_name"]
            if row["site_name"] in (row["project_name"], prefixed_project_name)
            else row["site_name"]
        )
        conn.execute(
            """INSERT INTO project_sites
               (public_id, project_id, site_code, name, address, is_active,
                legacy_construction_site_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid4()),
                row["project_id"],
                _next_site_code(conn, row["project_id"]),
                display_name,
                row["address"] or "",
                row["is_active"],
                row["id"],
                row["created_at"] or now,
                now,
            ),
        )


MIGRATIONS = [
    (150, "V3 自动施工地点与项目地点完整性修复", repair_automatic_project_site_mappings),
]
