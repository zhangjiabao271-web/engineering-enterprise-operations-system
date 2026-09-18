"""Restore contract archives incorrectly voided during cash-project conversion."""

from datetime import datetime


AUTO_VOID_NOTE = "项目转为零星现金工程且无剩余履约关系，合同自动作废"


def _remove_auto_void_note(notes):
    parts = [part.strip() for part in (notes or "").split("；")]
    return "；".join(part for part in parts if part and part != AUTO_VOID_NOTE)


def restore_cash_conversion_contracts(conn):
    rows = conn.execute(
        """SELECT c.id, c.notes
           FROM contracts c
           WHERE c.status='void'
             AND INSTR(COALESCE(c.notes, ''), ?)>0
             AND EXISTS (
                 SELECT 1 FROM contract_project_allocations old_link
                 WHERE old_link.contract_id=c.id
                   AND old_link.status='void'
                   AND old_link.notes LIKE '%项目转为零星现金工程%'
             )""",
        (AUTO_VOID_NOTE,),
    ).fetchall()
    changed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    for row in rows:
        conn.execute(
            """UPDATE contracts
               SET status='active', notes=?, updated_at=?
               WHERE id=?""",
            (_remove_auto_void_note(row["notes"]), changed_at, row["id"]),
        )


MIGRATIONS = [
    (
        430,
        "恢复现金工程转换时被误作废的合同档案",
        restore_cash_conversion_contracts,
    ),
]
