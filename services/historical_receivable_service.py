"""Separate historical debt and collections; never creates current revenue."""
import json
from datetime import date
from db.connection import db_read, db_transaction
from services._common import now
from services._constraint_errors import translate_constraints
from services.funds_service import _money


def transaction(write=True):
    if write:
        return db_transaction(immediate=True)
    return db_read()


def audit(conn, entity, entity_id, before, after, reason):
    conn.execute('INSERT INTO historical_receivable_audit(entity,entity_id,before_json,after_json,reason,created_at) VALUES (?,?,?,?,?,?)',
                 (entity, entity_id, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), reason, now()))


@translate_constraints(
    duplicate="该客户已有同名历史旧账项目，请修改名称或选中原项目",
    related="关联的客户档案已不存在，请刷新后重选",
    invalid="历史旧账项目资料不符合保存条件，请核对后重试",
)
def save_project(customer_id, name, opening_amount=None, notes='', project_id=None):
    name = str(name).strip()
    if not name:
        raise ValueError('请填写历史旧账项目名称')
    opening = None if opening_amount is None or str(opening_amount).strip() == '' else _money(opening_amount, zero=True)
    with transaction() as conn:
        if not conn.execute("SELECT 1 FROM business_partners b JOIN partner_roles r ON r.partner_id=b.id WHERE b.id=? AND r.role_code='customer'", (customer_id,)).fetchone():
            raise ValueError('请选择客户档案')
        before = {}
        if project_id:
            row = conn.execute('SELECT * FROM historical_receivable_projects WHERE id=?', (project_id,)).fetchone()
            if not row:
                raise ValueError('历史项目不存在')
            before = dict(row)
            received = conn.execute("SELECT COALESCE(SUM(amount_minor),0) FROM historical_receipts WHERE project_id=? AND status='active'", (project_id,)).fetchone()[0]
            if before['customer_id'] != customer_id:
                raise ValueError('历史项目不能更换客户')
            if opening is not None and opening < received:
                raise ValueError('历史欠款总额不能低于累计收回金额')
            conn.execute('UPDATE historical_receivable_projects SET name=?,opening_minor=?,notes=?,updated_at=? WHERE id=?', (name,opening,notes,now(),project_id))
        else:
            project_id = conn.execute('INSERT INTO historical_receivable_projects(customer_id,name,opening_minor,notes,created_at,updated_at) VALUES (?,?,?,?,?,?)', (customer_id,name,opening,notes,now(),now())).lastrowid
        audit(conn,'project',project_id,before,dict(conn.execute('SELECT * FROM historical_receivable_projects WHERE id=?',(project_id,)).fetchone()),'维护历史欠款项目')
        return project_id


@translate_constraints(
    duplicate='这笔历史回款已登记，请刷新后核对，勿重复录入',
    related='历史项目或客户档案已变化，请刷新后重选',
    invalid='历史回款不符合保存条件，请核对后重试',
)
def record_receipt(project_id, receipt_date, amount, request_key, payment_method='待确认', notes='', *, payer_name=None):
    amount_minor = _money(amount)
    try:
        receipt_date = date.fromisoformat(str(receipt_date)).isoformat()
    except (TypeError, ValueError):
        raise ValueError('请填写有效收款日期') from None
    if receipt_date > date.today().isoformat():
        raise ValueError('实际回款日期不能在未来')
    if not request_key:
        raise ValueError('缺少本次登记标识')
    with transaction() as conn:
        previous = conn.execute('SELECT * FROM historical_receipts WHERE request_key=?',(request_key,)).fetchone()
        if previous:
            if payer_name is not None and payer_name.strip() and payer_name.strip() != previous['payer_name_snapshot']:
                raise ValueError('登记标识已使用，付款人不一致')
            if (previous['project_id'],previous['receipt_date'],previous['amount_minor'],previous['payment_method'],previous['notes'],previous['status']) != (project_id,receipt_date,amount_minor,payment_method,notes,'active'):
                raise ValueError('登记标识已使用，不能重复登记不同内容')
            return previous['id']
        project = conn.execute('SELECT * FROM historical_receivable_projects WHERE id=?',(project_id,)).fetchone()
        if not project:
            raise ValueError('请选择历史旧账项目')
        received = conn.execute("SELECT COALESCE(SUM(amount_minor),0) FROM historical_receipts WHERE project_id=? AND status='active'",(project_id,)).fetchone()[0]
        if project['opening_minor'] is not None and received+amount_minor > project['opening_minor']:
            raise ValueError('累计回款超过已确认的历史欠款总额，请先核对总额')
        payer = str(payer_name or '').strip() or conn.execute('SELECT legal_name FROM business_partners WHERE id=?',(project['customer_id'],)).fetchone()[0]
        receipt_id = conn.execute('INSERT INTO historical_receipts(project_id,request_key,receipt_date,amount_minor,payment_method,notes,created_at,updated_at,payer_name_snapshot) VALUES (?,?,?,?,?,?,?,?,?)',(project_id,request_key,receipt_date,amount_minor,payment_method,notes,now(),now(),payer)).lastrowid
        audit(conn,'receipt',receipt_id,{},dict(conn.execute('SELECT * FROM historical_receipts WHERE id=?',(receipt_id,)).fetchone()),'登记历史旧账回款')
        return receipt_id


def list_projects():
    with transaction(write=False) as conn:
        rows = conn.execute("""SELECT p.*,b.legal_name AS customer_name,
            COALESCE(SUM(CASE WHEN r.status='active' THEN r.amount_minor ELSE 0 END),0) AS received_minor
            FROM historical_receivable_projects p JOIN business_partners b ON b.id=p.customer_id
            LEFT JOIN historical_receipts r ON r.project_id=p.id GROUP BY p.id ORDER BY p.id DESC""").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item['remaining_minor'] = None if item['opening_minor'] is None else item['opening_minor']-item['received_minor']
            item['state'] = '总额待确认' if item['remaining_minor'] is None else ('已结清' if item['remaining_minor']==0 else '待回款')
            result.append(item)
        return result


def list_receipts(project_id):
    with transaction(write=False) as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM historical_receipts WHERE project_id=? AND status='active' ORDER BY receipt_date DESC,id DESC",(project_id,))]
