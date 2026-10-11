"""Read-only production backup and strictly isolated real-ledger rehearsal setup."""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def ledger_digest(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        from services.web_workbench_operations import database_fingerprint
        return database_fingerprint(conn)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to((ROOT / 'qa').resolve()):
        raise ValueError('演练必须使用 qa 内全新目录，不能选择已有账本')
    source = ROOT / 'supplier_data.db'
    os.environ['SUPPLY_CHAIN_DB_PATH'] = str(source)
    from db.backup import backup_database
    from services import backup_service as backup
    from web_workbench.backup import validate_archive
    before = ledger_digest(source)
    output.mkdir(parents=True)
    safety = ROOT / 'backups' / ('desktop_cutover_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    safety.mkdir(parents=True)
    archive = safety / 'complete-before.zip'
    backup.create_backup_archive(archive, database=source)
    review = validate_archive(archive)
    extracted = output / 'extracted'
    extracted.mkdir()
    manifest = backup._extract_archive(archive, extracted)
    database = output / 'supplier_data.db'
    backup_database(extracted / 'supplier_data.db', database)
    snapshot = ledger_digest(database)
    if snapshot != before:
        raise ValueError('备份与当前账本逐表摘要不同，请核对期间是否有新录入；不切换')
    storage = output / 'attachments'
    storage.mkdir()
    mapping = {}
    for item in manifest['files']:
        packed = extracted / item['archive_path']
        target = storage / packed.name
        shutil.copy2(packed, target)
        if backup._sha256(target) != backup._sha256(packed):
            raise ValueError('演练附件复制核对失败')
        mapping[item['original_path']] = str(target)
    for original in manifest.get('missing_files', []):
        mapping[original] = str(storage / ('missing-' + hashlib.sha256(original.encode()).hexdigest()))
    with closing(sqlite3.connect(database)) as conn, conn:
        for table, column in (('business_attachments','file_path'), ('construction_photos','file_path'),
                              ('input_invoices','file_path'), ('cashflow_documents','source_path')):
            for original, target in mapping.items():
                conn.execute(f'UPDATE {table} SET {column}=? WHERE {column}=?', (target,original))
    # Every rehearsal write/restore points at this new tree, including old absolute paths.
    with closing(sqlite3.connect(database)) as conn:
        for original in backup.referenced_files(conn):
            if not Path(original).resolve().is_relative_to(storage):
                raise ValueError('演练仍有正式附件引用，不允许启动录入')
        integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
        violations = conn.execute('PRAGMA foreign_key_check').fetchall()
        counts = {table:conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                  for table in ('projects','receipts','historical_receipts','sales_invoices','purchase_orders','work_logs')}
    after = ledger_digest(source)
    if after != before or integrity != 'ok' or violations:
        raise ValueError('演练准备校验未通过，不切换')
    result = {'source_digest_before':before,'source_digest_after':after,'archive_snapshot_digest':snapshot,
              'rehearsal_baseline_digest':ledger_digest(database),'counts':counts,'archive_review':review,
              'backup_archive':str(archive),'rehearsal_database':str(database),'rehearsal_attachments':str(storage),
              'integrity':integrity,'foreign_key_violations':len(violations)}
    (output / 'preparation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    main()
