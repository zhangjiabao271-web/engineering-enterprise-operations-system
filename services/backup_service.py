"""Portable database and referenced attachment backup, with an explicit manifest."""

import hashlib
import json
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

    roots = [(PROJECT_ROOT / "attachments").resolve(), _storage_root().resolve()]
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
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup_archive(destination):
    destination = Path(destination)
    with tempfile.TemporaryDirectory(prefix="operations_backup_") as folder:
        snapshot = backup_database(connection.DB_PATH, Path(folder)/"supplier_data.db")
        with closing(sqlite3.connect(snapshot)) as conn:
            paths = {row[0] for row in conn.execute("SELECT file_path FROM business_attachments")}
            paths.update(row[0] for row in conn.execute("SELECT file_path FROM construction_photos"))
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='input_invoices'").fetchone():
                paths.update(row[0] for row in conn.execute(
                    "SELECT file_path FROM input_invoices WHERE file_path IS NOT NULL"))
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
        names = set(archive.namelist())
        if len(names) != len(archive.namelist()):
            raise ValueError("备份包含重复文件")
        if "supplier_data.db" not in names or "manifest.json" not in names:
            raise ValueError("备份包缺少数据库或清单文件")
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
    if manifest.get("database") != "supplier_data.db" or not isinstance(manifest.get("files"), list):
        raise ValueError("备份包清单格式无效")
    if manifest.get('database_sha256') and _sha256(folder / 'supplier_data.db') != manifest['database_sha256']:
        raise ValueError('备份数据库校验码不匹配')
    for entry in manifest['files']:
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


def restore_backup_archive(source, *, safety_backup_dir=None):
    """把备份包恢复到当前数据库与附件目录。

    顺序：解包到隔离目录并校验 → 在线备份当前库到 backups/restore_safety_<时间戳>/ →
    用 SQLite 备份接口把快照写回当前库 → 按清单还原附件。
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
        safety_dir = safety_root / f"restore_safety_{datetime.now():%Y%m%d_%H%M%S}"
        safety_copy = backup_database(connection.DB_PATH, safety_dir / "before.db")

        # 用备份接口把快照内容写回当前库，避免直接替换正在使用的文件
        with closing(sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True)) as src:
            dst = sqlite3.connect(connection.DB_PATH, timeout=30)
            try:
                src.backup(dst)
                if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("恢复后完整性检查失败")
            finally:
                dst.close()

        restored_files, skipped_files = [], []
        for entry, resolved in zip(manifest["files"], targets):
            original = entry.get("original_path", "")
            packed = Path(folder) / "files" / PurePosixPath(entry.get("archive_path", "")).name
            if not packed.is_file():
                skipped_files.append(original)
                continue
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_bytes(packed.read_bytes())
            restored_files.append(original)

        return {
            "safety_backup": str(safety_copy),
            "restored_files": restored_files,
            "skipped_files": skipped_files,
            "missing_files": list(manifest.get("missing_files", [])),
        }
