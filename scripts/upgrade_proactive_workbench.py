"""Rehearse migration 640 and verify every pre-existing row before enabling it."""
import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
NEW_TABLES = {
    'daily_self_checks', 'operating_attention_states',
    'operating_check_history', 'assistant_entry_drafts',
}


def snapshot(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.execute('BEGIN')
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        facts = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            condition = ' WHERE version<>640' if table == 'schema_migrations' else ''
            rows = conn.execute('SELECT * FROM ' + quoted + condition).fetchall()
            fingerprints = sorted(hashlib.sha256(repr(row).encode()).hexdigest() for row in rows)
            facts[table] = {'rows':len(rows), 'digest':hashlib.sha256(''.join(fingerprints).encode()).hexdigest()}
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('账本完整性检查未通过，不启用新版')
        if conn.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('账本存在外键异常，不启用新版')
        versions = [row[0] for row in conn.execute('SELECT version FROM schema_migrations ORDER BY version')]
        return {'tables':facts, 'versions':versions, 'integrity':'ok', 'foreign_key_violations':0}


def verify_unchanged(before, after):
    if set(after['tables']) - set(before['tables']) != NEW_TABLES:
        raise ValueError('升级新增结构不符合本轮范围')
    for table, facts in before['tables'].items():
        if after['tables'].get(table) != facts:
            raise ValueError('原有记录对账失败：' + table)
    if any(after['tables'][table]['rows'] for table in NEW_TABLES):
        raise ValueError('升级不应制造核对、跟进或录入草稿')
    if after['versions'] != before['versions'] + [640]:
        raise ValueError('迁移版本对账失败')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / 'qa'):
        raise ValueError('请选择qa下全新的演练目录')
    source = ROOT / 'supplier_data.db'
    from db.application_lock import ledger_writer
    from db.backup import backup_database
    from db.migrations import MIGRATIONS
    from db.migration_runner import run_migrations
    from services.backup_service import create_backup_archive, inspect_backup_archive

    with ledger_writer(source):
        before = snapshot(source)
        pending = [version for version, _, _ in MIGRATIONS if version not in before['versions']]
        if pending != [640]:
            raise ValueError('本轮只允许升级640；实际待迁移：' + str(pending))
        output.mkdir(parents=True)
        safety = ROOT / 'backups' / ('before_proactive_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        backup = backup_database(source, safety / 'before.db')
        archive = safety / 'complete-before.zip'
        create_backup_archive(archive, database=source)
        archive_review = inspect_backup_archive(archive)
        rehearsal = backup_database(backup, output / 'rehearsal.db')
        if snapshot(rehearsal) != before:
            raise ValueError('在线备份与正式账本不一致')
        run_migrations(rehearsal, backup_keep=None)
        verify_unchanged(before, snapshot(rehearsal))
        if snapshot(source) != before:
            raise ValueError('演练期间正式账本变化，停止升级')
        result = {'status':'rehearsal_passed', 'backup_database':str(backup),
                  'backup_archive':str(archive), 'archive_review':archive_review,
                  'rehearsal_database':str(rehearsal), 'baseline':before}
        if args.apply:
            run_migrations(source, backup_keep=None)
            after = snapshot(source)
            verify_unchanged(before, after)
            result.update(status='applied', after=after)
        (output / 'upgrade-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'status':result['status'], 'old_tables_verified':len(before['tables']),
                          'new_empty_tables':sorted(NEW_TABLES), 'backup_archive':str(archive),
                          'integrity':'ok','foreign_key_violations':0},ensure_ascii=False))


if __name__ == '__main__':
    main()
