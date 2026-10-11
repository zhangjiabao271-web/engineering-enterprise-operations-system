"""Bounded archive transfer and explicitly confirmed, preflighted restoration."""

import base64
import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

MAX_ARCHIVE = 512 * 1024**2
CHUNK_SIZE = 4 * 1024**2


def fingerprint():
    from db.connection import db_read
    from services.web_workbench_operations import database_fingerprint
    with db_read() as conn:
        return database_fingerprint(conn)


def validate_archive(path):
    from db.migrations import MIGRATIONS
    from services import backup_service as backup
    with tempfile.TemporaryDirectory(prefix='web_restore_check_') as folder:
        manifest = backup._extract_archive(path, folder)
        backup._restore_targets(manifest)
        with closing(sqlite3.connect(Path(folder) / 'supplier_data.db')) as conn:
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or conn.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('备份数据库完整性或外键检查未通过')
            try:
                versions = {row[0] for row in conn.execute('SELECT version FROM schema_migrations')}
            except sqlite3.DatabaseError:
                raise ValueError('备份不是当前工程账本') from None
            # Versions 1 and 2 are the foundation migrations in db/schema.py.
            if versions != ({1, 2} | {row[0] for row in MIGRATIONS}):
                raise ValueError('备份版本与当前软件不一致，不能直接恢复；未升级或改写账本')
            if os.environ.get('SUPPLY_CHAIN_ATTACHMENTS_PATH'):
                # Missing references also must not reconnect an isolated copy to live files.
                backup._restore_targets({'files': [
                    {'original_path': value} for value in backup.referenced_files(conn)
                ]})
            return {'schema_version': max(versions), 'file_count': len(manifest['files']),
                    'missing_count': len(manifest.get('missing_files', [])),
                    'project_count': conn.execute('SELECT COUNT(*) FROM projects').fetchone()[0],
                    'receipt_count': conn.execute('SELECT COUNT(*) FROM receipts').fetchone()[0],
                    'historical_receipt_count': conn.execute('SELECT COUNT(*) FROM historical_receipts').fetchone()[0]}


def run(action, payload):
    from db.connection import DB_PATH
    environment = os.environ.copy()
    environment.update(SUPPLY_CHAIN_DB_PATH=str(DB_PATH), PYTHONIOENCODING='utf-8')
    result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-m', 'web_workbench.backup', action],
        input=json.dumps(payload, ensure_ascii=False), text=True, encoding='utf-8', capture_output=True,
        env=environment, cwd=Path(__file__).resolve().parents[1], timeout=300)
    try:
        response = json.loads(result.stdout.splitlines()[-1])
    except (ValueError, IndexError):
        raise ValueError('备份操作状态待核对，请保留文件并查看日志，不要重复提交恢复') from None
    if 'error' in response:
        raise ValueError(response['error'])
    return response['result']


def entry(server, token, kind):
    value = server.backups.get(token)
    if not value or value['kind'] != kind or time.monotonic() - value['created'] > 1800:
        raise ValueError('备份预览已取消或失效，请重新选择文件')
    return value


def get_api(handler, path, query):
    if path != '/api/backup/download':
        return False
    value = entry(handler.server, query.get('token'), 'download')
    handler.send_content(value['path'].read_bytes(), 'application/zip', attachment='工程账本与附件备份.zip')
    return True


def post_api(server, path, data):
    action = path.removeprefix('/api/backup/')
    if action == 'create':
        token = secrets.token_hex(24)
        target = server.staging / (token + '.zip')
        review = run('create', {'path': str(target)})
        server.backups[token] = {'kind': 'download', 'path': target, 'created': time.monotonic()}
        return {'token': token, 'review': review}
    if action == 'restore_start':
        size = data.get('size')
        if type(size) is not int or not 0 < size <= MAX_ARCHIVE or not str(data.get('name', '')).lower().endswith('.zip'):
            raise ValueError('请选择不超过512MB的账本备份 ZIP')
        token = secrets.token_hex(24)
        target = server.staging / (token + '.zip')
        with target.open('xb'):
            pass
        server.backups[token] = {'kind': 'restore', 'path': target, 'size': size, 'received': 0, 'created': time.monotonic()}
        return {'token': token, 'chunk_size': CHUNK_SIZE}
    value = entry(server, data.get('token'), 'restore')
    if action == 'restore_discard':
        if 'result' not in value:
            value['path'].unlink(missing_ok=True)
        server.backups.pop(data['token'], None)
        return True
    if action == 'restore_chunk':
        if 'review' in value:
            raise ValueError('文件已进入恢复预览，不能再修改')
        offset = data.get('offset')
        raw = base64.b64decode(data.get('content', ''), validate=True)
        if type(offset) is not int or not 0 < len(raw) <= CHUNK_SIZE or offset < 0 or offset + len(raw) > value['size']:
            raise ValueError('备份上传分片无效')
        if offset < value['received']:
            with value['path'].open('rb') as source:
                source.seek(offset)
                if source.read(len(raw)) == raw:
                    return {'received': value['received']}
            raise ValueError('重复上传内容不一致，请重新选择备份')
        if offset != value['received']:
            raise ValueError('备份分片顺序不正确')
        with value['path'].open('ab') as target:
            target.write(raw)
        value['received'] += len(raw)
        return {'received': value['received']}
    if action == 'restore_preview':
        if value['received'] != value['size']:
            raise ValueError('备份文件尚未上传完整')
        review = run('preview', {'path': str(value['path'])})
        value.update(review=review, fingerprint=fingerprint(), sha256=file_hash(value['path']))
        return {'review': review}
    if action == 'restore_apply':
        if server.read_only:
            raise ValueError('当前只读，不能恢复覆盖账本')
        if data.get('confirmed') is not True or data.get('confirmation') != '恢复该账本':
            raise ValueError('请核对覆盖范围并输入“恢复该账本”')
        if 'result' in value:
            return value['result']
        if 'review' not in value or value['sha256'] != file_hash(value['path']) or value['fingerprint'] != fingerprint():
            raise ValueError('文件或当前账本在预览后已变化，请重新预览；未执行恢复')
        server.assistant.close()
        try:
            result = run('restore', {'path': str(value['path']), 'fingerprint': value['fingerprint']})
        except Exception:
            # Ambiguous failures never leave a writable UI suggesting another attempt.
            server.read_only = True
            server.previews.clear()
            raise
        from web_workbench.assistant import WebAssistant
        server.assistant = WebAssistant()
        server.previews.clear()
        server.uploads.clear()
        server.token = secrets.token_urlsafe(32)
        value['result'] = result
        return result
    raise ValueError('备份操作不存在')


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def worker(action, payload):
    from db.connection import DB_PATH
    from services import backup_service as backup
    path = Path(payload['path'])
    if action == 'create':
        backup.create_backup_archive(path)
        return validate_archive(path)
    if action == 'preview':
        return validate_archive(path)
    if action == 'restore':
        validate_archive(path)
        if fingerprint() != payload['fingerprint']:
            raise ValueError('当前账本已变化，未执行恢复')
        return backup.restore_backup_archive(path, safety_backup_dir=DB_PATH.parent / 'backups')
    raise ValueError('未知备份操作')


if __name__ == '__main__':
    try:
        print(json.dumps({'result': worker(sys.argv[1], json.load(sys.stdin))}, ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        raise SystemExit(1)
