"""Local text-PDF invoice extraction. Recognition never writes business records."""
import hashlib
import re
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path

MONEY = r'[-−]?[\d,]+\.\d{2}'


def _unique(values, field, warnings):
    values = list(dict.fromkeys(values))
    if len(values) == 1:
        return values[0]
    warnings.append(f'{field}未识别或存在多个候选，请核对原票填写')
    return ''


def parse_invoice_text(text):
    text = text.replace('：', ':').replace('￥', '¥').replace('\u3000', ' ')
    warnings = []
    if '红字' in text or re.search(r'[-−]\d+\.\d{2}', text):
        raise ValueError('红字或负数发票暂不支持自动导入，请按红冲业务单独处理')
    number = _unique(re.findall(r'发票号码\s*:\s*(\d{8,20})(?!\d)', text), '发票号码', warnings)
    dates = re.findall(r'开票日期\s*:\s*(\d{4})\s*[年/-]\s*(\d{1,2})\s*[月/-]\s*(\d{1,2})', text)
    date = _unique(['-'.join((y, m.zfill(2), d.zfill(2))) for y, m, d in dates], '开票日期', warnings)
    if date:
        try:
            datetime.strptime(date, '%Y-%m-%d')
        except ValueError:
            date = ''
            warnings.append('开票日期无效，请核对')
    buyers = []
    for line in text.splitlines():
        if '名称:' in line.replace(' ', '') and re.search(r'购|购买方', line):
            match = re.search(r'名\s*称\s*:\s*(.+?)(?:\s{2,}|$)', line)
            if match:
                buyers.append(match[1].strip())
    buyer = _unique(buyers, '购买方', warnings)
    totals = []
    for line in text.splitlines():
        if re.match(r'^\s*合\s*计\s', line):
            amounts = re.findall(MONEY, line)
            if len(amounts) == 2:
                totals.append(tuple(v.replace(',', '') for v in amounts))
    total_pair = _unique(totals, '合计未税金额和税额', warnings)
    gross = _unique(re.findall(r'小写[)）]?\s*[:：]?\s*¥?\s*(' + MONEY + ')', text), '价税合计', warnings)
    gross = gross.replace(',', '')
    rates = sorted(set(re.findall(r'(?<!\d)(\d+(?:\.\d+)?)\s*%', text)), key=Decimal)
    if '免税' in text:
        rates.append('免税')
    if '不征税' in text:
        rates.append('不征税')
    rate = rates[0] if len(rates) == 1 else ('多税率' if rates else '')
    if not rates:
        warnings.append('税率未识别，请核对，不能将未识别当作零税率')
    net, tax = total_pair if total_pair else ('', '')
    if gross and net and tax and Decimal(net) + Decimal(tax) != Decimal(gross):
        warnings.append('未税金额与税额之和不等于价税合计，请修正后保存')
    return {'invoice_no': number, 'invoice_date': date, 'buyer_name': buyer,
            'amount': gross, 'net_amount': net, 'tax_amount': tax, 'tax_rate': rate,
            'tax_rate_label': ' / '.join(v if v in ('免税', '不征税') else v + '%' for v in rates),
            'warnings': warnings}


def recognize(path):
    from pypdf import PdfReader
    from io import BytesIO
    source = Path(path)
    if source.suffix.lower() != '.pdf' or not source.is_file():
        raise ValueError('请选择有效的 PDF 发票文件')
    if source.stat().st_size > 20 * 1024 * 1024:
        raise ValueError('PDF 超过20MB，请选择单张电子发票')
    content = source.read_bytes()
    try:
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted:
            raise ValueError('加密PDF暂不支持，请先提供未加密原票')
        if len(reader.pages) > 10:
            raise ValueError('请一次导入一张发票，PDF最多10页')
        text = '\n'.join((page.extract_text(extraction_mode='layout') or '')
                         if '/Contents' in page else '' for page in reader.pages)
    except ValueError:
        raise
    except Exception as error:
        raise ValueError('PDF无法读取，请确认文件完整') from error
    if '发票' not in text or not text.strip():
        raise ValueError('未读取到发票文字，扫描PDF暂不支持自动识别；可手动录入后添加附件')
    numbers = set(re.findall(r'发票号码\s*[:：]\s*(\d{8,20})', text))
    if len(numbers) > 1:
        raise ValueError('PDF中包含多张发票，请拆分后逐张导入')
    return {**parse_invoice_text(text), 'source_path': str(source.resolve()),
            'sha256': hashlib.sha256(content).hexdigest()}


def _existing_invoice_for_pdf(conn, recognition):
    from services._common import organization_id
    from services.attachment_service import invoice_attachment_statuses

    number = str(recognition.get('invoice_no') or '').strip()
    if not number:
        return None
    invoice = conn.execute('''SELECT i.*, p.name AS project_name,
            COALESCE(c.contract_no, '') AS contract_no
        FROM sales_invoices i JOIN projects p ON p.id=i.project_id
        LEFT JOIN contracts c ON c.id=i.contract_id
        WHERE i.organization_id=? AND i.invoice_no=?''',
        (organization_id(conn), number)).fetchone()
    if not invoice:
        return None
    invoice = dict(invoice)
    status = invoice_attachment_statuses(conn, [invoice['id']])[invoice['id']]
    invoice.update(status)
    invoice['has_available_attachment'] = status['attachment_count'] > status['missing_attachment_count']
    conflicts = []
    try:
        amount = Decimal(str(recognition.get('amount') or '')) * 100
        if not amount.is_finite() or amount != invoice['amount_minor']:
            conflicts.append('票面价税合计与原发票金额不一致')
    except ArithmeticError:
        conflicts.append('未识别到有效的票面价税合计')
    def normalize(value):
        return ''.join(str(value or '').split()).replace('（', '(').replace('）', ')')

    if invoice['buyer_name_snapshot'] and normalize(recognition.get('buyer_name')) != normalize(invoice['buyer_name_snapshot']):
        conflicts.append('票面购买方与原发票购买方不一致')
    invoice['pdf_conflicts'] = conflicts
    invoice['pdf_warnings'] = []
    if recognition.get('invoice_date') != invoice['invoice_date']:
        invoice['pdf_warnings'].append(
            f"票面日期 {recognition.get('invoice_date') or '未识别'} 与原登记日期 {invoice['invoice_date']} 不同；补附件不修改原日期")
    return invoice


def find_existing_invoice_for_pdf(recognition):
    """Read-only matching; the stored invoice, not the form project, owns the PDF."""
    from db.connection import db_read

    with db_read() as conn:
        return _existing_invoice_for_pdf(conn, recognition)


@contextmanager
def _stored_pdf(recognition):
    from uuid import uuid4
    from services import attachment_service

    source = Path(recognition['source_path'])
    content = source.read_bytes()
    if hashlib.sha256(content).hexdigest() != recognition['sha256']:
        raise ValueError('识别后PDF内容已变化，请重新选择文件识别')
    target = attachment_service._storage_root() / (uuid4().hex + '.pdf')
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open('xb') as output:
            output.write(content)
        yield source, target
    except Exception:
        target.unlink(missing_ok=True)
        raise


def _insert_pdf_attachment(conn, invoice_id, source, target, recognition, description):
    from uuid import uuid4
    from db.connection import PROJECT_ROOT
    from services._common import now, organization_id

    changed_at = now()
    stored = str(target.relative_to(PROJECT_ROOT)) if target.is_relative_to(PROJECT_ROOT) else str(target)
    cursor = conn.execute('''INSERT INTO business_attachments
        (public_id,organization_id,invoice_id,category,file_path,original_name,
         description,status,created_at,updated_at)
        VALUES (?,?,?,'销项发票PDF',?,?,?,'active',?,?)''',
        (str(uuid4()), organization_id(conn), invoice_id, stored, source.name,
         description + '；SHA256=' + recognition['sha256'], changed_at, changed_at))
    return cursor.lastrowid


def attach_pdf_to_existing(recognition):
    """Only add missing evidence; keep invoice, income, receipts and ownership intact."""
    from db.connection import db_transaction
    from services.attachment_service import attachment_path
    from services._common import now

    with _stored_pdf(recognition) as (source, target):
        with db_transaction(immediate=True) as conn:
            invoice = _existing_invoice_for_pdf(conn, recognition)
            if not invoice:
                raise ValueError('此票号尚未登记，请刷新后按新发票保存')
            if invoice['pdf_conflicts']:
                raise ValueError('；'.join(invoice['pdf_conflicts']) + '，请先核对原记录；未添加附件')
            if invoice['has_available_attachment']:
                raise ValueError('此发票已有可用附件，无需重复上传；可到原记录查看')
            missing_ids = [row['id'] for row in conn.execute(
                "SELECT id,file_path FROM business_attachments WHERE invoice_id=? AND status='active'", (invoice['id'],))
                if not attachment_path(row['file_path']).is_file()]
            changed_at = now()
            for attachment_id in missing_ids:
                conn.execute("UPDATE business_attachments SET status='void',updated_at=? WHERE id=?", (changed_at, attachment_id))
            description = '按相同票号补充已有发票附件，不修改财务记录'
            if missing_ids:
                description += '；原缺失附件记录保留为失效：' + ','.join(map(str, missing_ids))
            _insert_pdf_attachment(conn, invoice['id'], source, target, recognition, description)
        return invoice['id']


def save_with_pdf(data, recognition, invoice_id=None):
    """One transaction for invoice, automatic income and attachment metadata."""
    from db.connection import db_transaction
    from services import finance_service

    if not invoice_id and find_existing_invoice_for_pdf(recognition):
        return attach_pdf_to_existing(recognition)
    required = ('invoice_no', 'invoice_date', 'amount', 'net_amount', 'tax_amount', 'tax_rate', 'buyer_name')
    if any(data.get(field) is None or not str(data[field]).strip() for field in required):
        raise ValueError('PDF识别的票号、日期、金额、税额、税率和购买方需要核对完整后保存')
    with _stored_pdf(recognition) as (source, target):
        with db_transaction(immediate=True) as conn:
            if invoice_id:
                finance_service.update_invoice(invoice_id, data, _conn=conn)
            else:
                invoice_id = finance_service.create_invoice(data, _conn=conn)
            _insert_pdf_attachment(conn, invoice_id, source, target, recognition,
                                   '本地PDF识别，经人工核对保存')
        return invoice_id
