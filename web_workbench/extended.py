"""Local CSV, invoice PDF and retained evidence adapters for existing services."""
import csv
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

CSV_MODULES = ('projects', 'suppliers', 'customers', 'offers')
PHOTO_TYPES = ('施工现场', '验收照片')


def csv_records(module, path):
    from services.web_workbench_service import SPECS
    if module not in CSV_MODULES:
        raise ValueError('此资料不支持 CSV 整批导入')
    raw = Path(path).read_bytes()
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('CSV 每批最多2MB')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValueError('请选择 UTF-8 编码的 CSV') from None
    fields = SPECS[module]['fields']
    mapping = {key: field['name'] for field in fields for key in (field['name'], field['label'])}
    reader = csv.reader(io.StringIO(text), strict=True)
    headers = next(reader, [])
    names = [mapping.get(label.strip()) for label in headers]
    if not names or None in names or len(set(names)) != len(names):
        raise ValueError('CSV 表头包含未知或重复字段，请下载当前模板')
    records = []
    for row in reader:
        if not any(value.strip() for value in row):
            continue
        if len(row) != len(names):
            raise ValueError(f'CSV 第{reader.line_num}行列数与表头不一致')
        records.append({'line': reader.line_num, 'values': dict(zip(names, row))})
        if len(records) > 5000:
            raise ValueError('每批最多5000条资料，不会截断导入')
    if not records:
        raise ValueError('CSV 没有可导入资料')
    return records


def reject_duplicate(conn, module, values):
    """Imports create records; updating an existing record requires its edit flow."""
    name = str(values.get('name') or values.get('legal_name') or '').strip()
    if module == 'projects':
        code = str(values.get('project_code') or '').strip()
        if not code:
            raise ValueError('整批项目导入须填写项目编号，以可靠识别重复资料')
        exists = conn.execute('SELECT 1 FROM projects WHERE project_code=?', (code,)).fetchone()
    elif module in ('suppliers', 'customers'):
        exists = conn.execute('SELECT 1 FROM business_partners WHERE trim(legal_name)=? COLLATE NOCASE', (name,)).fetchone()
    else:
        exists = conn.execute('SELECT 1 FROM supplier_offers so JOIN materials m ON m.id=so.material_id WHERE so.supplier_partner_id=? AND trim(m.name)=? AND COALESCE(m.specification,\'\')=?',
                              (values.get('supplier_id'), name, str(values.get('specification') or '').strip())).fetchone()
    if exists:
        raise ValueError('资料已存在，重复导入不会写入；请从原档案编辑：' + (name or str(values.get('project_code'))))


def run(action, payload):
    from db.connection import DB_PATH
    environment = os.environ.copy()
    environment.update(SUPPLY_CHAIN_DB_PATH=str(DB_PATH), PYTHONIOENCODING='utf-8')
    result = subprocess.run([sys.executable, '-B', '-m', 'web_workbench.extended', action],
                            input=json.dumps(payload, ensure_ascii=False), text=True, encoding='utf-8',
                            capture_output=True, env=environment, cwd=Path(__file__).resolve().parents[1], timeout=60)
    try:
        response = json.loads(result.stdout.splitlines()[-1])
    except (ValueError, IndexError):
        raise ValueError('本地操作未完成，请核对状态；未报告成功') from None
    if isinstance(response, dict) and 'error' in response:
        raise ValueError(response['error'])
    return response


def apply_evidence(conn, values):
    from services import attachment_service as attachments, construction_service as construction
    action = values['action']
    reason = str(values.get('reason') or '').strip()
    if not reason:
        raise ValueError('请填写移出、恢复或分类依据')
    if action in ('attachment_void', 'attachment_restore'):
        column = attachments.ENTITY_COLUMNS.get(values['entity'])
        if not column:
            raise ValueError('附件业务类型无效')
        row = conn.execute(f'SELECT * FROM business_attachments WHERE id=? AND {column[0]}=?',
                           (int(values['attachment_id']), int(values['id']))).fetchone()
        if not row:
            raise ValueError('附件不属于当前业务记录')
        if action == 'attachment_restore':
            if row['status'] != 'void' or not attachments.attachment_path(row['file_path']).is_file():
                raise ValueError('只可恢复原文件仍在本机的已移出附件')
            conn.execute("UPDATE business_attachments SET status='active' WHERE id=?", (row['id'],))
        else:
            if row['status'] != 'active':
                raise ValueError('附件已经移出，请刷新资料')
            attachments.void_attachments([row['id']])
        event = '恢复' if action == 'attachment_restore' else '移出（文件保留）'
        trace = f'\n[{datetime.now().isoformat(timespec="seconds")}] 网页{event}：{reason}'
        conn.execute('UPDATE business_attachments SET description=?, updated_at=? WHERE id=?',
                     ((row['description'] or '') + trace, datetime.now().isoformat(), row['id']))
        return {'id': row['id'], 'status': 'active' if action == 'attachment_restore' else 'void', 'file_retained': True}
    if action == 'photo_category':
        kind = values['category']
        if kind not in PHOTO_TYPES:
            raise ValueError('照片分类无效')
        row = conn.execute('SELECT * FROM construction_photos WHERE id=? AND record_id=?',
                           (int(values['photo_id']), int(values['id']))).fetchone()
        if not row:
            raise ValueError('照片不属于当前施工记录')
        trace = f'\n[{datetime.now().isoformat(timespec="seconds")}] 分类 {row["photo_type"]} → {kind}：{reason}'
        conn.execute('UPDATE construction_photos SET photo_type=?, notes=? WHERE id=?', (kind, (row['notes'] or '') + trace, row['id']))
        conn.execute('UPDATE business_attachments SET category=?, description=COALESCE(description,\'\') || ?, updated_at=? WHERE construction_record_id=? AND file_path=?',
                     (kind, trace, datetime.now().isoformat(), row['record_id'], row['file_path']))
        return {'id': row['id'], 'category': kind}
    raise ValueError('证据操作未开放')


def apply_pdf(payload):
    from services import invoice_pdf_service as output, input_invoice_service as inputs
    recognition = payload['recognition']
    path = Path(recognition['source_path'])
    if hashlib.sha256(path.read_bytes()).hexdigest() != recognition['sha256']:
        raise ValueError('识别后 PDF 已变化，请重新预览')
    from decimal import Decimal
    recognized_rate = recognition.get('tax_rate')
    if recognized_rate is not None and not payload.get('attach'):
        rate_input = payload['record'].get('tax_rate_percent') if payload['module'] == 'invoices' else str(payload['record'].get('tax_rate_label') or '').rstrip('%')
        try:
            equal_rate = Decimal(str(rate_input)) == Decimal(str(recognized_rate))
        except ArithmeticError:
            equal_rate = False
        if not equal_rate:
            raise ValueError('填写税率与 PDF 识别结果冲突，请核对原票')
    elif recognition.get('tax_rate_label') and not payload.get('attach'):
        if payload['record'].get('tax_rate_label') != recognition['tax_rate_label']:
            raise ValueError('填写税率标识与 PDF 识别结果冲突，请核对原票')
    for field in ('invoice_no', 'invoice_date', 'amount', 'net_amount', 'tax_amount', 'buyer_name', 'seller_name'):
        recognized = recognition.get(field)
        if recognized is None or recognized == '' or payload.get('attach'):
            continue
        entered = payload['record'].get(field)
        if field.endswith('amount'):
            try:
                equal = Decimal(str(recognized)) == Decimal(str(entered))
            except ArithmeticError:
                equal = False
        else:
            equal = str(recognized).strip() == str(entered or '').strip()
        if not equal:
            raise ValueError('填写字段与 PDF 识别结果冲突：' + field + '；请核对原文件，不会关联错误票面')
    if payload['module'] == 'invoices':
        if payload.get('attach'):
            return output.attach_pdf_to_existing(recognition)
        data = dict(payload['record'])
        data['tax_rate'] = data.get('tax_rate_percent')
        for key in ('project_id', 'contract_id', 'settlement_id'):
            data[key] = int(data[key]) if data.get(key) else None
        # Reuse original validation and its invoice / income / PDF transaction.
        return output.save_with_pdf(data, recognition)
    if payload['module'] == 'input_invoices':
        data = dict(payload['record'])
        for key in ('entity_id', 'supplier_id'):
            data[key] = int(data[key]) if data.get(key) else None
        return inputs.create_invoice(data, recognition=recognition)
    raise ValueError('PDF 业务类型无效')


def apply_photos(conn, payload):
    from PIL import Image
    from services import construction_service as construction, attachment_service as attachments
    from services.web_workbench_service import get_record
    from uuid import uuid4
    record = get_record('construction', int(payload['id']))
    if record['_version'] != payload['expected'] or record['record_status'] != '有效':
        raise ValueError('施工记录已变化或已作废，请重新核对')
    if payload['category'] not in PHOTO_TYPES or not 1 <= len(payload['files']) <= 20:
        raise ValueError('请选择施工现场或验收照片，每次最多20张')
    targets = []
    try:
        result = []
        for item in payload['files']:
            source = Path(item['path'])
            if source.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp'):
                raise ValueError('仅支持 PNG / JPG / WEBP 照片')
            with Image.open(source) as image:
                if image.width * image.height > 40_000_000:
                    raise ValueError('照片超过4000万像素，请缩小后上传')
                image.verify()
            content = source.read_bytes()
            digest = hashlib.sha256(content).digest()
            for existing in construction.get_construction_photos(int(payload['id'])):
                original = attachments.attachment_path(existing['file_path'])
                if original.is_file() and hashlib.sha256(original.read_bytes()).digest() == digest:
                    raise ValueError('当前施工记录已有相同照片；请修改分类或从历史恢复，不会重复导入')
            target = attachments._storage_root() / (uuid4().hex + source.suffix.lower())
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            targets.append(target)
            result.append(construction.add_construction_photo(int(payload['id']), str(target), item['name'], payload['category'], payload.get('notes', '')))
        return result
    except Exception:
        for target in targets:
            target.unlink(missing_ok=True)
        raise


def worker(action, payload):
    from db.connection import DB_PATH
    from services import web_workbench_service as business
    from services.web_workbench_operations import database_fingerprint
    from web_workbench.transfer import bind_business_connections
    # Import before binding: every reused service participates in this transaction.
    from services import attachment_service, construction_service, invoice_pdf_service
    database = Path(DB_PATH)
    with tempfile.TemporaryDirectory(prefix='engineering_local_preview_') as temporary:
        preview = action.endswith('_preview')
        selected = database
        if preview:
            selected = Path(temporary) / 'snapshot.db'
            with closing(sqlite3.connect(f'{database.as_uri()}?mode=ro', uri=True)) as source, closing(sqlite3.connect(selected)) as target:
                source.backup(target)
        with closing(sqlite3.connect(selected)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('BEGIN IMMEDIATE')
            try:
                fingerprint = database_fingerprint(conn)
                if not preview and fingerprint != payload['fingerprint']:
                    raise ValueError('经营资料在预览后已变化，本次未提交，请重新核对')
                with bind_business_connections(conn):
                    if action.startswith('csv_'):
                        if not preview and hashlib.sha256(Path(payload['path']).read_bytes()).hexdigest() != payload['sha256']:
                            raise ValueError('CSV 在预览后已变化，请重新选择')
                        results = []
                        for record in csv_records(payload['module'], payload['path']):
                            try:
                                reject_duplicate(conn, payload['module'], record['values'])
                                record_id = business.save_record(payload['module'], record['values'])
                                results.append({'line': record['line'], 'id': record_id, 'values': record['values']})
                            except (ValueError, TypeError, KeyError) as error:
                                raise ValueError(f'CSV 第{record["line"]}行：{error}；整批未提交') from None
                        result = {'rows': results, 'count': len(results), 'fingerprint': fingerprint,
                                  'sha256': hashlib.sha256(Path(payload['path']).read_bytes()).hexdigest()}
                    elif action.startswith('evidence_'):
                        result = apply_evidence(conn, payload)
                        result = {'result': result, 'fingerprint': fingerprint, 'record': payload}
                    elif action == 'pdf_apply':
                        result = apply_pdf(payload)
                    elif action == 'photo_apply':
                        result = apply_photos(conn, payload)
                    elif action == 'attachment_apply':
                        source = Path(payload['files'][0]['path'])
                        renamed = Path(temporary) / payload['files'][0]['name']
                        shutil.copy2(source, renamed)
                        result = attachment_service.add_attachment(payload['entity'], int(payload['id']), renamed,
                            category=payload.get('category') or '业务附件', description=payload.get('description',''))
                    elif action == 'input_attach_apply':
                        current=business.get_record('input_invoices',payload['id'])
                        if current['_version']!=payload['expected']:
                            raise ValueError('进项票在预览后已修改，请重新核对')
                        if hashlib.sha256(Path(payload['source_path']).read_bytes()).hexdigest()!=payload['recognition']['sha256']:
                            raise ValueError('预览后的 PDF 内容已变化，请重新选择')
                        result=business.inputs.attach_pdf(payload['id'],payload['source_path'])
                    elif action == 'save_apply':
                        result = business.save_record(payload['module'], payload['record'], payload.get('id'), payload.get('expected'))
                    elif action == 'action_apply':
                        result = business.action(payload['action'], payload['record'])
                    else:
                        raise ValueError('本地操作未开放')
                conn.rollback() if preview else conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise


if __name__ == '__main__':
    try:
        print(json.dumps(worker(sys.argv[1], json.load(sys.stdin)), ensure_ascii=False, default=str))
    except Exception as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        raise SystemExit(1)
