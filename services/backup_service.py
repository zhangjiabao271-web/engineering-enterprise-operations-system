"""Portable database and referenced attachment backup, with an explicit manifest."""

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path, PurePosixPath

from db import connection
from db.backup import backup_database
from db.connection import PROJECT_ROOT
from services.attachment_service import attachment_path


def _restore_targets(manifest):
    """Validate every destination before any live database or file is changed."""
    from services.attachment_service import _storage_root

    # An explicitly isolated attachment store must never restore into the live one.
    roots = [_storage_root().resolve()]
    if not os.environ.get('SUPPLY_CHAIN_ATTACHMENTS_PATH'):
        roots.append((PROJECT_ROOT / "attachments").resolve())
    targets = []
    seen = set()
    for entry in manifest["files"]:
        original = entry.get("original_path")
        if not isinstance(original, str) or not original.strip():
            raise ValueError("备份附件目标路径无效，未执行恢复")
        target = attachment_path(original).resolve()
        if not any(target != root and target.is_relative_to(root) for root in roots):
            raise ValueError("备份附件目标必须位于附件目录内，未执行恢复")
        if target in seen or (target.exists() and not target.is_file()):
            raise ValueError("备份附件目标重复或不是文件，未执行恢复")
        seen.add(target)
        targets.append(target)
    return targets


def _sha256(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def referenced_files(conn):
    """Include retained evidence and cashflow source files, not just active rows."""
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    paths = set()
    for table, column in (('business_attachments', 'file_path'), ('construction_photos', 'file_path'),
                          ('input_invoices', 'file_path'), ('cashflow_documents', 'source_path')):
        if table in tables:
            paths.update(row[0] for row in conn.execute(
                f'SELECT {column} FROM {table} WHERE {column} IS NOT NULL AND trim({column})<>\'\''))
    return paths


def create_backup_archive(destination, *, database=None):
    destination = Path(destination)
    with tempfile.TemporaryDirectory(prefix="operations_backup_") as folder:
        snapshot = backup_database(database or connection.DB_PATH, Path(folder)/"supplier_data.db")
        with closing(sqlite3.connect(snapshot)) as conn:
            paths = referenced_files(conn)
        manifest = {"version": 2, "database": "supplier_data.db", "database_sha256": _sha256(snapshot),
                    "files": [], "missing_files": []}
        # Exclusive creation protects earlier backups even when a caller bypasses the dialog.
        with destination.open("xb") as output:
            try:
                with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.write(snapshot, "supplier_data.db")
                    for stored_path in sorted(paths):
                        source = attachment_path(stored_path)
                        if not source.is_file():
                            manifest["missing_files"].append(stored_path)
                            continue
                        name = "files/" + hashlib.sha256(stored_path.encode("utf-8")).hexdigest() + source.suffix
                        archive.write(source, name)
                        manifest["files"].append({"original_path": stored_path, "archive_path": name,
                                                  "sha256": _sha256(source)})
                    archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                    archive.writestr("恢复说明.txt", "推荐在程序“导入导出”页使用“恢复备份”一键恢复（恢复前会自动备份当前库）。手工恢复：先退出程序并保留现有数据，supplier_data.db 为一致性快照；附件按 manifest.json 中 original_path 恢复，files/ 是备份包内部路径，missing_files 为备份时缺失的文件。覆盖正式数据前先在隔离目录验证。")
            except Exception:
                output.close()
                destination.unlink(missing_ok=True)
                raise
        return manifest


def _extract_archive(source, folder):
    """只解出备份包约定成员；拒绝异常路径与结构。"""
    folder = Path(folder)
    with zipfile.ZipFile(source) as archive:
        members = archive.infolist()
        if len(members) > 100000 or sum(item.file_size for item in members) > 8 * 1024**3:
            raise ValueError('备份包解压规模超过安全限制')
        if any(item.file_size > 2 * 1024**3 or item.flag_bits & 1 for item in members):
            raise ValueError('备份包含过大或加密成员')
        names = set(archive.namelist())
        if len(names) != len(archive.namelist()):
            raise ValueError("备份包含重复文件")
        if "supplier_data.db" not in names or "manifest.json" not in names:
            raise ValueError("备份包缺少数据库或清单文件")
        if archive.getinfo('manifest.json').file_size > 20 * 1024**2:
            raise ValueError('备份清单过大')
        for name in names:
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts or "\\" in name or ":" in name:
                raise ValueError(f"备份包含非法路径：{name}")
            if name == "supplier_data.db" or name in ("manifest.json", "恢复说明.txt"):
                archive.extract(name, folder)
            elif name.startswith("files/") and not name.endswith("/"):
                if len(pure.parts) != 2:
                    raise ValueError("备份附件路径层级无效")
                target = folder / "files" / pure.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("database") != "supplier_data.db" or not isinstance(manifest.get("files"), list):
        raise ValueError("备份包清单格式无效")
    if manifest.get('database_sha256') and _sha256(folder / 'supplier_data.db') != manifest['database_sha256']:
        raise ValueError('备份数据库校验码不匹配')
    for entry in manifest['files']:
        if not isinstance(entry, dict):
            raise ValueError('附件清单格式无效')
        member = PurePosixPath(entry.get('archive_path', ''))
        if (len(member.parts) != 2 or member.parts[0] != 'files' or '..' in member.parts
                or '\\' in str(member) or ':' in str(member)):
            raise ValueError('附件清单路径无效')
        packed = folder / 'files' / member.name
        if not packed.is_file():
            raise ValueError('备份缺少清单中的附件')
        if entry.get('sha256') and _sha256(packed) != entry['sha256']:
            raise ValueError('备份附件校验码不匹配')
    return manifest


def inspect_backup_archive(source):
    """读取备份包内容并做只读校验，供恢复前确认。"""
    with tempfile.TemporaryDirectory(prefix="operations_restore_check_") as folder:
        manifest = _extract_archive(source, folder)
        db_file = Path(folder) / "supplier_data.db"
        with closing(sqlite3.connect(db_file)) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise ValueError(f"备份数据库完整性检查失败：{integrity}")
            if conn.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('备份数据库存在外键异常')
            try:
                version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            except sqlite3.OperationalError:
                version = None
            project_count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
        return {
            "manifest": manifest,
            "schema_version": version,
            "table_count": project_count,
            "file_count": len(manifest["files"]),
            "missing_count": len(manifest.get("missing_files", [])),
        }


def _restore_database(snapshot):
    with closing(sqlite3.connect(Path(snapshot).resolve().as_uri() + '?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(connection.DB_PATH, timeout=30)) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('恢复后完整性检查失败')
            if dst.execute('PRAGMA foreign_key_check').fetchone():
                raise RuntimeError('恢复后外键检查失败')


def _replace_attachment(source, target):
    """Stage beside the destination so failure cannot truncate its old contents."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix='.restore_', dir=target.parent, delete=False) as handle:
        staged = Path(handle.name)
    try:
        shutil.copyfile(source, staged)
        staged.replace(target)
    finally:
        staged.unlink(missing_ok=True)


def _preserve_restore_state(targets, safety_dir):
    files_dir = safety_dir / 'files'
    files_dir.mkdir()
    entries = []
    for index, target in enumerate(targets):
        saved = files_dir / str(index) if target.exists() else None
        if saved:
            shutil.copy2(target, saved)
        entries.append({'target': str(target), 'before': str(saved) if saved else None})
    (safety_dir / 'rollback_manifest.json').write_text(
        json.dumps({'database': str(connection.DB_PATH), 'files': entries}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    return entries


def restore_backup_archive(source, *, safety_backup_dir=None):
    """把备份包恢复到当前数据库与附件目录。

    校验完整包 → 保全当前库及目标附件 → 在线恢复库及原子替换附件。
    中途失败则回滚两类资源；回滚失败时保留安全副本和逐文件恢复清单。
    不删除备份包之外已存在的附件。
    """
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    with tempfile.TemporaryDirectory(prefix="operations_restore_") as folder:
        manifest = _extract_archive(source, folder)
        snapshot = Path(folder) / "supplier_data.db"
        targets = _restore_targets(manifest)
        with closing(sqlite3.connect(snapshot)) as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("备份数据库完整性检查失败，未执行恢复")
            if conn.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("备份数据库存在外键异常，未执行恢复")

        # 覆盖前先给当前库留安全副本
        safety_root = Path(safety_backup_dir) if safety_backup_dir else PROJECT_ROOT / "backups"
        safety_root.mkdir(parents=True, exist_ok=True)
        safety_dir = Path(tempfile.mkdtemp(prefix=f'restore_safety_{datetime.now():%Y%m%d_%H%M%S}_', dir=safety_root))
        safety_copy = backup_database(connection.DB_PATH, safety_dir / "before.db")
        previous_files = _preserve_restore_state(targets, safety_dir)

        restored_files, touched = [], []
        try:
            _restore_database(snapshot)
            for entry, resolved, prior in zip(manifest['files'], targets, previous_files):
                packed = Path(folder) / 'files' / PurePosixPath(entry['archive_path']).name
                touched.append(prior)
                _replace_attachment(packed, resolved)
                restored_files.append(entry['original_path'])
        except Exception as error:
            rollback_errors = []
            for prior in reversed(touched):
                target = Path(prior['target'])
                try:
                    if prior['before']:
                        _replace_attachment(Path(prior['before']), target)
                    else:
                        target.unlink(missing_ok=True)
                except Exception as rollback_error:
                    rollback_errors.append(f'附件 {target}: {rollback_error}')
            try:
                _restore_database(safety_copy)
            except Exception as rollback_error:
                rollback_errors.append(f'数据库: {rollback_error}')
            if rollback_errors:
                raise RuntimeError(
                    f'恢复失败，自动回滚未完成，请停止录入并核对数据。安全材料保留于 {safety_dir}；'
                    'before.db 是恢复前数据库，rollback_manifest.json 标明各附件原件及原本不存在的路径。'
                    '请关闭应用后使用这些材料恢复，勿重复提交恢复。未完成项：' + '；'.join(rollback_errors)
                ) from error
            raise RuntimeError(f'恢复失败，已回滚数据库和本次涉及的附件。安全副本：{safety_dir}。原因：{error}') from error

        return {
            "safety_backup": str(safety_copy),
            "restored_files": restored_files,
            "skipped_files": [],
            "missing_files": list(manifest.get("missing_files", [])),
        }
