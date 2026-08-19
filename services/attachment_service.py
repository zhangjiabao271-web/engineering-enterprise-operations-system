from services._common import now as _now, organization_id as _organization_id
import os
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from db.connection import PROJECT_ROOT, get_connection


ENTITY_COLUMNS = {
    "contract": ("contract_id", "contracts"),
    "settlement": ("settlement_id", "settlements"),
    "invoice": ("invoice_id", "sales_invoices"),
    "receipt": ("receipt_id", "receipts"),
    "cost": ("cost_entry_id", "cost_entries"),
    "construction": ("construction_record_id", "construction_records"),
}


def _storage_root():
    configured = os.environ.get("SUPPLY_CHAIN_ATTACHMENTS_PATH")
    return (
        Path(configured)
        if configured
        else PROJECT_ROOT / "attachments" / "business"
    )


def _entity(entity_type):
    if entity_type not in ENTITY_COLUMNS:
        raise ValueError("附件业务类型无效")
    return ENTITY_COLUMNS[entity_type]


def add_attachment(
    entity_type, entity_id, source_path, category="业务附件", description=""
):
    column, table = _entity(entity_type)
    source = Path(source_path)
    if not source.is_file():
        raise ValueError("所选附件文件不存在")
    storage = _storage_root()
    storage.mkdir(parents=True, exist_ok=True)
    target = storage / f"{uuid4().hex}{source.suffix.lower()}"
    shutil.copy2(source, target)

    conn = get_connection()
    try:
        if not conn.execute(
            f"SELECT 1 FROM {table} WHERE id=?", (entity_id,)
        ).fetchone():
            raise ValueError("附件对应的业务记录不存在")
        now = _now()
        cursor = conn.execute(
            f"""INSERT INTO business_attachments (
                    public_id, organization_id, {column}, category,
                    file_path, original_name, description, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                str(uuid4()),
                _organization_id(conn),
                entity_id,
                (category or "业务附件").strip(),
                str(target.relative_to(PROJECT_ROOT))
                if target.is_relative_to(PROJECT_ROOT)
                else str(target),
                source.name,
                (description or "").strip(),
                now,
                now,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    except Exception:
        conn.rollback()
        try:
            target.unlink()
        except OSError:
            pass
        raise
    finally:
        conn.close()


def list_attachments(entity_type, entity_id, include_void=False):
    column, _table = _entity(entity_type)
    conn = get_connection()
    try:
        sql = f"SELECT * FROM business_attachments WHERE {column}=?"
        params = [entity_id]
        if not include_void:
            sql += " AND status='active'"
        sql += " ORDER BY created_at DESC, id DESC"
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        for row in rows:
            path = Path(row["file_path"])
            row["absolute_path"] = str(
                path if path.is_absolute() else PROJECT_ROOT / path
            )
        return rows
    finally:
        conn.close()


def void_attachments(attachment_ids):
    if not attachment_ids:
        return
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(attachment_ids))
        conn.execute(
            f"""UPDATE business_attachments
                SET status='void', updated_at=?
                WHERE id IN ({placeholders}) AND status='active'""",
            (_now(), *attachment_ids),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
