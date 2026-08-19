"""Backfill work_logs project attribution from exact, unambiguous aliases.

Only exact case-insensitive matches against project codes, project names,
active project site names and active legacy construction site names are
applied. Ambiguous or unknown site text stays unassigned (待归集) — the
migration never guesses, matching the V4 principle that fuzzy mappings go
to the unassigned queue instead of being forced onto a project.
"""

from datetime import datetime


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _aliases(conn):
    aliases = {}

    def add(project_id, site_id, value):
        key = (value or "").strip().casefold()
        if key:
            aliases.setdefault(key, set()).add((project_id, site_id))

    for row in conn.execute("SELECT id, project_code, name FROM projects"):
        add(row["id"], None, row["project_code"])
        add(row["id"], None, row["name"])
    for row in conn.execute(
        "SELECT project_id, id, name FROM project_sites WHERE is_active=1"
    ):
        add(row["project_id"], row["id"], row["name"])
    for row in conn.execute(
        """SELECT cs.project_id, ps.id AS site_id, cs.site_name
           FROM construction_sites cs
           LEFT JOIN project_sites ps
             ON ps.legacy_construction_site_id=cs.id
           WHERE cs.is_active=1"""
    ):
        add(row["project_id"], row["site_id"], row["site_name"])
    return aliases


def backfill_work_log_project_attribution(conn):
    aliases = _aliases(conn)
    rows = conn.execute(
        "SELECT id, construction_site FROM work_logs WHERE project_id IS NULL"
    ).fetchall()
    now = _now()
    updated = 0
    for row in rows:
        key = (row["construction_site"] or "").strip().casefold()
        matches = aliases.get(key, set()) if key else set()
        project_ids = {item[0] for item in matches}
        if len(project_ids) != 1:
            continue
        project_id = next(iter(project_ids))
        site_ids = {
            item[1] for item in matches
            if item[1] is not None and item[0] == project_id
        }
        site_id = next(iter(site_ids)) if len(site_ids) == 1 else None
        conn.execute(
            """UPDATE work_logs
               SET project_id=?,
                   project_site_id=COALESCE(project_site_id, ?),
                   updated_at=?
               WHERE id=? AND project_id IS NULL""",
            (project_id, site_id, now, row["id"]),
        )
        updated += 1
    return updated


MIGRATIONS = [
    (
        270,
        "工天记录项目归属精确匹配回填（歧义保持待归集）",
        backfill_work_log_project_attribution,
    ),
]
