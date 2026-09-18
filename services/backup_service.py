"""Portable database and referenced attachment backup, with an explicit manifest."""

import hashlib
import json
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path

from db import connection
from db.backup import backup_database
from services.attachment_service import attachment_path


def create_backup_archive(destination):
    destination = Path(destination)
    with tempfile.TemporaryDirectory(prefix="operations_backup_") as folder:
        snapshot = backup_database(connection.DB_PATH, Path(folder)/"supplier_data.db")
        with closing(sqlite3.connect(snapshot)) as conn:
            paths = {row[0] for row in conn.execute("SELECT file_path FROM business_attachments")}
            paths.update(row[0] for row in conn.execute("SELECT file_path FROM construction_photos"))
        manifest = {"database": "supplier_data.db", "files": [], "missing_files": []}
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
                        manifest["files"].append({"original_path": stored_path, "archive_path": name})
                    archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                    archive.writestr("恢复说明.txt", "先退出程序并保留现有数据。supplier_data.db 为一致性快照；附件请按 manifest.json 中 original_path 恢复，files/ 是备份包内部路径。missing_files 为备份时缺失的文件。覆盖正式数据前先在隔离目录验证。")
            except Exception:
                output.close()
                destination.unlink(missing_ok=True)
                raise
        return manifest
