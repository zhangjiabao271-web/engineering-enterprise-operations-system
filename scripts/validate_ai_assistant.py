import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path


def main():
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    source_db = project_root / "supplier_data.db"
    with tempfile.TemporaryDirectory(prefix="supply-chain-ai-") as temp_dir:
        test_db = Path(temp_dir) / "migration-rehearsal.db"
        shutil.copy2(source_db, test_db)

        from db.migration_runner import run_migrations
        from services import ai_conversation_service

        migration_result = run_migrations(test_db)
        conversation = ai_conversation_service.create_conversation(
            title="迁移验收对话",
            context={"time": {"label": "2026年"}},
            db_path=test_db,
        )
        ai_conversation_service.add_message(
            conversation["id"],
            "user",
            "迁移验收",
            db_path=test_db,
        )
        reloaded = ai_conversation_service.get_conversation(
            conversation["id"], db_path=test_db
        )

        conn = sqlite3.connect(test_db)
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
        versions = {
            row[0] for row in conn.execute("SELECT version FROM schema_migrations")
        }
        conn.close()

        if 230 not in versions:
            raise RuntimeError("迁移 230 未应用")
        if integrity != "ok":
            raise RuntimeError(f"数据库完整性检查失败：{integrity}")
        if foreign_keys:
            raise RuntimeError(f"外键检查失败：{foreign_keys[:5]}")
        if reloaded["context"]["time"]["label"] != "2026年":
            raise RuntimeError("会话上下文未能持久化")
        print(
            "AI assistant validation passed:",
            {"applied": migration_result["applied"], "integrity": integrity},
        )


if __name__ == "__main__":
    main()
