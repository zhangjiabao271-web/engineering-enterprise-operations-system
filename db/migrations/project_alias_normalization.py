from datetime import datetime


ALIASES = {
    "澄湖": "澄湖药业",
    "屹峰": "屹峰药业",
}


def _target_project(conn, name):
    rows = conn.execute(
        "SELECT id FROM projects WHERE name=? ORDER BY id", (name,)
    ).fetchall()
    if len(rows) != 1:
        raise RuntimeError(f"项目“{name}”必须唯一存在")
    return rows[0]["id"]


def normalize_project_aliases(conn):
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    for alias, canonical in ALIASES.items():
        project_id = _target_project(conn, canonical)
        duplicate_project = conn.execute(
            "SELECT id FROM projects WHERE name=?", (alias,)
        ).fetchone()
        if duplicate_project:
            raise RuntimeError(
                f"存在独立项目“{alias}”，不允许自动强制合并"
            )

        legacy_site = conn.execute(
            """SELECT id FROM construction_sites
               WHERE project_id=? AND site_name=?""",
            (project_id, alias),
        ).fetchone()
        canonical_site = conn.execute(
            """SELECT id FROM construction_sites
               WHERE project_id=? AND site_name=?""",
            (project_id, canonical),
        ).fetchone()
        if legacy_site and canonical_site:
            raise RuntimeError(
                f"项目“{canonical}”同时存在旧施工地和新施工地，需人工确认"
            )
        if legacy_site:
            conn.execute(
                """UPDATE construction_sites SET site_name=?
                   WHERE id=?""",
                (canonical, legacy_site["id"]),
            )

        project_site = conn.execute(
            """SELECT ps.id
               FROM project_sites ps
               LEFT JOIN construction_sites cs
                 ON cs.id=ps.legacy_construction_site_id
               WHERE ps.project_id=?
                 AND (ps.name=? OR cs.site_name=?)
               ORDER BY CASE WHEN ps.name=? THEN 0 ELSE 1 END, ps.id
               LIMIT 1""",
            (project_id, alias, canonical, canonical),
        ).fetchone()
        if project_site:
            conn.execute(
                """UPDATE project_sites SET name=?, updated_at=?
                   WHERE id=?""",
                (canonical, now, project_site["id"]),
            )

        conn.execute(
            """UPDATE work_logs
               SET construction_site=?, project_id=?, project_site_id=?,
                   updated_at=?
               WHERE TRIM(construction_site) IN (?, ?)""",
            (
                canonical,
                project_id,
                project_site["id"] if project_site else None,
                now,
                alias,
                canonical,
            ),
        )
        conn.execute(
            "UPDATE purchases SET construction_site=? WHERE TRIM(construction_site)=?",
            (canonical, alias),
        )
        conn.execute(
            "UPDATE purchase_order_items SET purpose=? WHERE TRIM(purpose)=?",
            (canonical, alias),
        )


MIGRATIONS = [
    (210, "澄湖与屹峰项目及施工地别名归一", normalize_project_aliases),
]
