"""App-local backup maintenance. Never deletes older backups or business data."""

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path

from db import connection
from services.backup_service import create_backup_archive, inspect_backup_archive

logger = logging.getLogger(__name__)
_lock = threading.Lock()


def daily_backup(*, directory=None, now=None):
    now = now or datetime.now()
    root = Path(directory or os.environ.get('SUPPLY_CHAIN_BACKUP_DIR')
                or connection.DB_PATH.parent / 'backups' / 'automatic')
    root.mkdir(parents=True, exist_ok=True)
    with _lock:
        status_path = root / 'last_backup.json'
        if status_path.exists():
            try:
                state = json.loads(status_path.read_text(encoding='utf-8'))
                if (state.get('database') == str(connection.DB_PATH.resolve())
                        and now - datetime.fromisoformat(state['checked_at']) < timedelta(days=1)
                        and Path(state['archive']).is_file() and state.get('status') in ('verified', 'warning')):
                    return state
            except (ValueError, KeyError, TypeError):
                pass
        archive = root / f'engineering_{now:%Y%m%d_%H%M%S_%f}.zip'
        try:
            create_backup_archive(archive)
            check = inspect_backup_archive(archive)
            state = {'status': 'warning' if check['missing_count'] else 'verified',
                     'checked_at': now.isoformat(), 'archive': str(archive.resolve()),
                     'database': str(connection.DB_PATH.resolve()),
                     'file_count': check['file_count'], 'missing_count': check['missing_count']}
            if check['missing_count']:
                logger.warning('备份已验证，但原附件缺失 %s 个', check['missing_count'])
        except Exception as error:
            state = {'status': 'failed', 'checked_at': now.isoformat(), 'error': str(error),
                     'database': str(connection.DB_PATH.resolve())}
            logger.exception('自动备份失败；未删除任何既有备份')
        temporary = status_path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(status_path)
        return state


def start_backup_worker():
    """Check on startup and hourly while open; back up once per 24 hours."""
    stop = threading.Event()

    def run():
        while not stop.is_set():
            try:
                daily_backup()
            except Exception:
                logger.exception('无法运行备份维护，请检查目录权限和磁盘空间')
            stop.wait(3600)

    threading.Thread(target=run, name='database-backup', daemon=True).start()
    return stop
