"""Read-only evidence for desktop cutover; never creates a business record."""

import argparse
import json
import socket
import sqlite3
import sys
import tempfile
import urllib.request
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.web_workbench_operations import database_fingerprint


def inspect_ledger(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        conn.row_factory = sqlite3.Row
        return {
            'digest': database_fingerprint(conn),
            'schema_version': conn.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0],
            'integrity': conn.execute('PRAGMA integrity_check').fetchone()[0],
            'foreign_key_violations': len(conn.execute('PRAGMA foreign_key_check').fetchall()),
            'counts': {table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                       for table in ('projects', 'receipts', 'historical_receipts',
                                     'sales_invoices', 'purchase_orders', 'work_logs')},
        }


def inspect_archive(path, expected_digest):
    from services.backup_service import _extract_archive
    with tempfile.TemporaryDirectory(prefix='desktop_cutover_check_') as directory:
        manifest = _extract_archive(path, directory)
        ledger = inspect_ledger(Path(directory) / 'supplier_data.db')
    if ledger['integrity'] != 'ok' or ledger['foreign_key_violations']:
        raise ValueError('下载备份完整性或外键检查未通过')
    if ledger['digest'] != expected_digest:
        raise ValueError('下载备份与隔离账本基线不一致')
    return {'path': str(path.resolve()), 'bytes': path.stat().st_size, 'digest': ledger['digest'],
            'schema_version': ledger['schema_version'], 'file_count': len(manifest['files']),
            'missing_count': len(manifest.get('missing_files', []))}


def inspect_origin(origin):
    parsed = urlsplit(origin)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port:
        raise ValueError('验收只接受本机工程工作台地址')
    with urllib.request.urlopen(origin.rstrip('/') + '/api/bootstrap', timeout=10) as response:
        boot = json.load(response)
    # Do not persist the session token, model settings, or customer data.
    return {key: boot[key] for key in ('read_only', 'demo', 'rehearsal')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preparation', type=Path, required=True)
    archives = parser.add_mutually_exclusive_group(required=True)
    archives.add_argument('--native-archive', type=Path)
    archives.add_argument('--browser-archive', type=Path)
    parser.add_argument('--matrix', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--origin')
    parser.add_argument('--expected-mode', choices=('live', 'rehearsal'), default='live')
    parser.add_argument('--closed-port', type=int, action='append', default=[])
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / 'qa').resolve()) or output.exists():
        raise ValueError('验收证据须保存到 qa 内新文件，不覆盖旧证据')
    preparation = json.loads(args.preparation.read_text(encoding='utf-8'))
    live = inspect_ledger(ROOT / 'supplier_data.db')
    rehearsal = inspect_ledger(Path(preparation['rehearsal_database']))
    if live['digest'] != preparation['source_digest_before']:
        raise ValueError('正式账本已变化，须重新核对，不能沿用旧切换结论')
    if rehearsal['digest'] != preparation['rehearsal_baseline_digest']:
        raise ValueError('隔离副本未回到基线')
    for ledger in (live, rehearsal):
        if ledger['integrity'] != 'ok' or ledger['foreign_key_violations']:
            raise ValueError('账本完整性或外键检查未通过')
    matrix = json.loads(args.matrix.read_text(encoding='utf-8'))
    if (len(matrix['matrix']) != matrix['cases']
            or any(row['status'] != 'passed' for row in matrix['matrix'])
            or matrix['errors'] or matrix['external_requests']):
        raise ValueError('浏览器完整写入验收未全部通过')
    closed = {}
    for port in args.closed_port:
        with socket.socket() as client:
            client.settimeout(.3)
            closed[str(port)] = client.connect_ex(('127.0.0.1', port)) != 0
        if not closed[str(port)]:
            raise ValueError(f'验收窗口端口 {port} 尚未释放')
    evidence = {
        'verified_at': datetime.now().isoformat(timespec='seconds'),
        'scope': 'Same local ledger; no production business writes or migrations',
        'production': live, 'rehearsal': rehearsal,
        'before_backup': preparation['backup_archive'],
        'browser_matrix': {'path': str(args.matrix.resolve()), 'passed': matrix['cases'],
                           'errors': 0, 'external_requests': 0},
        'closed_ports': closed,
    }
    archive_kind = 'native_backup' if args.native_archive else 'browser_backup'
    evidence[archive_kind] = inspect_archive(args.native_archive or args.browser_archive, rehearsal['digest'])
    if args.origin:
        evidence['native_bootstrap'] = inspect_origin(args.origin)
        expected = {'read_only': False, 'demo': False,
                    'rehearsal': args.expected_mode == 'rehearsal'}
        if evidence['native_bootstrap'] != expected:
            raise ValueError('原生窗口的账本或录入模式与验收要求不一致')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(evidence, ensure_ascii=False))


if __name__ == '__main__':
    main()
