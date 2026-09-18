"""Local text-PDF invoice extraction. Recognition never writes business records."""
import hashlib
import re
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


def save_with_pdf(data, recognition, invoice_id=None):
    """One transaction for invoice, automatic income and attachment metadata."""
    from uuid import uuid4
    from db.connection import db_transaction, PROJECT_ROOT
    from services import attachment_service, finance_service
    from services._common import now, organization_id
    required = ('invoice_no', 'invoice_date', 'amount', 'net_amount', 'tax_amount', 'tax_rate', 'buyer_name')
    if any(data.get(field) is None or not str(data[field]).strip() for field in required):
        raise ValueError('PDF识别的票号、日期、金额、税额、税率和购买方需要核对完整后保存')
    source = Path(recognition['source_path'])
    content = source.read_bytes()
    if hashlib.sha256(content).hexdigest() != recognition['sha256']:
        raise ValueError('识别后PDF内容已变化，请重新选择文件识别')
    target = attachment_service._storage_root() / (uuid4().hex + '.pdf')
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open('xb') as output:
            output.write(content)
        with db_transaction(immediate=True) as conn:
            if invoice_id:
                finance_service.update_invoice(invoice_id, data, _conn=conn)
            else:
                invoice_id = finance_service.create_invoice(data, _conn=conn)
            changed_at = now()
            stored = str(target.relative_to(PROJECT_ROOT)) if target.is_relative_to(PROJECT_ROOT) else str(target)
            conn.execute('''INSERT INTO business_attachments
                (public_id,organization_id,invoice_id,category,file_path,original_name,
                 description,status,created_at,updated_at)
                VALUES (?,?,?,'销项发票PDF',?,?,?,'active',?,?)''',
                (str(uuid4()), organization_id(conn), invoice_id, stored, source.name,
                 '本地PDF识别，经人工核对保存；SHA256=' + recognition['sha256'], changed_at, changed_at))
        return invoice_id
    except Exception:
        target.unlink(missing_ok=True)
        raise
