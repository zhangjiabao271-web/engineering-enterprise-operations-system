"""Service boundary for construction and acceptance workflows.

V4.4: the construction storage implementation lives here, reading/writing
through db.connection directly.  The page depends only on this service;
database.py keeps a thin delegation for legacy callers.
"""

from datetime import datetime
from uuid import uuid4

from db.connection import get_connection
from services import project_service


def get_projects(active_only=False):
    return project_service.list_projects(active_only=active_only)


def _construction_month_bounds(month):
    year, month_number = map(int, month.split("-"))
    start = f"{year:04d}-{month_number:02d}-01"
    if month_number == 12:
        end = f"{year + 1:04d}-01-01"
    else:
        end = f"{year:04d}-{month_number + 1:02d}-01"
    return start, end


def _now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_construction_sites(active_only=True):
    conn = get_connection()
    try:
        sql = """
            SELECT cs.*, p.name AS project_name, p.project_code
            FROM construction_sites cs
            JOIN projects p ON cs.project_id=p.id
        """
        if active_only:
            sql += " WHERE cs.is_active=1"
        sql += " ORDER BY CASE cs.site_name WHEN '澄湖药业' THEN 1 WHEN '屹峰药业' THEN 2 WHEN '朗润' THEN 3 ELSE 4 END, cs.site_name"
        rows = conn.execute(sql).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_construction_work_areas(project_id=None):
    conn = get_connection()
    try:
        sql = """
            SELECT DISTINCT TRIM(cr.work_area) AS work_area
            FROM construction_records cr
            JOIN construction_sites cs ON cs.id=cr.site_id
            WHERE cr.record_status='有效' AND TRIM(COALESCE(cr.work_area, ''))<>''
        """
        params = []
        if project_id:
            sql += " AND cs.project_id=?"
            params.append(project_id)
        sql += " ORDER BY work_area"
        rows = conn.execute(sql, params).fetchall()
        return [row["work_area"] for row in rows]
    finally:
        conn.close()


def _ensure_v3_project_site(conn, construction_site):
    exists = conn.execute(
        "SELECT 1 FROM project_sites WHERE legacy_construction_site_id=?",
        (construction_site["id"],),
    ).fetchone()
    if exists:
        return
    number = conn.execute(
        "SELECT COALESCE(COUNT(*), 0) + 1 FROM project_sites WHERE project_id=?",
        (construction_site["project_id"],),
    ).fetchone()[0]
    while conn.execute(
        "SELECT 1 FROM project_sites WHERE project_id=? AND site_code=?",
        (construction_site["project_id"], f"SITE-{number:03d}"),
    ).fetchone():
        number += 1
    prefixed_name = (
        f"{construction_site['project_code']} · "
        f"{construction_site['project_name']}"
    )
    display_name = (
        construction_site["project_name"]
        if construction_site["site_name"] in (
            construction_site["project_name"],
            prefixed_name,
        )
        else construction_site["site_name"]
    )
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO project_sites
           (public_id, project_id, site_code, name, address, is_active,
            legacy_construction_site_id, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid4()),
            construction_site["project_id"],
            f"SITE-{number:03d}",
            display_name,
            construction_site["address"] or "",
            construction_site["is_active"],
            construction_site["id"],
            construction_site["created_at"] or now,
            now,
        ),
    )


def _construction_site_for_project(conn, project_id):
    row = conn.execute("""
        SELECT cs.id, cs.project_id, cs.site_name, cs.address, cs.is_active, cs.created_at,
               p.project_code, p.name AS project_name
        FROM construction_sites cs
        JOIN projects p ON p.id=cs.project_id
        WHERE cs.project_id=?
        ORDER BY CASE WHEN cs.site_name=p.name THEN 0 ELSE 1 END,
                 cs.is_active DESC, cs.id
        LIMIT 1
    """, (project_id,)).fetchone()
    if row:
        _ensure_v3_project_site(conn, row)
        return row["id"]

    project = conn.execute(
        "SELECT project_code, name FROM projects WHERE id=?", (project_id,)
    ).fetchone()
    if not project:
        raise ValueError("所选项目不存在")
    site_name = project["name"]
    conflict = conn.execute(
        "SELECT 1 FROM construction_sites WHERE site_name=?", (site_name,)
    ).fetchone()
    if conflict:
        site_name = f"{project['project_code']} · {project['name']}"
    legacy_id = conn.execute("""
        INSERT INTO construction_sites (
            project_id, site_name, is_active, notes, created_at
        ) VALUES (?, ?, 1, '施工记录自动建立的项目关联', ?)
    """, (
        project_id, site_name, _now_text()
    )).lastrowid
    row = conn.execute(
        """SELECT cs.id, cs.project_id, cs.site_name, cs.address, cs.is_active, cs.created_at,
                  p.project_code, p.name AS project_name
           FROM construction_sites cs
           JOIN projects p ON p.id=cs.project_id
           WHERE cs.id=?""",
        (legacy_id,),
    ).fetchone()
    _ensure_v3_project_site(conn, row)
    return legacy_id


def _construction_details_summary(work_details):
    """Keep a short legacy summary while the full record lives in work_details."""
    first_line = next(
        (line.strip() for line in (work_details or "").splitlines() if line.strip()),
        "综合安装明细",
    )
    return first_line[:120]


def _legacy_construction_details(data):
    """Build readable details for older callers that still send one work item."""
    lines = []
    work_item = (data.get("work_item") or "").strip()
    if work_item:
        lines.append(work_item)
    quantity = data.get("quantity")
    unit = (data.get("unit") or "").strip()
    if quantity not in (None, "") and unit:
        lines.append(f"工程量：{quantity} {unit}")
    description = (data.get("description") or "").strip()
    if description:
        lines.append(description)
    return "\n".join(lines)


def add_construction_record(data):
    conn = get_connection()
    try:
        now = _now_text()
        site_id = data.get("site_id")
        if data.get("project_id"):
            site_id = _construction_site_for_project(conn, data["project_id"])
        work_details = (data.get("work_details") or "").strip()
        if not work_details:
            work_details = _legacy_construction_details(data)
        work_item = (data.get("work_item") or _construction_details_summary(work_details))
        cursor = conn.execute("""
            INSERT INTO construction_records (
                site_id, record_date, work_area, work_item, quantity, unit,
                team_name, description, inspection_status, record_status,
                start_date, end_date, work_amount_cents, work_details,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '待验收', '有效', ?, ?, ?, ?, ?, ?)
        """, (
            site_id, data["end_date"], data.get("work_area", ""),
            work_item, data.get("quantity", 0), data.get("unit", "批"),
            data.get("team_name", ""), data.get("description", ""),
            data["start_date"], data["end_date"], data.get("work_amount_cents", 0),
            work_details, now, now
        ))
        conn.commit()
        return cursor.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_construction_record(record_id, data):
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT site_id FROM construction_records WHERE id=?", (record_id,)
        ).fetchone()
        if not existing:
            raise ValueError("施工记录不存在")
        site_id = data.get("site_id") or existing["site_id"]
        if data.get("project_id"):
            current_project = conn.execute(
                "SELECT project_id FROM construction_sites WHERE id=?",
                (existing["site_id"],),
            ).fetchone()
            if not current_project or current_project["project_id"] != data["project_id"]:
                site_id = _construction_site_for_project(conn, data["project_id"])
        work_details = (data.get("work_details") or "").strip()
        if not work_details:
            work_details = _legacy_construction_details(data)
        work_item = (data.get("work_item") or _construction_details_summary(work_details))
        conn.execute("""
            UPDATE construction_records
            SET site_id=?, record_date=?, work_area=?, work_item=?, quantity=?,
                unit=?, team_name=?, description=?, start_date=?, end_date=?,
                work_amount_cents=?, work_details=?, updated_at=?
            WHERE id=? AND record_status='有效'
        """, (
            site_id, data["end_date"], data.get("work_area", ""),
            work_item, data.get("quantity", 0), data.get("unit", "批"),
            data.get("team_name", ""), data.get("description", ""),
            data["start_date"], data["end_date"], data.get("work_amount_cents", 0),
            work_details, _now_text(), record_id
        ))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_construction_record(record_id):
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT cr.*, cs.site_name, p.id AS project_id, p.name AS project_name,
                   (SELECT COUNT(*) FROM construction_photos cp WHERE cp.record_id=cr.id) AS photo_count
            FROM construction_records cr
            JOIN construction_sites cs ON cr.site_id=cs.id
            JOIN projects p ON cs.project_id=p.id
            WHERE cr.id=?
        """, (record_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_construction_records(month="", project_id=None, inspection_status="", keyword=""):
    conn = get_connection()
    try:
        sql = """
            SELECT cr.*, cs.site_name, p.id AS project_id, p.name AS project_name,
                   (SELECT COUNT(*) FROM construction_photos cp WHERE cp.record_id=cr.id) AS photo_count
            FROM construction_records cr
            JOIN construction_sites cs ON cr.site_id=cs.id
            JOIN projects p ON cs.project_id=p.id
            WHERE cr.record_status='有效'
        """
        params = []
        if month:
            month_start, next_month = _construction_month_bounds(month)
            sql += """
                AND COALESCE(cr.start_date, cr.record_date) < ?
                AND COALESCE(cr.end_date, cr.record_date) >= ?
            """
            params.extend([next_month, month_start])
        if project_id:
            sql += " AND cs.project_id=?"
            params.append(project_id)
        if inspection_status:
            sql += " AND cr.inspection_status=?"
            params.append(inspection_status)
        if keyword:
            sql += """
                AND (cr.work_area LIKE ? OR cr.work_details LIKE ?
                     OR cr.work_item LIKE ? OR cr.team_name LIKE ?
                     OR cr.description LIKE ? OR cr.inspection_notes LIKE ?
                     OR p.name LIKE ?)
            """
            params.extend([f"%{keyword}%"] * 7)
        sql += " ORDER BY COALESCE(cr.end_date, cr.record_date) DESC, cr.id DESC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_construction_inspection(record_id, data):
    conn = get_connection()
    try:
        conn.execute("""
            UPDATE construction_records
            SET inspection_status=?, inspector=?, inspection_date=?,
                inspection_notes=?, updated_at=?
            WHERE id=? AND record_status='有效'
        """, (
            data["inspection_status"], data.get("inspector", ""),
            data.get("inspection_date") or None, data.get("inspection_notes", ""),
            _now_text(), record_id
        ))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def void_construction_records(record_ids):
    if not record_ids:
        return
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(record_ids))
        conn.execute(
            f"UPDATE construction_records SET record_status='作废', updated_at=? WHERE id IN ({placeholders})",
            (_now_text(), *record_ids)
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def add_construction_photo(record_id, file_path, original_name, photo_type="施工现场", notes=""):
    conn = get_connection()
    try:
        cursor = conn.execute("""
            INSERT INTO construction_photos (
                record_id, photo_type, file_path, original_name, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            record_id, photo_type, file_path, original_name, notes, _now_text()
        ))
        record = conn.execute(
            """SELECT COALESCE(p.organization_id, 1) AS organization_id
               FROM construction_records cr
               JOIN construction_sites cs ON cs.id=cr.site_id
               JOIN projects p ON p.id=cs.project_id WHERE cr.id=?""",
            (record_id,),
        ).fetchone()
        if not record:
            raise ValueError("施工记录不存在")
        now = _now_text()
        conn.execute(
            """INSERT INTO business_attachments (
                   public_id, organization_id, construction_record_id,
                   category, file_path, original_name, description,
                   status, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                str(uuid4()), record["organization_id"], record_id,
                photo_type, file_path, original_name, notes, now, now,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_construction_photos(record_id):
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM construction_photos WHERE record_id=? ORDER BY id
        """, (record_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def delete_construction_photo(photo_id):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM construction_photos WHERE id=?", (photo_id,)).fetchone()
        conn.execute("DELETE FROM construction_photos WHERE id=?", (photo_id,))
        if row:
            conn.execute(
                """UPDATE business_attachments
                   SET status='void', updated_at=?
                   WHERE construction_record_id=? AND file_path=? AND status='active'""",
                (_now_text(), row["record_id"], row["file_path"]),
            )
        conn.commit()
        return dict(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_construction_dashboard(month, project_id=None):
    conn = get_connection()
    try:
        month_start, next_month = _construction_month_bounds(month)
        where = """
            cr.record_status='有效'
            AND COALESCE(cr.start_date, cr.record_date) < ?
            AND COALESCE(cr.end_date, cr.record_date) >= ?
        """
        params = [next_month, month_start]
        if project_id:
            where += " AND cs.project_id=?"
            params.append(project_id)
        summary = conn.execute(f"""
            SELECT COUNT(*) AS record_count,
                   SUM(CASE WHEN cr.inspection_status='待验收' THEN 1 ELSE 0 END) AS pending_count,
                   SUM(CASE WHEN cr.inspection_status='已验收' THEN 1 ELSE 0 END) AS accepted_count,
                   SUM(CASE WHEN cr.inspection_status='需整改' THEN 1 ELSE 0 END) AS rectification_count,
                   COALESCE(SUM(cr.work_amount_cents), 0) AS total_amount_cents,
                   COALESCE(SUM(CASE WHEN cr.inspection_status='已验收'
                                     THEN cr.work_amount_cents ELSE 0 END), 0)
                       AS accepted_amount_cents,
                   COALESCE(SUM((SELECT COUNT(*) FROM construction_photos cp WHERE cp.record_id=cr.id)), 0) AS photo_count
            FROM construction_records cr
            JOIN construction_sites cs ON cr.site_id=cs.id
            WHERE {where}
        """, params).fetchone()
        by_site = conn.execute(f"""
            SELECT p.name AS label, COUNT(*) AS record_count,
                   SUM(CASE WHEN cr.inspection_status='待验收' THEN 1 ELSE 0 END) AS pending_count,
                   COALESCE(SUM(cr.work_amount_cents), 0) AS amount_cents
            FROM construction_records cr
            JOIN construction_sites cs ON cr.site_id=cs.id
            JOIN projects p ON p.id=cs.project_id
            WHERE {where}
            GROUP BY p.id, p.name ORDER BY amount_cents DESC, record_count DESC
        """, params).fetchall()
        by_area = conn.execute(f"""
            SELECT p.name AS project_name, cr.work_area AS label,
                   COUNT(*) AS record_count,
                   COALESCE(SUM(cr.work_amount_cents), 0) AS amount_cents
            FROM construction_records cr
            JOIN construction_sites cs ON cr.site_id=cs.id
            JOIN projects p ON p.id=cs.project_id
            WHERE {where}
            GROUP BY p.id, p.name, cr.work_area
            ORDER BY amount_cents DESC, record_count DESC LIMIT 10
        """, params).fetchall()
        return {
            "summary": dict(summary),
            "by_site": [dict(row) for row in by_site],
            "by_area": [dict(row) for row in by_area],
        }
    finally:
        conn.close()
