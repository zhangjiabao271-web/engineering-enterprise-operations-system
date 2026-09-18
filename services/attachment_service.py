from services._common import now as _now, organization_id as _organization_id
import os
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from db.connection import PROJECT_ROOT, db_read, db_transaction


ENTITY_COLUMNS = {
    "purchase": ("purchase_order_id", "purchase_orders"),
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


def attachment_path(file_path):
    path = Path(file_path)
    return path if path.is_absolute() else PROJECT_ROOT / path


def invoice_attachment_statuses(conn, invoice_ids):
    result = {invoice_id: {"attachment_count": 0, "missing_attachment_count": 0} for invoice_id in invoice_ids}
    # One scan instead of opening a database connection for each invoice.
    for row in conn.execute("SELECT invoice_id, file_path FROM business_attachments WHERE invoice_id IS NOT NULL AND status='active'"):
        if row["invoice_id"] in result:
            values = result[row["invoice_id"]]
            values["attachment_count"] += 1
            if not attachment_path(row["file_path"]).is_file():
                values["missing_attachment_count"] += 1
    for values in result.values():
        count, missing = values["attachment_count"], values["missing_attachment_count"]
        if missing:
            values["attachment_status"] = f"文件缺失 {missing} 个"
        elif count:
            values["attachment_status"] = f"已上传 {count} 个"
        else:
            values["attachment_status"] = "未上传"
        values["attachment_needs_attention"] = not count or bool(missing)
    return result


def purchase_attachment_statuses(order_ids):
    """Read attachment availability for a list without opening one connection per row."""
    result = {int(order_id): "未上传" for order_id in order_ids}
    if not result:
        return result
    counts = {}
    with db_read() as conn:
        for row in conn.execute(
            "SELECT purchase_order_id, file_path FROM business_attachments "
            "WHERE purchase_order_id IS NOT NULL AND status='active'"
        ):
            order_id = row["purchase_order_id"]
            if order_id not in result:
                continue
            count, missing = counts.get(order_id, (0, 0))
            counts[order_id] = (count + 1, missing + (not attachment_path(row["file_path"]).is_file()))
    for order_id, (count, missing) in counts.items():
        result[order_id] = f"文件缺失 {missing} 个" if missing else f"已上传 {count} 个"
    return result


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

    try:
        with db_transaction() as conn:
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
            return cursor.lastrowid
    except Exception:
        try:
            target.unlink()
        except OSError:
            pass
        raise


def list_attachments(entity_type, entity_id, include_void=False):
    column, _table = _entity(entity_type)
    with db_read() as conn:
        sql = f"SELECT * FROM business_attachments WHERE {column}=?"
        params = [entity_id]
        if not include_void:
            sql += " AND status='active'"
        sql += " ORDER BY created_at DESC, id DESC"
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        for row in rows:
            path = attachment_path(row["file_path"])
            row["absolute_path"] = str(path)
            row["file_exists"] = path.is_file()
        return rows


def void_attachments(attachment_ids):
    if not attachment_ids:
        return
    with db_transaction() as conn:
        placeholders = ",".join("?" * len(attachment_ids))
        conn.execute(
            f"""UPDATE business_attachments
                SET status='void', updated_at=?
                WHERE id IN ({placeholders}) AND status='active'""",
            (_now(), *attachment_ids),
        )
