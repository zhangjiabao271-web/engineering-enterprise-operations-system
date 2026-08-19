import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


class FakeClient:
    def __init__(self, answer):
        self.answer = answer
        self.messages = None

    def chat_completion(self, messages, **_kwargs):
        self.messages = messages
        return self.answer


def main():
    parser = argparse.ArgumentParser(
        description="Smoke-test AI operating workflow without external requests"
    )
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="ai_workflow_") as temp_dir:
        test_database = Path(temp_dir) / "supplier_data.db"
        shutil.copy2(args.database, test_database)
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        import database
        import ai_engine
        from ai_client import AIError
        from services import project_service

        database.init_db()
        projects = project_service.list_projects(active_only=False)
        assert projects
        selected_project = projects[0]

        context = ai_engine.build_operating_context(selected_project["id"])
        assert context["overview"]["north_star"]["name"] == "项目经营可核算率"
        assert context["selected"]["summary"]["project"]["id"] == selected_project["id"]

        fake_client = FakeClient(
            "# 经营结论\n\n事实、判断和行动建议已经分开。"
        )
        ai_engine.make_ai_client = lambda *_args, **_kwargs: fake_client
        result = ai_engine.ask_ai(
            "这个项目赚不赚钱，现金是否安全？",
            project_id=selected_project["id"],
        )
        assert "经营结论" in result
        prompt = fake_client.messages[1]["content"]
        assert selected_project["name"] in prompt
        assert "毛利" in prompt and "现金余额" in prompt
        assert "现场记录金额" in prompt and "结算确认" in prompt

        try:
            ai_engine.ask_ai("   ")
        except AIError as error:
            assert error.code == "empty_question"
            assert "请输入" in str(error)
        else:
            raise AssertionError("Empty AI question should be rejected")

    print("AI operating workflow smoke test passed")


if __name__ == "__main__":
    main()
