import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "venv" / "Lib" / "site-packages"))

import ai_engine
from db.migrations.ai_conversations import add_ai_conversations
from services import ai_conversation_service


class AIConversationServiceTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.db_path = Path(handle.name)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            """CREATE TABLE projects (
                   id INTEGER PRIMARY KEY,
                   name TEXT NOT NULL
               )"""
        )
        conn.execute("INSERT INTO projects(id, name) VALUES (1, '澄湖药业')")
        add_ai_conversations(conn)
        conn.commit()
        conn.close()

    def tearDown(self):
        self.db_path.unlink(missing_ok=True)

    def test_conversation_messages_and_context_are_persisted(self):
        conversation = ai_conversation_service.create_conversation(
            project_id=1,
            db_path=self.db_path,
        )
        ai_conversation_service.add_message(
            conversation["id"],
            "user",
            "锦帆那里今年买了多少东西？",
            db_path=self.db_path,
        )
        ai_conversation_service.update_context(
            conversation["id"],
            {
                "time": {"code": "current_year", "label": "2026年"},
                "supplier_name": "锦帆五金制品批发部",
            },
            db_path=self.db_path,
        )

        reloaded = ai_conversation_service.get_conversation(
            conversation["id"], db_path=self.db_path
        )
        messages = ai_conversation_service.list_messages(
            conversation["id"], db_path=self.db_path
        )
        self.assertEqual(reloaded["project_name"], "澄湖药业")
        self.assertEqual(
            reloaded["context"]["supplier_name"],
            "锦帆五金制品批发部",
        )
        self.assertEqual(messages[0]["content"], "锦帆那里今年买了多少东西？")

    def test_archived_conversation_leaves_recent_list(self):
        conversation = ai_conversation_service.create_conversation(
            db_path=self.db_path
        )
        ai_conversation_service.archive_conversation(
            conversation["id"], db_path=self.db_path
        )
        self.assertEqual(
            ai_conversation_service.list_conversations(db_path=self.db_path),
            [],
        )

    def test_inferred_project_updates_and_clears_conversation_scope(self):
        conversation = ai_conversation_service.create_conversation(
            db_path=self.db_path
        )
        updated = ai_conversation_service.update_context(
            conversation["id"], {"project_id": 1}, db_path=self.db_path
        )
        self.assertEqual(updated["project_id"], 1)
        self.assertEqual(updated["context"]["project_id"], 1)

        cleared = ai_conversation_service.update_context(
            conversation["id"], {"project_id": None}, db_path=self.db_path
        )
        self.assertIsNone(cleared["project_id"])
        self.assertNotIn("project_id", cleared["context"])


class AIConversationContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 检索业务知识会读取默认库；开源环境无生产库，构建全新临时库
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="ai_context_")
        cls.test_db = Path(cls.temp_dir.name) / "ai_context.db"
        import db.connection as _conn_module
        import db.migration_runner as _runner_module

        cls._saved_conn_path = _conn_module.DB_PATH
        cls._saved_runner_path = _runner_module.DB_PATH
        _conn_module.DB_PATH = cls.test_db
        _runner_module.DB_PATH = cls.test_db
        import database as _database

        _database.init_db()  # 建基础表 + run_migrations()

    @classmethod
    def tearDownClass(cls):
        import db.connection as _conn_module
        import db.migration_runner as _runner_module

        _conn_module.DB_PATH = cls._saved_conn_path
        _runner_module.DB_PATH = cls._saved_runner_path
        cls.temp_dir.cleanup()

    def setUp(self):
        self.rows = [
            {
                "id": 1,
                "order_no": "CG-20260301-001",
                "purchase_date": "2026-03-01",
                "project_name": "澄湖药业",
                "supplier_name": "锦帆五金制品批发部",
                "merchant_name_snapshot": "锦帆五金制品批发部",
                "material_name_snapshot": "螺帽",
                "specification_snapshot": "M20",
                "unit_snapshot": "个",
                "quantity": 1200,
                "line_amount_cents": 96000,
                "material_amount_cents": 87273,
                "tax_amount_cents": 8727,
                "freight_amount_cents": 20000,
            },
            {
                "id": 2,
                "order_no": "CG-20260401-001",
                "purchase_date": "2026-04-01",
                "project_name": "蓝湾",
                "supplier_name": "锦帆五金制品批发部",
                "merchant_name_snapshot": "锦帆五金制品批发部",
                "material_name_snapshot": "螺帽",
                "specification_snapshot": "M24",
                "unit_snapshot": "个",
                "quantity": 1200,
                "line_amount_cents": 96000,
                "material_amount_cents": 87273,
                "tax_amount_cents": 8727,
                "freight_amount_cents": 30000,
            },
        ]

    @patch(
        "services.business_knowledge_service.procurement_service.list_purchase_orders"
    )
    def test_supplier_abbreviation_becomes_visible_confirmation(self, list_orders):
        list_orders.return_value = self.rows
        turn = ai_engine.ask_ai_turn("锦帆那里今年买了多少东西？")
        self.assertEqual(turn["response_type"], "confirmation")
        self.assertEqual(turn["candidates"][0]["label"], "锦帆五金制品批发部")
        self.assertEqual(turn["context_updates"]["time"]["code"], "current_year")

    @patch(
        "services.business_knowledge_service.procurement_service.list_purchase_orders"
    )
    def test_followup_inherits_confirmed_supplier_and_year(self, list_orders):
        list_orders.return_value = self.rows
        context = {
            "supplier_name": "锦帆五金制品批发部",
            "time": {
                "code": "current_year",
                "label": "2026年",
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
            },
        }
        turn = ai_engine.ask_ai_turn("那螺帽呢？", conversation_context=context)
        self.assertEqual(turn["response_type"], "answer")
        self.assertEqual(turn["answer_mode"], "local")
        self.assertIn("锦帆五金制品批发部", turn["answer"])
        self.assertIn("2400个", turn["answer"])
        self.assertEqual(turn["sources"][0]["record_count"], 2)

    @patch(
        "services.business_knowledge_service.procurement_service.list_purchase_orders"
    )
    def test_company_material_spend_is_aggregate_not_material_name(self, list_orders):
        list_orders.return_value = self.rows
        with patch(
            "ai_engine.make_ai_client",
            side_effect=AssertionError("采购总额不应调用联网模型"),
        ):
            turn = ai_engine.ask_ai_turn("今年买材料花了多少钱")
        self.assertEqual(turn["response_type"], "answer")
        self.assertEqual(turn["answer_mode"], "local")
        self.assertIn("全公司采购总额", turn["answer"])
        self.assertIn("¥2,420.00", turn["answer"])
        self.assertIn("2 笔采购", turn["answer"])
        self.assertEqual(turn["sources"][0]["record_count"], 2)

    @patch(
        "services.business_knowledge_service.procurement_service.list_purchase_orders"
    )
    def test_explicit_company_scope_clears_supplier_context(self, list_orders):
        other_supplier = dict(self.rows[0])
        other_supplier.update(
            {
                "id": 3,
                "order_no": "CG-20260501-001",
                "purchase_date": "2026-05-01",
                "supplier_name": "砺锋钢铁",
                "merchant_name_snapshot": "砺锋钢铁",
                "material_name_snapshot": "H型钢",
                "line_amount_cents": 10000,
                "material_amount_cents": 9091,
                "tax_amount_cents": 909,
                "freight_amount_cents": 0,
            }
        )
        list_orders.return_value = [*self.rows, other_supplier]
        turn = ai_engine.ask_ai_turn(
            "全公司今年买材料花了多少钱",
            project_id=4,
            conversation_context={
                "project_id": 4,
                "supplier_name": "锦帆五金制品批发部",
            },
        )
        self.assertIn("¥2,520.00", turn["answer"])
        self.assertIn("全公司采购总额", turn["answer"])
        self.assertIsNone(turn["context_updates"]["project_id"])
        self.assertIsNone(turn["context_updates"]["supplier_name"])
        self.assertIsNone(turn["context_updates"]["material_name"])

    def test_project_year_labor_cost_is_resolved_inside_company_scope(self):
        projects = [
            {
                "id": 4,
                "name": "青枫",
                "project_code": "LEGACY-0004",
                "status": "进行中",
            },
            {
                "id": 10,
                "name": "青枫201",
                "project_code": "P-201",
                "status": "已关闭",
            },
        ]
        summary = {
            "record_count": 93,
            "worker_count": 8,
            "work_days": 108,
            "amount_minor": 3260000,
        }
        with (
            patch(
                "services.business_knowledge_service.project_service.list_projects",
                return_value=projects,
            ),
            patch(
                "services.business_knowledge_service.labor_service.get_labor_cost_summary",
                return_value=summary,
            ) as labor_summary,
            patch(
                "services.business_knowledge_service.procurement_service.list_purchase_orders",
                return_value=[],
            ) as purchase_orders,
            patch(
                "ai_engine.make_ai_client",
                side_effect=AssertionError("人工事实查询不应调用联网模型"),
            ),
        ):
            turn = ai_engine.ask_ai_turn("青枫今年的人工成本是多少")

        today = date.today()
        labor_summary.assert_called_once_with(
            start_date=f"{today.year}-01-01",
            end_date=today.isoformat(),
            project_id=4,
        )
        purchase_orders.assert_not_called()
        self.assertEqual(turn["answer_mode"], "local")
        self.assertIn("“青枫”项目人工成本为 ¥32,600.00", turn["answer"])
        self.assertEqual(turn["context_updates"]["project_id"], 4)
        self.assertEqual(
            turn["context_updates"]["time"]["code"], "current_year"
        )
        self.assertEqual(turn["sources"][0]["module"], "人工工天")

    def test_project_year_material_cost_is_resolved_inside_company_scope(self):
        projects = [
            {
                "id": 4,
                "name": "青枫",
                "project_code": "LEGACY-0004",
                "status": "进行中",
            }
        ]
        project_rows = [
            {
                **self.rows[0],
                "project_name": "青枫",
                "line_amount_cents": 150000,
                "material_amount_cents": 136364,
                "tax_amount_cents": 13636,
                "freight_amount_cents": 10000,
            }
        ]

        def list_orders(project_id=None):
            return project_rows if project_id == 4 else [*self.rows, *project_rows]

        with (
            patch(
                "services.business_knowledge_service.project_service.list_projects",
                return_value=projects,
            ),
            patch(
                "services.business_knowledge_service.procurement_service.list_purchase_orders",
                side_effect=list_orders,
            ),
            patch(
                "ai_engine.make_ai_client",
                side_effect=AssertionError("材料事实查询不应调用联网模型"),
            ),
        ):
            turn = ai_engine.ask_ai_turn("青枫今年的材料成本是多少")

        self.assertEqual(turn["answer_mode"], "local")
        self.assertIn("“青枫”项目采购总额", turn["answer"])
        self.assertEqual(turn["context_updates"]["project_id"], 4)
        self.assertEqual(turn["sources"][0]["view_type"], "procurement")
        self.assertEqual(turn["sources"][0]["scope_label"], "青枫")
        self.assertEqual(turn["sources"][0]["record_count"], 1)

    def test_explicit_company_labor_question_overrides_project_scope(self):
        projects = [
            {
                "id": 4,
                "name": "青枫",
                "project_code": "LEGACY-0004",
                "status": "进行中",
            }
        ]
        with (
            patch(
                "services.business_knowledge_service.project_service.list_projects",
                return_value=projects,
            ),
            patch(
                "services.business_knowledge_service.labor_service.get_labor_cost_summary",
                return_value={
                    "record_count": 120,
                    "worker_count": 12,
                    "work_days": 150,
                    "amount_minor": 5000000,
                },
            ) as labor_summary,
            patch(
                "services.business_knowledge_service.procurement_service.list_purchase_orders",
                return_value=[],
            ),
        ):
            turn = ai_engine.ask_ai_turn(
                "全公司今年人工成本是多少",
                project_id=4,
                conversation_context={"project_id": 4},
            )

        self.assertIsNone(labor_summary.call_args.kwargs["project_id"])
        self.assertIn("全公司人工成本为 ¥50,000.00", turn["answer"])
        self.assertIsNone(turn["context_updates"]["project_id"])


if __name__ == "__main__":
    unittest.main()
