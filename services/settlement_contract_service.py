"""Attach a later signed contract without recreating business or receipt facts."""
import json

from db.connection import db_read, db_transaction
from services.entity_ownership import OwnershipResolver


def validate(conn, settlement_id, contract_id):
    from services.contract_service import _validate_contract_settlement
    from services.invoice_income_service import protect_manual
    row = conn.execute("SELECT * FROM settlements WHERE id=? AND status='active'", (settlement_id,)).fetchone()
    if not row or row['source_type'] != 'contract' or row['contract_id']:
        raise ValueError('仅合同待补的有效已确认业务可以补关联合同')
    protect_manual(conn, contract_id=contract_id)
    _validate_contract_settlement(conn, contract_id, row['project_id'], row['amount_minor'], settlement_id=settlement_id)
    contract = conn.execute('SELECT * FROM contracts WHERE id=?', (contract_id,)).fetchone()
    project = conn.execute('SELECT * FROM projects WHERE id=?', (row['project_id'],)).fetchone()
    if contract['customer_partner_id'] != project['customer_partner_id']:
        raise ValueError('合同与已确认业务不属于同一客户')
    resolver = OwnershipResolver(conn)
    old_owner = resolver.record('settlements', row)
    new_owner = resolver.contract(contract_id)
    if not old_owner['entity_id'] or old_owner['entity_id'] != new_owner['entity_id']:
        raise ValueError('请先核对业务与合同的经营主体，不能跨主体关联或在主体不明时关联')
    return dict(row), dict(contract)


def preview(settlement_id, contract_id):
    with db_read() as conn:
        row, contract = validate(conn, settlement_id, contract_id)
        return {'before': row, 'rows': [{'settlement_no': row['settlement_no'],
            'contract_no': contract['contract_no'], 'amount_minor': row['amount_minor']}],
            'notice': '沿用原收入记录、确认日期和回款，补充合同归属并保留变更历史；不会新增收入或到账。'}


def attach(settlement_id, contract_id, reason):
    if not str(reason or '').strip():
        raise ValueError('请填写补关联合同的依据')
    from services import finance_service as finance
    from services.operating_entity_service import validate_inherited_ownership_change
    from services.advance_receipt_service import reconcile
    with db_transaction(immediate=True) as conn:
        before, contract = validate(conn, settlement_id, contract_id)
        resolver = OwnershipResolver(conn)
        stamp = finance._now()
        receipts = conn.execute('''SELECT DISTINCT r.* FROM receipts r JOIN receipt_allocations a
            ON a.receipt_id=r.id WHERE a.settlement_id=? AND a.status='active' AND r.status='active' ''',
            (settlement_id,)).fetchall()
        for receipt in receipts:
            finance._insert_receipt_revision(conn, receipt, 'update', stamp)
        invoices = conn.execute('''SELECT DISTINCT i.* FROM sales_invoices i
            JOIN invoice_settlement_allocations a ON a.invoice_id=i.id
            WHERE a.settlement_id=? AND i.status='active' ''', (settlement_id,)).fetchall()
        for invoice in invoices:
            if invoice['contract_id']:
                raise ValueError('业务已关联另一合同的发票，请先核对发票归属')
            others = conn.execute('''SELECT 1 FROM invoice_settlement_allocations a JOIN settlements s
                ON s.id=a.settlement_id WHERE a.invoice_id=? AND s.id<>?
                AND s.contract_id IS NOT ?''', (invoice['id'], settlement_id, contract_id)).fetchone()
            if others:
                raise ValueError('发票同时涉及其他合同待补业务，请先核对整张发票归属')
            finance._insert_invoice_revision(conn, invoice, 'update', stamp)
            conn.execute('UPDATE sales_invoices SET contract_id=?,updated_at=? WHERE id=?',
                         (contract_id, stamp, invoice['id']))
        conn.execute('''UPDATE settlements SET contract_id=?,business_date=COALESCE(business_date,settlement_date),
            updated_at=? WHERE id=?''', (contract_id, stamp, settlement_id))
        conn.execute('''UPDATE receipt_allocations SET contract_id=?,notes=COALESCE(notes,'')||char(10)||?
            WHERE settlement_id=? AND status='active' ''', (contract_id, '补关联合同：' + reason, settlement_id))
        after = dict(conn.execute('SELECT * FROM settlements WHERE id=?', (settlement_id,)).fetchone())
        conn.execute('''INSERT INTO settlement_contract_revisions
            (settlement_id,previous_json,current_json,reason,created_at) VALUES (?,?,?,?,?)''',
            (settlement_id, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), reason, stamp))
        reconcile(conn, before['project_id'], contract_id)
        finance._reconcile_customer_invoice_receipts(conn, contract['customer_partner_id'], stamp)
        validate_inherited_ownership_change(conn, resolver)
        return settlement_id
