import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Smoke-test supplement links and business attachments"
    )
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="business_attachments_") as temp_dir:
        temp_path = Path(temp_dir)
        test_database = temp_path / "supplier_data.db"
        attachment_path = temp_path / "attachments"
        source = temp_path / "test-contract.txt"
        source.write_text("contract attachment test", encoding="utf-8")
        shutil.copy2(args.database, test_database)
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        os.environ["SUPPLY_CHAIN_ATTACHMENTS_PATH"] = str(attachment_path)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        import database
        from db.connection import get_connection
        from services import attachment_service, contract_service

        database.init_db()
        parent_id = contract_service.create_contract(
            {
                "contract_no": "TEST-ATTACH-PARENT",
                "name": "附件测试原合同",
                "contract_type": "annual",
                "sign_date": "2026-07-30",
                "amount": "10000.00",
                "status": "active",
            }
        )
        supplement_id = contract_service.create_contract(
            {
                "contract_no": "TEST-ATTACH-SUPPLEMENT",
                "name": "附件测试补充协议",
                "contract_type": "supplement",
                "parent_contract_id": parent_id,
                "sign_date": "2026-07-31",
                "amount": "1000.00",
                "status": "active",
            }
        )
        supplement = contract_service.get_contract(supplement_id)
        assert supplement["parent_contract_id"] == parent_id
        assert supplement["parent_contract_no"] == "TEST-ATTACH-PARENT"

        attachment_id = attachment_service.add_attachment(
            "contract",
            parent_id,
            source,
            category="合同原件",
            description="临时数据库测试附件",
        )
        rows = attachment_service.list_attachments("contract", parent_id)
        assert len(rows) == 1
        assert rows[0]["id"] == attachment_id
        stored_file = Path(rows[0]["absolute_path"])
        assert stored_file.exists()
        assert stored_file.read_text(encoding="utf-8") == "contract attachment test"

        attachment_service.void_attachments([attachment_id])
        assert not attachment_service.list_attachments("contract", parent_id)
        assert attachment_service.list_attachments(
            "contract", parent_id, include_void=True
        )[0]["status"] == "void"
        assert stored_file.exists()

        conn = get_connection()
        try:
            assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            conn.close()

    print("Supplement contract and business attachment smoke test passed")


if __name__ == "__main__":
    main()
