"""Read-only acceptance checks for deterministic AI operating questions."""

import argparse
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    os.environ["SUPPLY_CHAIN_DB_PATH"] = str(args.database.resolve())
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    import ai_engine

    def no_remote_model(*_args, **_kwargs):
        raise AssertionError("确定性经营问题不应调用联网模型")

    ai_engine.make_ai_client = no_remote_model
    cases = (
        ("青岭目前确认了多少收入、开了多少票、回了多少？", "project_finance"),
        ("哪些发票还有余额？", "invoice_balance"),
        ("哪些回款还在等后续开票？", "receipt_matching"),
        ("今年哪个客户业务最多？", "customer_business"),
        ("哪个客户欠款最多？", "customer_business"),
        ("蓝湾龙门吊基础和零星工程毛利多少？", "project_profit"),
        ("本月成本主要花在哪里？", "cost_breakdown"),
        ("哪些项目现金余额为负？", "cash_risk"),
        ("哪些项目还有数据缺口？", "business_gaps"),
        ("今年和去年相比采购增加了多少？", "purchase_year_comparison"),
    )
    for index, (question, intent) in enumerate(cases, 1):
        try:
            result = ai_engine.ask_ai_turn(question)
        except Exception as error:
            raise AssertionError(f"case {index} failed: {intent}") from error
        assert result["answer_mode"] == "local", question
        assert result.get("intent") == intent, (question, result.get("intent"))
        assert result["answer"].strip(), question
        assert result.get("sources"), question
        assert result["sources"][0].get("page_key"), question
    print(f"AI deterministic operating queries passed: {len(cases)}")


if __name__ == "__main__":
    main()
