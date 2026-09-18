import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_client import AIClient

from db.migrations.ai_conversations import add_ai_conversations
from db.migrations.ai_feedback import add_ai_message_feedback
from services import (
    ai_conversation_service,
    ai_operating_query_service,
    ai_secret_store,
)


class OperatingQueryTests(unittest.TestCase):
    project = {
        "id": 7,
        "name": "蓝湾轨道基础",
        "project_code": "P-TEST-7",
        "status": "进行中",
    }

    def _project_patch(self):
        return patch(
            "services.ai_operating_query_service.project_service.list_projects",
            return_value=[self.project],
        )

    def test_project_finance_is_direct_and_traceable(self):
        finance = {
            "summary": {},
            "projects": [{
                "project_id": 7,
                "project_name": "蓝湾轨道基础",
                "settlement_minor": 7_500_000,
                "invoice_minor": 5_900_000,
                "receipt_minor": 4_000_000,
                "receivable_minor": 3_500_000,
                "uninvoiced_minor": 1_600_000,
                "invoice_rate_percent": 78.666,
                "receipt_rate_percent": 53.333,
                "invoice_policy": "required",
            }],
        }
        with (
            self._project_patch(),
            patch(
                "services.ai_operating_query_service.finance_service.get_finance_dashboard",
                return_value=finance,
            ),
            patch(
                "services.ai_operating_query_service.finance_service.list_invoices",
                return_value=[{"id": 1}],
            ),
            patch(
                "services.ai_operating_query_service.finance_service.list_receipts",
                return_value=[{"id": 2}],
            ),
        ):
            result = ai_operating_query_service.retrieve_operating_query(
                "蓝湾轨道基础确认了多少收入、开了多少票、回了多少？"
            )
        self.assertEqual(result["intent"], "project_finance")
        self.assertIn("¥75,000.00", result["answer"])
        self.assertIn("尚未开票 ¥16,000.00", result["answer"])
        self.assertEqual(result["sources"][0]["page_key"], "finance")
        self.assertEqual(result["context_updates"]["project_id"], 7)

    def test_invoice_balance_uses_unreceived_amount(self):
        invoices = [
            {
                "invoice_date": "2026-08-01",
                "invoice_no": "INV-1",
                "project_name": "项目甲",
                "amount_minor": 10_000,
                "received_minor": 6_000,
                "unreceived_minor": 4_000,
                "collection_status": "部分回款",
            },
            {
                "invoice_date": "2026-08-02",
                "invoice_no": "INV-2",
                "project_name": "项目乙",
                "amount_minor": 20_000,
                "received_minor": 20_000,
                "unreceived_minor": 0,
                "collection_status": "已结清",
            },
        ]
        with (
            patch(
                "services.ai_operating_query_service.project_service.list_projects",
                return_value=[],
            ),
            patch(
                "services.ai_operating_query_service.finance_service.list_invoices",
                return_value=invoices,
            ),
        ):
            result = ai_operating_query_service.retrieve_operating_query(
                "哪些发票还有余额？"
            )
        self.assertIn("1 张发票尚未结清", result["answer"])
        self.assertIn("¥40.00", result["answer"])
        self.assertEqual(result["sources"][0]["record_count"], 1)

    def test_procurement_question_is_not_intercepted(self):
        with self._project_patch():
            result = ai_operating_query_service.retrieve_operating_query(
                "蓝湾轨道基础今年买了多少螺栓？"
            )
        self.assertEqual(result["status"], "not_applicable")

    def test_collection_action_plan_is_local_and_traceable(self):
        row = {
            "project_id": 7,
            "project_name": "蓝湾轨道基础",
            "customer_name": "测试客户",
            "receivable_minor": 700_000,
            "aging_bucket": "31—60天",
            "promised_date": "2026-08-27",
            "next_followup_date": "2026-08-28",
            "owner_name": "本人",
            "attention_status": "承诺逾期",
            "next_action": "电话确认付款",
            "needs_action_today": True,
        }
        with (
            self._project_patch(),
            patch(
                "services.ai_operating_query_service.collection_service.list_project_cases",
                return_value=[row],
            ),
        ):
            result = ai_operating_query_service.retrieve_operating_query(
                "今天该催谁？"
            )
        self.assertEqual(result["intent"], "collection_actions")
        self.assertIn("蓝湾轨道基础", result["answer"])
        self.assertIn("¥7,000.00", result["answer"])
        self.assertEqual(result["sources"][0]["page_key"], "finance")

    def test_promised_overdue_query_does_not_mix_other_followups(self):
        row = {
            "project_id": 7,
            "project_name": "蓝湾轨道基础",
            "customer_name": "测试客户",
            "receivable_minor": 700_000,
            "aging_bucket": "31—60天",
            "promised_date": None,
            "next_followup_date": "2026-08-28",
            "owner_name": "本人",
            "attention_status": "今日跟进",
            "next_action": "电话确认付款",
            "needs_action_today": True,
        }
        with (
            self._project_patch(),
            patch(
                "services.ai_operating_query_service.collection_service.list_project_cases",
                return_value=[row],
            ),
        ):
            result = ai_operating_query_service.retrieve_operating_query(
                "哪些客户承诺逾期？"
            )
        self.assertIn("没有客户承诺付款日已经逾期", result["answer"])
        self.assertEqual(result["sources"][0]["record_count"], 0)

    def test_business_action_plan_combines_collection_and_governance(self):
        collection = {
            "project_name": "蓝湾轨道基础",
            "receivable_minor": 700_000,
            "attention_status": "今日跟进",
            "next_action": "电话确认付款",
        }
        governance = {
            "issue_type": "待验收施工",
            "project_name": "桑叶殿改造",
            "subject": "现场验收",
            "action": "确认验收结果",
        }
        with (
            self._project_patch(),
            patch(
                "services.ai_operating_query_service.collection_service.get_actionable_cases",
                return_value=[collection],
            ),
            patch(
                "services.ai_operating_query_service.data_governance_service.list_fulfillment_gaps",
                return_value=[governance],
            ),
        ):
            result = ai_operating_query_service.retrieve_operating_query(
                "今天做什么？"
            )
        self.assertEqual(result["intent"], "business_action_plan")
        self.assertIn("催款或复查 1 项", result["answer"])
        self.assertEqual(
            [source["page_key"] for source in result["sources"]],
            ["finance", "governance"],
        )


class FeedbackAndSecretStoreTests(unittest.TestCase):
    def test_feedback_is_kept_outside_business_facts(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "feedback.db"
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.execute(
                """CREATE TABLE projects (
                       id INTEGER PRIMARY KEY, name TEXT, project_code TEXT
                   )"""
            )
            add_ai_conversations(conn)
            add_ai_message_feedback(conn)
            conn.commit()
            conn.close()
            conversation = ai_conversation_service.create_conversation(
                db_path=db_path
            )
            message = ai_conversation_service.add_message(
                conversation["id"],
                "assistant",
                "本地经营回答",
                message_type="answer",
                db_path=db_path,
            )
            ai_conversation_service.set_message_feedback(
                message["id"], "useful", db_path=db_path
            )
            loaded = ai_conversation_service.list_messages(
                conversation["id"], db_path=db_path
            )
            self.assertEqual(loaded[0]["feedback"], "useful")

    def test_secret_store_round_trip_with_injected_crypto(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "credential.dat"
            ai_secret_store.save_secret(
                "test-secret",
                path,
                protect=lambda value: value[::-1],
            )
            loaded = ai_secret_store.get_secret(
                path,
                unprotect=lambda value: value[::-1],
            )
            self.assertEqual(loaded, "test-secret")
            self.assertNotEqual(path.read_bytes(), b"test-secret")


class StreamingClientTests(unittest.TestCase):
    class Response:
        status_code = 200

        def __init__(self):
            self.closed = False

        def iter_lines(self, decode_unicode=True):
            del decode_unicode
            yield 'data: {"choices":[{"delta":{"content":"回答"}}]}'
            yield 'data: {"choices":[{"delta":{"content":" 内容"}}]}'
            yield "data: [DONE]"

        def close(self):
            self.closed = True

    class Session:
        def __init__(self, response):
            self.response = response

        def post(self, *_args, **_kwargs):
            return self.response

    def test_stream_preserves_chunk_spacing(self):
        client = AIClient("test-key")
        response = self.Response()
        with patch.object(client, "_session", return_value=self.Session(response)):
            chunks = list(client.chat_completion_stream([{"role": "user", "content": "测试"}]))
        self.assertEqual("".join(chunks), "回答 内容")
        self.assertTrue(response.closed)

    def test_stream_can_be_cancelled_without_partial_result(self):
        client = AIClient("test-key")
        response = self.Response()
        cancelled = threading.Event()
        cancelled.set()
        with patch.object(client, "_session", return_value=self.Session(response)):
            chunks = list(
                client.chat_completion_stream(
                    [{"role": "user", "content": "测试"}],
                    cancel_event=cancelled,
                )
            )
        self.assertEqual(chunks, [])
        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
