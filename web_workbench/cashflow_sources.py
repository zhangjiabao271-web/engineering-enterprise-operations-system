"""Read-only source previews followed by scope-bound, explicit association."""

import secrets
import shutil
import time
from pathlib import Path

from services import document_cashflow_service as cashflow
from services.document_cashflow_extract import KINDS, digest, read_document
from services.document_understanding_service import understand_document

PREVIEW_PATHS = frozenset({'/api/cashflow/preview', '/api/cashflow/attachment_preview'})
SOURCE_PATHS = PREVIEW_PATHS | {'/api/cashflow/save_document', '/api/cashflow/link_attachment',
                               '/api/cashflow/discard'}


def _attachment(scope, attachment_id):
    row = next((row for row in cashflow.available_attachments(**scope)
                if row['id'] == attachment_id), None)
    if not row or not row['file_exists']:
        raise ValueError('附件不属于当前范围、已移出或原文件缺失，请刷新核对')
    return row


def _attachment_version(row):
    return digest({key: row[key] for key in ('id', 'file_path', 'updated_at', 'status')})


def _discard(ticket):
    if not ticket.get('attachment_id'):
        Path(ticket['source']['source_path']).unlink(missing_ok=True)


def preview_source(handler, path, data, scope):
    server = handler.server
    generation = server.token
    use_model = data.get('use_model', False)
    if type(use_model) is not bool:
        raise ValueError('资料模型授权须明确为布尔值')
    kind = data.get('kind', '合同正文')
    if kind not in KINDS:
        raise ValueError('资料类型无效')
    if use_model and not any(scope.values()):
        raise ValueError('请先选择资料要分析的合同或项目')
    with server.write_lock:
        for token, ticket in list(server.uploads.items()):
            if ticket.get('cashflow') and ticket['expires'] <= time.monotonic():
                _discard(ticket)
                server.uploads.pop(token)
        if sum(bool(ticket.get('cashflow')) and 'result' not in ticket for ticket in server.uploads.values()) >= 32:
            raise ValueError('待核对的资料预览过多，请先取消旧预览或稍后重试')
    attachment = None
    if path == '/api/cashflow/attachment_preview':
        attachment = _attachment(scope, int(data['attachment_id']))
        token = secrets.token_hex(24)
        file, name = Path(attachment['absolute_path']), attachment['original_name']
    else:
        token, file, name = handler.staged_upload(data)
    try:
        if scope['contract_id'] or scope['project_id']:
            cashflow.scope_label(**scope)
        if use_model:
            context, context_fingerprint = cashflow.understanding_context(**scope)
            source = understand_document(file, context=context, kind=kind, name=name)
            source['fragments'][0]['understanding']['scope_fingerprint'] = context_fingerprint
        else:
            source = {**read_document(file), 'name': name}
        with server.write_lock:
            if generation != server.token:
                raise ValueError('账本已恢复或会话已变化，请重新读取资料')
            if attachment and _attachment_version(_attachment(scope, attachment['id'])) != _attachment_version(attachment):
                raise ValueError('原附件在识别期间已变化，请刷新后重新读取')
            if digest(file.read_bytes()) != source['source_file_sha256']:
                raise ValueError('原资料在理解期间已变化，请重新读取')
            if use_model and cashflow.understanding_context(**scope)[1] != context_fingerprint:
                raise ValueError('项目、合同或经营主体在理解期间已变化，请重新读取')
            ticket = {'cashflow': True, 'source': source, 'generation': generation,
                      'scope': scope if attachment or any(scope.values()) else None,
                      'attachment_id': attachment['id'] if attachment else None,
                      'attachment_version': _attachment_version(attachment) if attachment else None,
                      'expires': time.monotonic() + 1800}
            server.uploads[token] = ticket
        return {'token': token, 'name': name, 'fragments': source['fragments'],
                'sha256': source['source_file_sha256'], 'recognition': source['recognition'],
                'understanding': source.get('understanding')}
    except Exception:
        if not attachment:
            file.unlink(missing_ok=True)
        raise


def post_source(handler, path, data, scope):
    server = handler.server
    if path in PREVIEW_PATHS:
        return preview_source(handler, path, data, scope)
    if path == '/api/cashflow/discard':
        ticket = server.uploads.get(data['token'])
        if ticket:
            if ticket.get('cashflow'):
                _discard(ticket)
            else:
                # Existing input-invoice previews use this same cancellation endpoint.
                temporary = Path(ticket['source_path']).resolve()
                if not temporary.is_relative_to(server.staging.resolve()):
                    raise ValueError('暂存文件范围异常，请保留资料并核对')
                temporary.unlink(missing_ok=True)
            server.uploads.pop(data['token'])
        return True
    if not data.get('token'):
        if path == '/api/cashflow/link_attachment':
            raise ValueError('请先读取并核对已有附件，再确认关联')
        # Manual/client text cannot impersonate a server-generated interpretation.
        entry = {key: data[key] for key in ('name', 'kind', 'change_reason') if key in data}
        fragments = data.get('fragments')
        if not isinstance(fragments, list) or any(not isinstance(fragment, dict) for fragment in fragments):
            raise ValueError('资料原文须为带来源位置的段落列表')
        entry['fragments'] = [{key: fragment.get(key) for key in ('locator', 'text')}
                              for fragment in fragments]
        return cashflow.save_document(entry, **scope)
    ticket = server.uploads.get(data['token'])
    if not ticket or not ticket.get('cashflow') or ticket['generation'] != server.token or ticket['expires'] <= time.monotonic():
        raise ValueError('资料预览已取消、过期或会话已变化，请重新读取')
    if ticket['scope'] is not None and ticket['scope'] != scope:
        raise ValueError('预览范围与当前合同/项目不一致，请重新读取')
    request_hash = digest({'path': path, 'scope': scope, 'kind': data.get('kind'),
                           'change_reason': data.get('change_reason', ''),
                           'acknowledged': data.get('acknowledged')})
    if 'result' in ticket:
        if request_hash != ticket['request_hash']:
            raise ValueError('本预览已确认，请重新读取后修改资料')
        return ticket['result']
    source = ticket['source']
    if (ticket['attachment_id'] or source.get('understanding') or source['recognition']['ocr_pages']) and data.get('acknowledged') is not True:
        raise ValueError('请对照原附件核对业务理解、金额及付款条件，并明确确认')
    target = None
    if ticket['attachment_id']:
        attachment = _attachment(scope, ticket['attachment_id'])
        if _attachment_version(attachment) != ticket['attachment_version']:
            raise ValueError('原附件信息已变化，请重新读取核对')
        entry = {**source, 'attachment_id': attachment['id']}
    else:
        from services.attachment_service import _storage_root
        directory = _storage_root() / 'cashflow'
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / (secrets.token_hex(20) + Path(source['source_path']).suffix)
        shutil.copyfile(source['source_path'], target)
        entry = {**source, 'source_path': str(target)}
    try:
        result = cashflow.save_document({**entry, 'kind': data['kind'],
                                       'change_reason': data.get('change_reason', '')}, **scope)
    except Exception:
        if target:
            target.unlink(missing_ok=True)
        raise
    if target and result['duplicate']:
        target.unlink()
    ticket.update(result=result, request_hash=request_hash)
    _discard(ticket)
    return result
