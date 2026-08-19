import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


def _supplier_data(name):
    return {
        "name": name,
        "category": "钢材",
        "default_tax_rate_percent": "10",
        "contact": "测试联系人",
        "price_level": "中",
        "delivery": "一般",
        "quality": "良",
        "export": "否",
        "notes": "采购材料联想测试",
    }


def _offer_data(supplier_id, name, specification, price):
    return {
        "supplier_id": supplier_id,
        "name": name,
        "specification": specification,
        "unit": "吨",
        "price": price,
        "tax_rate_percent": "10",
        "notes": "采购材料联想测试",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Smoke-test formal procurement material typeahead"
    )
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="procurement_typeahead_") as temp_dir:
        test_database = Path(temp_dir) / "supplier_data.db"
        shutil.copy2(args.database, test_database)
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        import database
        from services import master_data_service
        from ui.typeahead import filter_supplier_offer_labels

        database.init_db()
        supplier_id = master_data_service.create_supplier(
            _supplier_data("联想测试钢材供应商甲")
        )
        other_supplier_id = master_data_service.create_supplier(
            _supplier_data("联想测试钢材供应商乙")
        )

        master_data_service.create_supplier_offer(
            _offer_data(supplier_id, "H型钢", "H300×300", "3500")
        )
        master_data_service.create_supplier_offer(
            _offer_data(supplier_id, "H型钢", "HW200×200", "3600")
        )
        master_data_service.create_supplier_offer(
            _offer_data(supplier_id, "槽钢", "10#", "3300")
        )
        master_data_service.create_supplier_offer(
            _offer_data(other_supplier_id, "H型钢", "H400×400", "3700")
        )

        offers = master_data_service.list_supplier_offers(supplier_id=supplier_id)
        assert len(offers) == 3
        assert all(offer["supplier_id"] == supplier_id for offer in offers)
        assert len(
            master_data_service.list_supplier_offers(
                supplier_id=supplier_id, keyword="H"
            )
        ) == 2

        offers_by_label = {
            f"{offer['name']} · {offer['specification']}": offer
            for offer in offers
        }
        h_matches = filter_supplier_offer_labels(offers_by_label, "h")
        assert len(h_matches) == 2
        assert all(label.startswith("H型钢") for label in h_matches)
        assert filter_supplier_offer_labels(offers_by_label, "300") == [
            "H型钢 · H300×300"
        ]
        assert filter_supplier_offer_labels(offers_by_label, "槽") == [
            "槽钢 · 10#"
        ]
        assert filter_supplier_offer_labels(offers_by_label, "不存在") == []

    print("Formal procurement material typeahead smoke test passed")


if __name__ == "__main__":
    main()
