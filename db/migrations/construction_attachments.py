"""Expose construction photos through the unified attachment ledger."""

from datetime import datetime
from uuid import uuid4


def migration_320(conn):
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(business_attachments)")
    }
    if "construction_record_id" not in columns:
        conn.execute(
            """ALTER TABLE business_attachments
               ADD COLUMN construction_record_id INTEGER
               REFERENCES construction_records(id)"""
        )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_business_attachments_construction
           ON business_attachments(construction_record_id, status)"""
    )
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    photos = conn.execute(
        """SELECT cp.*, cr.id AS construction_record_id,
                  COALESCE(p.organization_id, 1) AS organization_id
           FROM construction_photos cp
           JOIN construction_records cr ON cr.id=cp.record_id
           JOIN construction_sites cs ON cs.id=cr.site_id
           JOIN projects p ON p.id=cs.project_id"""
    ).fetchall()
    for photo in photos:
        exists = conn.execute(
            """SELECT 1 FROM business_attachments
               WHERE construction_record_id=? AND file_path=? AND status='active'""",
            (photo["construction_record_id"], photo["file_path"]),
        ).fetchone()
        if not exists:
            conn.execute(
                """INSERT INTO business_attachments (
                       public_id, organization_id, construction_record_id,
                       category, file_path, original_name, description,
                       status, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
                (
                    str(uuid4()), photo["organization_id"],
                    photo["construction_record_id"],
                    photo["photo_type"] or "施工照片", photo["file_path"],
                    photo["original_name"], photo["notes"] or "", now, now,
                ),
            )


MIGRATIONS = [(320, "施工照片纳入统一附件账", migration_320)]
