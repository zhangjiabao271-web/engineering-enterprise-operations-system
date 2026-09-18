"""Rehearse the cash-ledger migration without touching any production records."""
from contextlib import closing
import hashlib
from pathlib import Path
import sys
import tempfile


def fingerprint(conn,tables):
    result = {}
    for table in tables:
        rows = sorted(repr(tuple(row)) for row in conn.execute(f'SELECT * FROM "{table}"'))
        result[table] = (len(rows),hashlib.sha256('\n'.join(rows).encode()).hexdigest())
    return result


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root))
    from db.backup import backup_database
    from db.connection import DB_PATH,get_connection
    from db.migration_runner import run_migrations

    with tempfile.TemporaryDirectory(prefix='funds_upgrade_') as directory:
        snapshot = backup_database(DB_PATH,Path(directory)/'test.db')
        with closing(get_connection(snapshot)) as conn:
            version = conn.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]
            assert version==500, f'Rehearsal expects pre-funds schema 500, found {version}'
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name<>'schema_migrations'")]
            original = fingerprint(conn,tables)
        result = run_migrations(snapshot)
        assert result['applied']==[510],result
        with closing(get_connection(snapshot)) as conn:
            assert fingerprint(conn,tables)==original,'Original records changed'
            assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            assert not conn.execute('PRAGMA foreign_key_check').fetchall()
            new_tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'fund_%'")]
            assert len(new_tables)==6,new_tables
            assert all(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]==0 for name in new_tables)
        assert not run_migrations(snapshot)['applied']
        print(f'PASS: schema 500 -> 510; {len(tables)} original tables identical; 6 new tables empty; integrity OK; foreign keys OK; repeat migration no-op.')
        print('Production database was not upgraded or modified.')


if __name__=='__main__':
    main()
