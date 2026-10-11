"""Rehearse additive migration 610; --apply requires a successful local rehearsal."""
import argparse
from contextlib import closing
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db.backup import backup_database
from db.migration_runner import run_migrations


def snapshot(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        conn.execute('BEGIN')
        tables = {}
        for name, sql in conn.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name<>'schema_migrations'"):
            quoted='"'+name.replace('"','""')+'"'
            rows=sorted(repr(tuple(row)) for row in conn.execute('SELECT * FROM '+quoted))
            tables[name]={'count':len(rows),'sha256':hashlib.sha256('\n'.join(rows).encode()).hexdigest(),'schema':sql}
        return tables


def verify(path, before):
    after=snapshot(path)
    changed=[table for table,value in before.items() if after.get(table)!=value]
    if changed:
        raise RuntimeError('Existing records or schema changed: '+', '.join(changed))
    with closing(sqlite3.connect(path)) as conn:
        integrity=conn.execute('PRAGMA integrity_check').fetchone()[0]
        foreign_keys=conn.execute('PRAGMA foreign_key_check').fetchall()
        version=conn.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]
        pending=conn.execute('SELECT COUNT(*) FROM record_entities').fetchone()[0]
        invoices=conn.execute('SELECT COUNT(*) FROM input_invoices').fetchone()[0]
    if integrity!='ok' or foreign_keys or version!=610 or pending or invoices:
        raise RuntimeError('Migration validation failed')
    return {'unchanged_tables':len(before),'unchanged_rows':sum(v['count'] for v in before.values()),
            'integrity':integrity,'foreign_key_errors':len(foreign_keys),'schema_version':version,
            'attributed_records':pending,'input_invoices':invoices}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    live=ROOT/'supplier_data.db'
    with closing(sqlite3.connect(live.as_uri()+'?mode=ro',uri=True)) as conn:
        version=conn.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]
    if version!=600:
        raise RuntimeError(f'Expected version 600, found {version}; review instead of rerunning')
    folder=ROOT/'backups'/('operating_entities_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    backup=backup_database(live,folder/'before.db')
    rehearsal=backup_database(backup,folder/'rehearsal.db')
    before=snapshot(backup)
    result=run_migrations(rehearsal,backup_keep=None)
    if result['applied']!=[610]:
        raise RuntimeError('Unexpected migrations: '+str(result))
    report={'backup':str(backup),'rehearsal':str(rehearsal),'rehearsal_validation':verify(rehearsal,before)}
    if args.apply:
        if snapshot(live)!=before:
            raise RuntimeError('Live data changed since backup; rehearse again')
        report['formal_migration']=run_migrations(live,backup_keep=None)
        report['formal_validation']=verify(live,before)
    (folder/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
