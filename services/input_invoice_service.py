"""Input invoices are evidence for existing costs, not additional cost entries."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from db.connection import db_read, db_transaction, PROJECT_ROOT
from services._common import now
from services.operating_entity_service import entity_for

DEDUCTIONS = {'pending':'待会计确认', 'eligible':'可抵扣', 'deducted':'已抵扣', 'ineligible':'不可抵扣'}


def recognize_pdf(path):
    from pypdf import PdfReader
    from services.invoice_pdf_service import recognize, _unique
    result = recognize(path)
    text = '\n'.join(p.extract_text(extraction_mode='layout') or '' for p in PdfReader(path).pages)
    text = text.replace('：', ':')
    sellers = []
    for line in text.splitlines():
        match = re.search(r'销(?:售方)?\s*名\s*称\s*:\s*(.+?)(?:\s{2,}|$)', line)
        if match:
            sellers.append(match[1].strip())
    result['seller_name'] = _unique(sellers, '销售方', result['warnings'])
    result['buyer_tax_number'] = ''
    result['seller_tax_number'] = ''
    # Parallel tax-number columns can safely be recognized only as a unique pair.
    pairs=[]
    for line in text.splitlines():
        numbers=re.findall(r'(?:统一社会信用代码|纳税人识别号)[^:]*:\s*([A-Z0-9]{15,20})(?![A-Z0-9])',line)
        if len(numbers)==2:
            pairs.append(tuple(numbers))
    if len(set(pairs))==1:
        result['buyer_tax_number'],result['seller_tax_number']=pairs[0]
    result['warnings'].append('请人工核对购销双方抬头、税号及抵扣资格，识别不会自动入账。')
    return result


def money(value):
    try:
        amount = Decimal(str(value)) * 100
        if not amount.is_finite() or amount < 0 or amount != amount.to_integral_value():
            raise ValueError()
        return int(amount)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('金额须为非负数字，且最多两位小数') from None


def _normal(value):
    return ''.join(str(value or '').split()).replace('（','(').replace('）',')')


def _validate(conn, data):
    entity = conn.execute('SELECT * FROM operating_entities WHERE id=? AND active=1', (data.get('entity_id'),)).fetchone()
    supplier = conn.execute('''SELECT b.* FROM business_partners b JOIN partner_roles r ON r.partner_id=b.id
        WHERE b.id=? AND r.role_code='supplier' AND b.status='active' ''', (data.get('supplier_id'),)).fetchone()
    if not entity or not supplier:
        raise ValueError('请选择经营主体和有效供应商')
    if _normal(data.get('buyer_name')) != _normal(entity['name']):
        raise ValueError('购买方抬头与经营主体不一致，不能跨主体登记')
    if _normal(data.get('seller_name')) != _normal(supplier['legal_name']):
        raise ValueError('销售方抬头与供应商名称不一致，请核对供应商档案')
    for key, expected in [('buyer_tax_number',entity['tax_number']),('seller_tax_number',supplier['unified_credit_code'])]:
        actual = str(data.get(key) or '').strip().upper()
        if expected and actual and actual != expected.upper():
            raise ValueError('发票税号与主体或供应商档案不一致')
    number = str(data.get('invoice_no') or '').strip()
    if not re.fullmatch(r'\d{8,20}', number):
        raise ValueError('发票号码须为8至20位数字')
    try:
        datetime.strptime(data.get('invoice_date',''), '%Y-%m-%d')
    except (ValueError, TypeError):
        raise ValueError('请选择正确的开票日期') from None
    net, tax, gross = (money(data.get(k)) for k in ('net_amount','tax_amount','amount'))
    if not gross or net+tax != gross:
        raise ValueError('未税金额加税额必须等于价税合计，且合计大于零')
    if not str(data.get('tax_rate_label') or '').strip():
        raise ValueError('请核对并填写税率；未知税率不能视为零')
    deduction = data.get('deduction_status','pending')
    if deduction not in DEDUCTIONS:
        raise ValueError('抵扣状态无效')
    if entity['tax_identity'] == 'small' and deduction in ('eligible','deducted'):
        raise ValueError('小规模纳税人不能将进项税标记为可抵扣或已抵扣')
    period = str(data.get('deduction_period') or '').strip()
    if deduction == 'deducted':
        try:
            datetime.strptime(period, '%Y-%m')
        except ValueError:
            raise ValueError('已抵扣需填写会计确认的抵扣月份 YYYY-MM') from None
    elif period:
        raise ValueError('只有已抵扣发票填写抵扣月份')
    return entity, number, net, tax, gross


def _source(conn, kind, record_id):
    if kind not in ('purchase_orders','cost_entries'):
        raise ValueError('仅能关联采购或费用')
    row = conn.execute(f'SELECT * FROM {kind} WHERE id=?', (record_id,)).fetchone()
    if not row or row['status'] != 'active':
        raise ValueError('成本记录不存在或已作废')
    if kind == 'cost_entries' and row['source_type'] != 'manual':
        raise ValueError('仅关联直接登记的费用，其他成本请关联其原始采购单，避免重复')
    return row


def _check_links(conn, invoice, links, exclude_invoice_id=None):
    used = set()
    total = 0
    for link in links:
        kind, record_id = link['kind'], int(link['record_id'])
        if (kind,record_id) in used:
            raise ValueError('同一成本记录不能重复关联')
        used.add((kind,record_id))
        row = _source(conn,kind,record_id)
        if entity_for(conn,kind,record_id) != invoice['entity_id']:
            raise ValueError('成本主体未确认或与发票购买主体不同，请先在经营主体页确认')
        if kind == 'purchase_orders' and row['supplier_partner_id'] != invoice['supplier_id']:
            raise ValueError('采购供应商与发票销售方不一致')
        if kind == 'cost_entries' and _normal(row['counterparty_name_snapshot']) != _normal(invoice['seller_name']):
            raise ValueError('费用往来单位与发票销售方不一致，请先核对费用记录')
        amount = money(link['amount'])
        if amount <= 0:
            raise ValueError('关联金额必须大于零')
        column = 'purchase_order_id' if kind == 'purchase_orders' else 'cost_entry_id'
        already = conn.execute(f'''SELECT COALESCE(SUM(l.amount_minor),0)
            FROM input_invoice_links l JOIN input_invoices i ON i.id=l.invoice_id
            WHERE l.{column}=? AND i.status='active' AND i.id<>?''',
            (record_id,exclude_invoice_id or -1)).fetchone()[0]
        limit = row['total_amount_cents'] if kind=='purchase_orders' else row['amount_minor']
        if already+amount > limit:
            raise ValueError('关联金额超过这笔成本尚未关联发票的金额')
        total += amount
    if total > invoice['gross_minor']:
        raise ValueError('关联总额不能超过发票价税合计')


def _save_links(conn, invoice_id, links):
    conn.execute('DELETE FROM input_invoice_links WHERE invoice_id=?', (invoice_id,))
    for link in links:
        purchase = int(link['record_id']) if link['kind']=='purchase_orders' else None
        cost = int(link['record_id']) if link['kind']=='cost_entries' else None
        conn.execute('''INSERT INTO input_invoice_links(invoice_id,purchase_order_id,cost_entry_id,amount_minor)
            VALUES (?,?,?,?)''',(invoice_id,purchase,cost,money(link['amount'])))


def _audit(conn, invoice_id, action, before, after):
    conn.execute('''INSERT INTO input_invoice_audit(invoice_id,action,before_json,after_json,created_at)
        VALUES (?,?,?,?,?)''',(invoice_id,action,json.dumps(before,ensure_ascii=False),
                               json.dumps(after,ensure_ascii=False),now()))


def create_invoice(data, links=(), recognition=None):
    if data.get('deduction_status', 'pending') != 'pending':
        raise ValueError('新发票先登记为待会计确认，再通过抵扣状态入口确认')
    target = None
    try:
        file_hash = None
        if recognition:
            source = Path(recognition['source_path'])
            if source.suffix.lower() != '.pdf' or source.stat().st_size > 20*1024*1024:
                raise ValueError('请选择20MB以内的PDF')
            content = source.read_bytes()
            file_hash = hashlib.sha256(content).hexdigest()
            if file_hash != recognition['sha256']:
                raise ValueError('识别后PDF已变化，请重新识别')
            from services.attachment_service import _storage_root
            target = _storage_root() / (uuid4().hex+'.pdf')
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as output:
                output.write(content)
        with db_transaction(immediate=True) as conn:
            entity, number, net, tax, gross = _validate(conn,data)
            invoice = dict(data,entity_id=entity['id'],gross_minor=gross)
            _check_links(conn,invoice,links)
            stored = None if target is None else str(target.relative_to(PROJECT_ROOT) if target.is_relative_to(PROJECT_ROOT) else target)
            stamp = now()
            invoice_id = conn.execute('''INSERT INTO input_invoices(
                invoice_no,invoice_date,entity_id,supplier_id,buyer_name,seller_name,
                buyer_tax_number,seller_tax_number,net_minor,tax_minor,gross_minor,tax_rate_label,
                deduction_status,deduction_period,notes,file_path,file_sha256,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (number,data['invoice_date'],entity['id'],int(data['supplier_id']),data['buyer_name'].strip(),
                 data['seller_name'].strip(),data.get('buyer_tax_number','').strip().upper(),
                 data.get('seller_tax_number','').strip().upper(),net,tax,gross,data['tax_rate_label'].strip(),
                 data.get('deduction_status','pending'),data.get('deduction_period') or None,
                 data.get('notes',''),stored,file_hash,stamp,stamp)).lastrowid
            _save_links(conn,invoice_id,links)
            _audit(conn,invoice_id,'create',{}, {'invoice':invoice,'links':list(links)})
            return invoice_id
    except Exception as error:
        if target:
            target.unlink(missing_ok=True)
        if isinstance(error,sqlite3.IntegrityError) and 'UNIQUE constraint failed: input_invoices.' in str(error):
            raise ValueError('发票号码或PDF已登记，不能重复导入') from error
        raise


def list_invoices(entity_id=None, include_void=False):
    with db_read() as conn:
        result=[]
        for r in conn.execute('SELECT i.*,e.name entity_name FROM input_invoices i JOIN operating_entities e ON e.id=i.entity_id ORDER BY invoice_date DESC,i.id DESC'):
            if (entity_id and r['entity_id']!=entity_id) or (not include_void and r['status']!='active'):
                continue
            row=dict(r)
            row['links'] = [dict(l) for l in conn.execute('SELECT * FROM input_invoice_links WHERE invoice_id=?',(r['id'],))]
            row['linked_minor']=sum(l['amount_minor'] for l in row['links'])
            row['remaining_minor']=row['gross_minor']-row['linked_minor']
            row['warning']=link_warning(conn,row)
            from services.attachment_service import attachment_path
            row['attachment_status']='未上传' if not row['file_path'] else ('已上传' if attachment_path(row['file_path']).is_file() else '文件缺失')
            result.append(row)
        return result


def link_warning(conn, invoice):
    links=[]
    for row in invoice['links']:
        kind='purchase_orders' if row['purchase_order_id'] else 'cost_entries'
        links.append({'kind':kind,'record_id':row['purchase_order_id'] or row['cost_entry_id'],
                      'amount':str(Decimal(row['amount_minor'])/100)})
    try:
        _check_links(conn,invoice,links,invoice['id'])
    except ValueError as error:
        return str(error)
    return ''


def update_links(invoice_id, links):
    with db_transaction(immediate=True) as conn:
        row=conn.execute("SELECT * FROM input_invoices WHERE id=? AND status='active'",(invoice_id,)).fetchone()
        if not row:
            raise ValueError('有效进项票不存在')
        _check_links(conn,row,links,invoice_id)
        before=[dict(r) for r in conn.execute('SELECT * FROM input_invoice_links WHERE invoice_id=?',(invoice_id,))]
        _save_links(conn,invoice_id,links)
        _audit(conn,invoice_id,'links',before,list(links))


def update_invoice(invoice_id, data):
    with db_transaction(immediate=True) as conn:
        row=conn.execute("SELECT * FROM input_invoices WHERE id=? AND status='active'",(invoice_id,)).fetchone()
        if not row:
            raise ValueError('有效进项票不存在')
        if row['deduction_status']=='deducted':
            raise ValueError('已抵扣发票请先由会计确认是否需要转出，不能直接修改票面信息')
        data = dict(data, deduction_status=row['deduction_status'],
                    deduction_period=row['deduction_period'])
        entity,number,net,tax,gross=_validate(conn,data)
        if row['file_path'] or row['file_sha256']:
            protected = {
                'invoice_no': number, 'invoice_date': data['invoice_date'],
                'entity_id': entity['id'], 'supplier_id': int(data['supplier_id']),
                'buyer_name': data['buyer_name'], 'seller_name': data['seller_name'],
                'buyer_tax_number': data.get('buyer_tax_number', '').strip().upper(),
                'seller_tax_number': data.get('seller_tax_number', '').strip().upper(),
                'net_minor': net, 'tax_minor': tax, 'gross_minor': gross,
                'tax_rate_label': data['tax_rate_label'].strip(),
            }
            if any(_normal(value) != _normal(row[key]) for key, value in protected.items()):
                raise ValueError('已有原始PDF，不能直接修改票号、日期、购销双方或票面金额税率；请先核对原票。备注和成本关联仍可修改')
        links=[{'kind':'purchase_orders' if r['purchase_order_id'] else 'cost_entries',
                'record_id':r['purchase_order_id'] or r['cost_entry_id'],
                'amount':str(Decimal(r['amount_minor'])/100)} for r in conn.execute(
                    'SELECT * FROM input_invoice_links WHERE invoice_id=?',(invoice_id,))]
        _check_links(conn,dict(data,entity_id=entity['id'],gross_minor=gross),links,invoice_id)
        if conn.execute('SELECT 1 FROM input_invoices WHERE invoice_no=? AND id<>?',(number,invoice_id)).fetchone():
            raise ValueError('该发票号码已存在')
        conn.execute('''UPDATE input_invoices SET invoice_no=?,invoice_date=?,entity_id=?,supplier_id=?,
            buyer_name=?,seller_name=?,buyer_tax_number=?,seller_tax_number=?,net_minor=?,tax_minor=?,
            gross_minor=?,tax_rate_label=?,notes=?,updated_at=? WHERE id=?''',
            (number,data['invoice_date'],entity['id'],data['supplier_id'],data['buyer_name'],data['seller_name'],
             data.get('buyer_tax_number',''),data.get('seller_tax_number',''),net,tax,gross,
             data['tax_rate_label'],data.get('notes',''),now(),invoice_id))
        _audit(conn,invoice_id,'edit',dict(row),dict(data))


def set_deduction(invoice_id, status, period='', reason=''):
    if not reason.strip():
        raise ValueError('请填写会计确认依据')
    with db_transaction(immediate=True) as conn:
        row=conn.execute("SELECT * FROM input_invoices WHERE id=? AND status='active'",(invoice_id,)).fetchone()
        if not row:
            raise ValueError('有效进项票不存在')
        data=dict(row,net_amount=str(Decimal(row['net_minor'])/100),tax_amount=str(Decimal(row['tax_minor'])/100),
                  amount=str(Decimal(row['gross_minor'])/100),deduction_status=status,deduction_period=period)
        _validate(conn,data)
        conn.execute('UPDATE input_invoices SET deduction_status=?,deduction_period=?,updated_at=? WHERE id=?',
                     (status,period or None,now(),invoice_id))
        _audit(conn,invoice_id,'deduction',dict(row),{'status':status,'period':period,'reason':reason})


def void_invoice(invoice_id, reason):
    if not reason.strip():
        raise ValueError('请填写作废原因')
    with db_transaction(immediate=True) as conn:
        row=conn.execute("SELECT * FROM input_invoices WHERE id=? AND status='active'",(invoice_id,)).fetchone()
        if not row:
            raise ValueError('有效进项票不存在')
        if row['deduction_status']=='deducted':
            raise ValueError('已抵扣发票请先由会计确认抵扣转出，再调整抵扣状态；不能直接作废')
        conn.execute("UPDATE input_invoices SET status='void',updated_at=? WHERE id=?",(now(),invoice_id))
        _audit(conn,invoice_id,'void',dict(row),{'reason':reason})


def attach_pdf(invoice_id, path):
    """Add original evidence to an existing manual entry without replacing evidence."""
    recognition = recognize_pdf(path)
    content = Path(recognition['source_path']).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != recognition['sha256']:
        raise ValueError('识别后PDF已变化，请重新选择')
    target = None
    try:
        with db_transaction(immediate=True) as conn:
            row = conn.execute("SELECT * FROM input_invoices WHERE id=? AND status='active'", (invoice_id,)).fetchone()
            if not row:
                raise ValueError('有效进项票不存在')
            if row['file_path']:
                raise ValueError('已有原始附件，不能覆盖；文件缺失请从备份恢复')
            if recognition.get('invoice_no') != row['invoice_no']:
                raise ValueError('PDF发票号码与当前记录不一致，或无法识别，请核对')
            for field, stored in [('amount', 'gross_minor'), ('net_amount', 'net_minor'), ('tax_amount', 'tax_minor')]:
                if money(recognition.get(field)) != row[stored]:
                    raise ValueError('PDF金额与当前记录不一致，请先核对票面记录')
            for field in ('buyer_name', 'seller_name'):
                if recognition.get(field) and _normal(recognition[field]) != _normal(row[field]):
                    raise ValueError('PDF购销双方与当前记录不一致，请核对')
            if conn.execute('SELECT 1 FROM input_invoices WHERE file_sha256=?', (digest,)).fetchone():
                raise ValueError('该PDF已登记，不能重复上传')
            from services.attachment_service import _storage_root
            target = _storage_root() / (uuid4().hex + '.pdf')
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as output:
                output.write(content)
            stored = str(target.relative_to(PROJECT_ROOT) if target.is_relative_to(PROJECT_ROOT) else target)
            conn.execute('UPDATE input_invoices SET file_path=?,file_sha256=?,updated_at=? WHERE id=?',
                         (stored, digest, now(), invoice_id))
            _audit(conn, invoice_id, 'attach_pdf', {}, {'file_path': stored, 'sha256': digest})
    except Exception:
        if target:
            target.unlink(missing_ok=True)
        raise


def cost_candidates(entity_id, supplier_id=None, kind='purchase_orders', keyword=''):
    with db_read() as conn:
        result=[]
        if kind not in ('purchase_orders','cost_entries'):
            raise ValueError('请选择采购或费用')
        supplier=conn.execute('SELECT legal_name FROM business_partners WHERE id=?',(supplier_id,)).fetchone() if supplier_id else None
        for row in conn.execute(f'SELECT * FROM {kind} ORDER BY id DESC'):
            if row['status']!='active' or entity_for(conn,kind,row['id'])!=entity_id:
                continue
            if kind=='cost_entries' and row['source_type']!='manual':
                continue
            name=row['merchant_name_snapshot'] if kind=='purchase_orders' else row['counterparty_name_snapshot']
            if supplier_id and (row['supplier_partner_id']!=supplier_id if kind=='purchase_orders' else _normal(name)!=_normal(supplier[0] if supplier else '')):
                continue
            if keyword and keyword.casefold() not in ' '.join(str(v or '') for v in row).casefold():
                continue
            column='purchase_order_id' if kind=='purchase_orders' else 'cost_entry_id'
            linked=conn.execute(f'''SELECT COALESCE(SUM(l.amount_minor),0) FROM input_invoice_links l
                JOIN input_invoices i ON i.id=l.invoice_id WHERE l.{column}=? AND i.status='active' ''',(row['id'],)).fetchone()[0]
            total=row['total_amount_cents'] if kind=='purchase_orders' else row['amount_minor']
            result.append({'id':row['id'],'kind':kind,'label':row['order_no'] if kind=='purchase_orders' else row['cost_no'],
                           'counterparty':name,'amount_minor':total,'linked_minor':linked,'remaining_minor':max(total-linked,0)})
        return result
