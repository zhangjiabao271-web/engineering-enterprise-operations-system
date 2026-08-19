import argparse
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path


class FakeClient:
    def __init__(self, answer="已按近似名称匹配到岩棉瓦楞板。"):
        self.answer = answer
        self.messages = None

    def chat_completion(self, messages, **_kwargs):
        self.messages = messages
        return self.answer


def _project(name, code):
    return {
        "project_code": code,
        "name": name,
        "customer_name": "知识检索测试客户",
        "status": "进行中",
        "notes": "AI 知识检索隔离测试",
    }


def _purchase(
    project_id,
    name,
    quantity,
    unit,
    specification="",
    purchase_date=None,
    supplier_name="知识检索测试供应商",
    freight_amount_cents=1000,
):
    return (
        {
            "purchase_type": "正式采购",
            "project_id": project_id,
            "supplier_id": None,
            "merchant_name_snapshot": supplier_name,
            "purchase_date": purchase_date or date.today().isoformat(),
            "freight_amount_cents": freight_amount_cents,
            "notes": "AI 知识检索测试",
        },
        {
            "product_id": None,
            "material_name_snapshot": name,
            "specification_snapshot": specification,
            "unit_snapshot": unit,
            "quantity": quantity,
            "unit_price_cents": 10000,
            "line_amount_cents": round(quantity * 10000),
            "purpose": "测试车间",
        },
    )


def main():
    parser = argparse.ArgumentParser(
        description="Smoke-test natural-language retrieval of procurement facts"
    )
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="ai_knowledge_") as temp_dir:
        test_database = Path(temp_dir) / "supplier_data.db"
        shutil.copy2(args.database, test_database)
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        import database
        import ai_engine
        from services import business_knowledge_service, procurement_service, project_service

        database.init_db()
        first_project = project_service.create_project(
            _project("知识检索甲项目", "AI-KNOWLEDGE-A")
        )
        second_project = project_service.create_project(
            _project("知识检索乙项目", "AI-KNOWLEDGE-B")
        )

        for purchase in (
            _purchase(first_project, "岩棉瓦楞板", 12, "张", "50mm"),
            _purchase(first_project, "岩棉瓦楞板", 8, "张", "75mm"),
            _purchase(first_project, "岩棉瓦楞板", 3.5, "平方米", "100mm"),
            _purchase(first_project, "彩钢板", 999, "张", "干扰项"),
            _purchase(second_project, "岩棉瓦楞板", 100, "张", "范围外"),
        ):
            procurement_service.add_purchase_order(*purchase)

        scoped = business_knowledge_service.retrieve_procurement_knowledge(
            "岩棉板目前买了多少",
            project_id=first_project,
        )
        assert scoped["status"] == "matched"
        assert not scoped["requires_confirmation"]
        candidate = scoped["candidates"][0]
        assert candidate["standard_name"] == "岩棉瓦楞板"
        quantities = {
            item["unit"]: item["quantity"] for item in candidate["quantity_by_unit"]
        }
        assert quantities == {"平方米": "3.5", "张": "20"}
        assert candidate["record_count"] == 3
        assert all(row["project"] == "知识检索甲项目" for row in candidate["details"])
        assert all(row["standard_name"] != "彩钢板" for row in candidate["details"])

        company = business_knowledge_service.retrieve_procurement_knowledge(
            "岩棉板目前买了多少"
        )
        company_candidate = next(
            item for item in company["candidates"]
            if item["standard_name"] == "岩棉瓦楞板"
        )
        company_quantities = {
            item["unit"]: item["quantity"]
            for item in company_candidate["quantity_by_unit"]
        }
        assert company_quantities == {"平方米": "3.5", "张": "120"}

        current_year = date.today().year
        previous_year = current_year - 1
        procurement_service.add_purchase_order(
            *_purchase(
                first_project,
                "岩棉瓦楞板",
                50,
                "张",
                "去年采购，不应计入今年",
                purchase_date=f"{previous_year}-06-01",
            )
        )
        first_this_month = date.today().replace(day=1)
        previous_month_date = first_this_month - timedelta(days=1)
        procurement_service.add_purchase_order(
            *_purchase(
                first_project,
                "岩棉瓦楞板",
                7,
                "张",
                "上个月采购",
                purchase_date=previous_month_date.isoformat(),
            )
        )
        this_year = business_knowledge_service.retrieve_procurement_knowledge(
            "今年买了多少岩棉板",
            project_id=first_project,
        )
        assert this_year["status"] == "matched"
        assert this_year["material_query"] == "岩棉板"
        assert this_year["scope"]["time"]["code"] == "current_year"
        assert this_year["scope"]["time"]["label"] == f"{current_year}年"
        this_year_quantities = {
            item["unit"]: item["quantity"]
            for item in this_year["candidates"][0]["quantity_by_unit"]
        }
        assert this_year_quantities == {"平方米": "3.5", "张": "27"}

        previous_year_result = (
            business_knowledge_service.retrieve_procurement_knowledge(
                "去年买了多少岩棉板",
                project_id=first_project,
            )
        )
        assert previous_year_result["scope"]["time"]["code"] == "previous_year"
        assert previous_year_result["candidates"][0]["quantity_by_unit"] == [
            {"unit": "张", "quantity": "50"}
        ]

        this_month = business_knowledge_service.retrieve_procurement_knowledge(
            "这个月岩棉板买了多少",
            project_id=first_project,
        )
        assert this_month["scope"]["time"]["code"] == "current_month"
        assert this_month["candidates"][0]["quantity_by_unit"] == [
            {"unit": "平方米", "quantity": "3.5"},
            {"unit": "张", "quantity": "20"},
        ]

        previous_month = business_knowledge_service.retrieve_procurement_knowledge(
            "上个月买了多少岩棉板",
            project_id=first_project,
        )
        assert previous_month["scope"]["time"]["code"] == "previous_month"
        assert previous_month["candidates"][0]["quantity_by_unit"] == [
            {"unit": "张", "quantity": "7"}
        ]

        procurement_service.add_purchase_order(
            *_purchase(first_project, "岩棉夹芯板", 5, "张", "冲突候选")
        )
        ambiguous = business_knowledge_service.retrieve_procurement_knowledge(
            "岩棉板买了多少",
            project_id=first_project,
        )
        assert ambiguous["status"] == "ambiguous"
        assert ambiguous["requires_confirmation"]
        assert {item["standard_name"] for item in ambiguous["candidates"]} == {
            "岩棉瓦楞板",
            "岩棉夹芯板",
        }

        not_found = business_knowledge_service.retrieve_procurement_knowledge(
            "铜管买了多少",
            project_id=first_project,
        )
        assert not_found["status"] == "not_found"
        assert not not_found["requires_confirmation"]

        suffix_project = project_service.create_project(
            _project("知识检索省略后缀项目", "AI-KNOWLEDGE-SUFFIX")
        )
        procurement_service.add_purchase_order(
            *_purchase(suffix_project, "岩棉瓦楞", 6, "张", "台账未写板字")
        )
        omitted_suffix = business_knowledge_service.retrieve_procurement_knowledge(
            "岩棉板目前买了多少",
            project_id=suffix_project,
        )
        assert omitted_suffix["status"] == "matched"
        assert omitted_suffix["candidates"][0]["standard_name"] == "岩棉瓦楞"
        assert omitted_suffix["candidates"][0]["quantity_by_unit"] == [
            {"unit": "张", "quantity": "6"}
        ]

        supplier_project_a = project_service.create_project(
            _project("供应商语义甲项目", "AI-SUPPLIER-A")
        )
        supplier_project_b = project_service.create_project(
            _project("供应商语义乙项目", "AI-SUPPLIER-B")
        )
        supplier_full_name = "锦程五金批发部"
        for purchase in (
            _purchase(
                supplier_project_a,
                "螺杆",
                2,
                "个",
                supplier_name=supplier_full_name,
                freight_amount_cents=1000,
            ),
            _purchase(
                supplier_project_a,
                "垫片",
                3,
                "个",
                supplier_name=supplier_full_name,
                freight_amount_cents=0,
            ),
            _purchase(
                supplier_project_b,
                "螺帽",
                4,
                "个",
                supplier_name=supplier_full_name,
                freight_amount_cents=500,
            ),
            _purchase(
                supplier_project_a,
                "去年螺母",
                1,
                "个",
                purchase_date=f"{previous_year}-05-01",
                supplier_name=supplier_full_name,
                freight_amount_cents=0,
            ),
        ):
            procurement_service.add_purchase_order(*purchase)

        supplier_knowledge = business_knowledge_service.retrieve_business_knowledge(
            "锦程五金批发部那里我今年买了多少东西了"
        )["supplier_procurement"]
        assert supplier_knowledge["status"] == "matched"
        assert supplier_knowledge["intent"] == "supplier_aggregate"
        assert supplier_knowledge["residual_query"] == ""
        supplier_candidate = supplier_knowledge["candidates"][0]
        assert supplier_candidate["supplier_name"] == supplier_full_name
        assert supplier_candidate["order_count"] == 3
        assert supplier_candidate["material_type_count"] == 3
        assert supplier_candidate["tax_inclusive_material_amount_cents"] == 90000
        assert supplier_candidate["freight_amount_cents"] == 1500
        assert supplier_candidate["procurement_total_cents"] == 91500
        assert supplier_candidate["amount_by_project"] == [
            {"project": "供应商语义乙项目", "amount_cents": 40500},
            {"project": "供应商语义甲项目", "amount_cents": 51000},
        ]

        scoped_supplier = (
            business_knowledge_service.retrieve_business_knowledge(
                "锦程五金批发部那里今年买了多少材料",
                project_id=supplier_project_a,
            )["supplier_procurement"]
        )
        assert scoped_supplier["candidates"][0]["procurement_total_cents"] == 51000

        supplier_material = business_knowledge_service.retrieve_business_knowledge(
            "锦程五金批发部那里的螺杆今年买了多少"
        )
        assert supplier_material["supplier_procurement"]["status"] == "context_only"
        assert supplier_material["supplier_procurement"]["intent"] == "supplier_material"
        assert supplier_material["procurement"]["status"] == "matched"
        assert supplier_material["procurement"]["material_query"] == "螺杆"
        assert supplier_material["procurement"]["scope"]["supplier"] == supplier_full_name
        assert supplier_material["procurement"]["candidates"][0]["standard_name"] == "螺杆"
        assert supplier_material["procurement"]["candidates"][0]["confidence"] == "exact"
        assert supplier_material["procurement"]["candidates"][0]["quantity_by_unit"] == [
            {"unit": "个", "quantity": "2"}
        ]

        fake_client = FakeClient()
        ai_engine.make_ai_client = lambda *_args, **_kwargs: fake_client
        direct_answer = ai_engine.ask_ai(
            "今年买了多少岩棉板",
            project_id=second_project,
        )
        assert (
            f"按{current_year}年的采购台账，按近似名称匹配到“岩棉瓦楞板”"
            in direct_answer
        )
        assert "100张" in direct_answer
        assert fake_client.messages is None

        supplier_answer = ai_engine.ask_ai(
            "锦程五金批发部那里我今年买了多少东西了"
        )
        assert supplier_full_name in supplier_answer
        assert "采购总额（含税含运费）为 ¥915.00" in supplier_answer
        assert "供应商语义甲项目 ¥510.00" in supplier_answer
        assert "供应商语义乙项目 ¥405.00" in supplier_answer

        supplier_material_answer = ai_engine.ask_ai(
            "锦程五金批发部那里的螺杆今年买了多少"
        )
        assert supplier_full_name in supplier_material_answer
        assert "螺杆" in supplier_material_answer and "2个" in supplier_material_answer

        procurement_service.add_purchase_order(
            *_purchase(
                supplier_project_b,
                "槽钢",
                1,
                "吨",
                supplier_name="锦程钢材店",
                freight_amount_cents=0,
            )
        )
        ambiguous_supplier = (
            business_knowledge_service.retrieve_supplier_procurement_knowledge(
                "锦程那里买了多少东西"
            )
        )
        assert ambiguous_supplier["status"] == "ambiguous"
        assert ambiguous_supplier["requires_confirmation"]
        assert {
            item["supplier_name"] for item in ambiguous_supplier["candidates"]
        } == {"锦程五金批发部", "锦程钢材店"}
        ambiguous_supplier_answer = ai_engine.ask_ai("锦程那里买了多少东西")
        assert "供应商" in ambiguous_supplier_answer

        answer = ai_engine.ask_ai(
            "请分析岩棉板的采购情况和风险",
            project_id=second_project,
        )
        assert "岩棉瓦楞板" in answer
        prompt = fake_client.messages[1]["content"]
        knowledge_prompt = prompt.split("以下是只读的本地经营数据：", 1)[0]
        assert '"standard_name": "岩棉瓦楞板"' in knowledge_prompt
        assert '"answer_style": "analysis"' in knowledge_prompt
        assert '"quantity": "100"' in knowledge_prompt
        assert "知识检索甲项目" not in knowledge_prompt

    print("AI business-knowledge retrieval smoke test passed")


if __name__ == "__main__":
    main()
