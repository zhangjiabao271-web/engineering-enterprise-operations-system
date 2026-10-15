"""HTTP adapters for local, previewed batch and evidence workflows."""
import csv
import hashlib
import io
import json
import secrets
import time
from pathlib import Path

from web_workbench import extended


def fingerprint():
    from db.connection import db_read
    from services.web_workbench_operations import database_fingerprint
    with db_read() as conn:
        return database_fingerprint(conn)


def get_api(handler, path, query):
    from services import web_workbench_service as business
    if path == '/api/receipt_income_preview':
        from services.receipt_income_service import public_preview
        handler.send_json(public_preview(int(query['id']), query.get('amount')))
        return True
    if path == '/api/write_ticket':
        kind = query.get('kind', 'save')
        module = query.get('module')
        action = query.get('action')
        record_id = int(query['id']) if query.get('id') else None
        if kind == 'save':
            schema = business.schema()
            capability = 'can_update' if record_id else 'can_create'
            if module not in schema or not schema[module][capability]:
                raise ValueError('此表单保存未开放')
            if record_id and business.get_record(module, record_id)['_version'] != query.get('expected'):
                raise ValueError('记录版本已变化，请重新打开表单')
        elif kind == 'action':
            if action not in ('plan_cancel','inspection','assign_labor','assign_purchase','collection','project_close','project_site','historical_receipt'):
                raise ValueError('此业务动作未开放')
        elif kind == 'photos_upload':
            if business.get_record('construction',record_id)['_version'] != query.get('expected'):
                raise ValueError('施工记录版本已变化，请重新打开照片列表')
        elif kind == 'upload_attachment':
            from services.attachment_service import ENTITY_COLUMNS
            entity = query.get('entity')
            if entity not in ENTITY_COLUMNS:
                raise ValueError('附件业务类型无效')
            from db.connection import db_read
            with db_read() as conn:
                table = ENTITY_COLUMNS[entity][1]
                if not conn.execute(f'SELECT 1 FROM {table} WHERE id=?', (record_id,)).fetchone():
                    raise ValueError('附件业务记录不存在')
        else:
            raise ValueError('此写入类型未开放')
        token = secrets.token_hex(24)
        handler.server.previews[token] = {'kind':'write_ticket','operation':kind,'module':module,'action':action,
            'id':record_id,'entity':query.get('entity'),'expected':query.get('expected'),'fingerprint':fingerprint(),'created':time.monotonic()}
        handler.send_json({'token':token})
    elif path == '/api/monthly_workdays':
        handler.send_json(business.labor.get_worker_monthly_workdays(query['start'], query['end']))
    elif path == '/api/csv_template':
        module = query['module']
        if module not in extended.CSV_MODULES:
            raise ValueError('未开放此 CSV 模板')
        stream = io.StringIO()
        csv.writer(stream).writerow(field['name'] for field in business.SPECS[module]['fields'])
        handler.send_content(('\ufeff' + stream.getvalue()).encode('utf-8'), 'text/csv; charset=utf-8', attachment=module+'-template.csv')
    elif path == '/api/photos':
        from db.connection import db_read
        record_id = int(query['id'])
        business.get_record('construction', record_id)
        with db_read() as conn:
            rows = conn.execute('''SELECT cp.*, ba.id AS attachment_id, ba.status AS attachment_status
                FROM construction_photos cp LEFT JOIN business_attachments ba
                ON ba.construction_record_id=cp.record_id AND ba.file_path=cp.file_path
                WHERE cp.record_id=? ORDER BY cp.id''', (record_id,)).fetchall()
        handler.send_json([dict(row) for row in rows])
    else:
        return False
    return True


def request_digest(data):
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def guarded_write(handler, path, data, execute):
    """One current-service form ticket authorizes one exact confirmed request."""
    kind = path.split('/')[-1]
    preview = saved_preview(handler, data.get('form_ticket'), 'write_ticket')
    value = data.get('id') or (data.get('record') or {}).get('id')
    record_id = int(value) if value else None
    if preview['operation'] != kind or preview['id'] != record_id:
        raise ValueError('保存令牌不属于当前表单')
    if kind == 'save' and (preview['module'] != data.get('module') or preview['expected'] != data.get('expected')):
        raise ValueError('保存令牌与记录版本不一致')
    if kind == 'action' and preview['action'] != data.get('action'):
        raise ValueError('保存令牌与业务动作不一致')
    if kind == 'upload_attachment' and preview['entity'] != data.get('entity'):
        raise ValueError('保存令牌与附件业务类型不一致')
    if kind == 'photos_upload' and preview['expected'] != data.get('expected'):
        raise ValueError('保存令牌与施工记录版本不一致')
    digest = request_digest({key:value for key,value in data.items() if key != 'form_ticket'})
    if 'applied' in preview:
        if preview['request_digest'] != digest:
            raise ValueError('此表单已保存，不能用同一令牌提交不同内容')
        return preview['applied']
    result = execute(preview)
    preview.update(applied=result, request_digest=digest)
    return result


def saved_preview(handler, token, kind):
    preview = handler.server.previews.get(token)
    if not preview or preview.get('kind') != kind or time.monotonic()-preview['created'] > 1800:
        raise ValueError('预览已取消或超过30分钟，请重新核对')
    return preview


def post_api(handler, path, data):
    if path == '/api/write_discard':
        preview = handler.server.previews.get(data.get('token'))
        if preview and preview.get('kind') == 'write_ticket' and 'applied' not in preview:
            handler.server.previews.pop(data['token'])
        return True
    if path in ('/api/csv_discard','/api/pdf_discard','/api/evidence_discard'):
        kind = path.split('/')[-1].split('_')[0]
        preview = handler.server.previews.get(data['token'])
        if preview and preview.get('kind') == kind:
            handler.server.previews.pop(data['token'])
            if preview.get('path'):
                Path(preview['path']).unlink(missing_ok=True)
        return True
    if path == '/api/csv_preview':
        token, file, name = handler.staged_upload(data)
        try:
            if file.suffix != '.csv':
                raise ValueError('请选择 UTF-8 CSV 文件')
            review = extended.run('csv_preview', {'module':data['module'], 'path':str(file)})
            handler.server.previews[token] = {'kind':'csv', 'module':data['module'], 'path':str(file),
                'fingerprint':review['fingerprint'], 'sha256':review['sha256'], 'created':time.monotonic()}
            return {'token':token, 'review':review, 'name':name}
        except Exception:
            file.unlink(missing_ok=True)
            raise
    if path == '/api/evidence_preview':
        review = extended.run('evidence_preview', data)
        token = secrets.token_hex(24)
        handler.server.previews[token] = {**review, 'kind':'evidence', 'created':time.monotonic()}
        return {'token':token, 'review':review}
    if path == '/api/pdf_preview':
        from services import invoice_pdf_service as output, input_invoice_service as inputs
        token, file, name = handler.staged_upload(data)
        try:
            module = data['module']
            if module not in ('invoices','input_invoices') or file.suffix != '.pdf':
                raise ValueError('请选择销项或进项发票的文字型 PDF')
            recognition = output.recognize(file) if module == 'invoices' else inputs.recognize_pdf(file)
            match = output.find_existing_invoice_for_pdf(recognition) if module == 'invoices' else None
            handler.server.previews[token] = {'kind':'pdf', 'module':module, 'recognition':recognition, 'match':match,
                'path':str(file), 'fingerprint':fingerprint(), 'created':time.monotonic()}
            return {'token':token, 'recognition':recognition, 'match':match, 'name':name}
        except Exception:
            file.unlink(missing_ok=True)
            raise
    if path in ('/api/photos_upload','/api/upload_attachment'):
        if data.get('confirmed') is not True:
            raise ValueError('请确认附件分类与当前业务记录')
        def execute(ticket):
            files = []
            photos = path == '/api/photos_upload'
            sources = data['files'] if photos else [data]
            try:
                for item in sources:
                    _, file, name = handler.staged_upload(item)
                    files.append({'path':str(file), 'name':name})
                operation = 'photo_apply' if photos else 'attachment_apply'
                return extended.run(operation, {**data, 'files':files, 'fingerprint':ticket['fingerprint']})
            finally:
                for item in files:
                    Path(item['path']).unlink(missing_ok=True)
        return guarded_write(handler,path,data,execute)
    if path in ('/api/csv_apply','/api/evidence_apply','/api/pdf_apply'):
        if data.get('confirmed') is not True:
            raise ValueError('请明确核对并确认执行')
        kind = path.split('/')[-1].split('_')[0]
        preview = saved_preview(handler, data['token'], kind)
        if 'applied' in preview:
            return preview['applied']
        payload = dict(preview)
        if kind == 'evidence':
            payload.update(preview['record'])
        elif kind == 'pdf':
            payload.update(record=data.get('record') or {}, attach=bool(preview['match']))
        result = extended.run(kind+'_apply', payload)
        preview['applied'] = result
        if preview.get('path'):
            Path(preview['path']).unlink(missing_ok=True)
        return result
    raise ValueError('本地资料操作未开放')
