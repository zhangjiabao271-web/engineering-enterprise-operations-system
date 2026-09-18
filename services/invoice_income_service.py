"""Invoice-owned income; all writes share the caller's transaction."""
import json
from uuid import uuid4

from services._common import now

INCOME_MODES = {'manual': '手动确认收入', 'invoice': '随开票自动确认收入'}


def is_automatic(conn, contract_id):
    row = conn.execute('SELECT income_mode FROM contracts WHERE id=?',
                       (contract_id,)).fetchone()
    return bool(row and row['income_mode'] == 'invoice')


def audit(conn, contract_id, invoice_id, action, before, after):
    conn.execute('''INSERT INTO invoice_income_audit
        (contract_id, invoice_id, action, before_json, after_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)''',
        (contract_id, invoice_id, action,
         json.dumps(dict(before or {}), ensure_ascii=False),
         json.dumps(dict(after or {}), ensure_ascii=False), now()))


def set_mode(conn, contract_id, mode):
    contract = conn.execute('SELECT * FROM contracts WHERE id=?',
                            (contract_id,)).fetchone()
    if mode not in INCOME_MODES:
        raise ValueError('收入确认方式无效')
    if mode == 'invoice' and (contract['contract_type'] != 'annual'
                              or contract['pricing_mode'] != 'actual'):
        raise ValueError('随开票确认仅适用于按实际结算的年度框架合同')
    if mode == contract['income_mode']:
        return
    links = conn.execute('''SELECT l.* FROM invoice_income_links l
        JOIN sales_invoices i ON i.id=l.invoice_id WHERE i.contract_id=?''',
        (contract_id,)).fetchall()
    if mode == 'manual' and links:
        raise ValueError('合同已有随开票收入，请先核对历史记录，不能直接切回手动')
    if mode == 'invoice':
        invoices = conn.execute("SELECT * FROM sales_invoices WHERE contract_id=? AND status='active'",
                                (contract_id,)).fetchall()
        settlements = conn.execute("SELECT * FROM settlements WHERE contract_id=? AND status='active'",
                                   (contract_id,)).fetchall()
        adopted = set()
        for invoice in invoices:
            rows = conn.execute('''SELECT s.*, a.allocated_amount_minor
                FROM invoice_settlement_allocations a
                JOIN settlements s ON s.id=a.settlement_id WHERE a.invoice_id=?''',
                (invoice['id'],)).fetchall()
            if len(rows) != 1:
                raise ValueError('已有发票与收入确认不是一一对应，请先核对历史分配')
            settlement = rows[0]
            other = conn.execute('''SELECT 1 FROM invoice_settlement_allocations
                WHERE settlement_id=? AND invoice_id<>?''',
                (settlement['id'], invoice['id'])).fetchone()
            if (other or settlement['id'] in adopted or settlement['status'] != 'active'
                    or settlement['contract_id'] != contract_id
                    or settlement['project_id'] != invoice['project_id']
                    or settlement['amount_minor'] != invoice['amount_minor']
                    or settlement['allocated_amount_minor'] != invoice['amount_minor']):
                raise ValueError('已有发票和收入确认金额或归属不一致，请先核对，系统不会重复生成收入')
            adopted.add(settlement['id'])
            conn.execute('INSERT INTO invoice_income_links VALUES (?, ?, ?)',
                         (invoice['id'], settlement['id'], now()))
            audit(conn, contract_id, invoice['id'], 'adopt', settlement, settlement)
        if adopted != {s['id'] for s in settlements}:
            raise ValueError('合同还有未对应发票的收入确认，请先核对后再启用')
    conn.execute('UPDATE contracts SET income_mode=?, updated_at=? WHERE id=?',
                 (mode, now(), contract_id))
    audit(conn, contract_id, None, 'mode', {'income_mode': contract['income_mode']},
          {'income_mode': mode})


def protect_manual(conn, contract_id=None, settlement_id=None):
    if settlement_id and conn.execute(
            'SELECT 1 FROM invoice_income_links WHERE settlement_id=?',
            (settlement_id,)).fetchone():
        raise ValueError('这笔收入由发票自动维护，请到销项发票修改或作废对应发票')
    if contract_id and is_automatic(conn, contract_id):
        raise ValueError('该合同随开票自动确认收入，请直接登记销项发票，无需重复确认')


def prepare_income(conn, values, invoice_id=None, *, void=False):
    """Return owned settlement id, or None for legacy manual invoices."""
    current = None
    if invoice_id:
        current = conn.execute('''SELECT s.* FROM invoice_income_links l
            JOIN settlements s ON s.id=l.settlement_id WHERE l.invoice_id=?''',
            (invoice_id,)).fetchone()
    automatic = is_automatic(conn, values['contract_id'])
    if current and (current['contract_id'] != values['contract_id']
                    or current['project_id'] != values['project_id']):
        raise ValueError('随开票收入已绑定项目与合同，不能更换归属；请核对后作废重录')
    if not automatic and not current:
        return None
    if invoice_id and not current:
        raise ValueError('历史发票尚未匹配自动收入，请先核对历史记录，不能直接恢复或转入')
    if values.get('settlement_id') and (
            not current or values['settlement_id'] != current['id']):
        raise ValueError('随开票确认不需要选择其他收入记录')
    if current:
        received = conn.execute('''SELECT COALESCE(SUM(a.allocated_amount_minor),0)
            FROM receipt_allocations a JOIN receipts r ON r.id=a.receipt_id
            WHERE a.settlement_id=? AND a.status='active' AND r.status='active' ''',
            (current['id'],)).fetchone()[0]
        if (void and received) or (not void and received > values['amount_minor']):
            raise ValueError('对应收入已有回款，不能作废或将发票金额降至已回款以下；请先调整回款分配')
    if not void:
        from services.contract_service import _validate_contract_settlement, _project_policy
        project = _project_policy(conn, values['project_id'])
        if project['invoice_policy'] == 'not_required' or project['business_mode'] == 'cash':
            raise ValueError('无需开票的零星工程不能使用随开票收入')
        _validate_contract_settlement(conn, values['contract_id'], values['project_id'],
                                      values['amount_minor'],
                                      settlement_id=current['id'] if current else None)
    changed_at = now()
    if not current:
        cursor = conn.execute('''INSERT INTO settlements
            (public_id, organization_id, settlement_no, contract_id, project_id,
             settlement_date, amount_minor, basis, source_type, status, created_at, updated_at)
            VALUES (?, (SELECT organization_id FROM contracts WHERE id=?), ?, ?, ?, ?, ?, ?,
                    'contract', 'active', ?, ?)''',
            (str(uuid4()), values['contract_id'], 'KP-' + uuid4().hex[:12].upper(),
             values['contract_id'], values['project_id'], values['invoice_date'],
             values['amount_minor'], '随销项发票自动确认：' + values['invoice_no'], changed_at, changed_at))
        return cursor.lastrowid
    original_invoice = conn.execute('SELECT invoice_date FROM sales_invoices WHERE id=?',
                                    (invoice_id,)).fetchone()
    # Adoption preserves historical recognition dates unless the invoice date changes.
    settlement_date = current['settlement_date']
    if not void and values['invoice_date'] != original_invoice['invoice_date']:
        settlement_date = values['invoice_date']
    conn.execute('''UPDATE settlements SET amount_minor=?, settlement_date=?, basis=?,
                    status=?, updated_at=? WHERE id=?''',
                 (current['amount_minor'] if void else values['amount_minor'],
                  settlement_date,
                  current['basis'] if void else '随销项发票自动确认：' + values['invoice_no'],
                  'void' if void else 'active', changed_at, current['id']))
    after = conn.execute('SELECT * FROM settlements WHERE id=?', (current['id'],)).fetchone()
    audit(conn, values['contract_id'], invoice_id, 'void' if void else 'sync', current, after)
    return current['id']


def link_created_income(conn, invoice_id, settlement_id):
    conn.execute('INSERT INTO invoice_income_links VALUES (?, ?, ?)',
                 (invoice_id, settlement_id, now()))
    row = conn.execute('SELECT * FROM settlements WHERE id=?', (settlement_id,)).fetchone()
    audit(conn, row['contract_id'], invoice_id, 'create', None, row)
