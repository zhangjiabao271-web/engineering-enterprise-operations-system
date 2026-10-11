"""Shared startup and lifecycle for the browser and native workbench."""

import os
import logging
import sqlite3
import sys
import threading
from contextlib import closing, contextmanager, nullcontext
from datetime import datetime
from pathlib import Path


def prepare_database(*, db=None, demo=None, allow_write=False):
    """Select one ledger before importing services. Never upgrade an existing DB."""
    if (db is None) == (demo is None):
        raise ValueError('请选择一个现有账本或全新演示目录')
    if demo is not None:
        directory = Path(demo).resolve()
        path = directory / 'supplier_data.db'
        if path.exists():
            raise ValueError('演示目录已有数据库，请选择全新目录以保护已有资料')
        directory.mkdir(parents=True, exist_ok=True)
        os.environ['SUPPLY_CHAIN_DB_PATH'] = str(path)
        os.environ['SUPPLY_CHAIN_ATTACHMENTS_PATH'] = str(directory / 'attachments')
        from db.schema import init_db
        from web_workbench.synthetic import seed
        init_db()
        seed()
        return path

    path = Path(db).resolve()
    if not path.is_file():
        raise ValueError('数据库不存在；工作台不会新建或覆盖正式库')
    os.environ['SUPPLY_CHAIN_DB_PATH'] = str(path)
    from db.migrations import MIGRATIONS
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
            versions = {row[0] for row in conn.execute('SELECT version FROM schema_migrations')}
    except sqlite3.DatabaseError as error:
        raise ValueError('所选文件不是可用的工程账本，请核对原文件；未自动修改') from error
    pending = [version for version, _, _ in MIGRATIONS if version not in versions]
    if pending:
        raise ValueError(f'数据库有待执行迁移 {pending}；工作台不会自动执行，请先在隔离副本验证并单独确认升级')
    if allow_write:
        from db.backup import backup_database
        from services.backup_service import create_backup_archive, inspect_backup_archive
        backup = path.parent / 'backups' / ('before_web_write_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.db')
        backup_database(path, backup)
        archive = backup.with_suffix('.zip')
        create_backup_archive(archive, database=path)
        check = inspect_backup_archive(archive)
        if check['missing_count']:
            logging.warning('启动备份原附件缺失 %s 个；请在数据导入导出页核对', check['missing_count'])
        if sys.stdout:
            print('启用录入前已创建数据库及完整附件备份：' + str(archive), flush=True)
            print('请停止同一账本的其他录入；附件原文件仍须保留。', flush=True)
    return path


@contextmanager
def local_server(*, read_only, demo, port=0):
    """The window owns the server; every exit path releases its socket."""
    from web_workbench.server import WorkbenchServer
    from db.application_lock import ledger_writer
    from db.connection import DB_PATH
    lock = nullcontext() if read_only else ledger_writer(DB_PATH)
    with lock:
        server = WorkbenchServer(port, read_only=read_only, demo=demo)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .1},
                                  name='workbench-http', daemon=True)
        try:
            thread.start()
            yield server
        finally:
            if thread.ident is not None:
                server.shutdown()
                thread.join(timeout=3)
            server.server_close()
