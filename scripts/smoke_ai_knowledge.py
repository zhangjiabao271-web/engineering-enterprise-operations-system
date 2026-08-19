import sys
from pathlib import Path


def main():
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    sys.path.insert(0, str(project_root / "venv" / "Lib" / "site-packages"))

    import ai_engine

    question = "锦帆那里今年买了多少东西？"
    first_turn = ai_engine.ask_ai_turn(question)
    if first_turn["response_type"] != "confirmation":
        raise RuntimeError("供应商简称没有进入可见确认流程")
    candidates = first_turn.get("candidates") or []
    if not candidates:
        raise RuntimeError("供应商确认没有候选")

    context = dict(first_turn.get("context_updates") or {})
    context.update(candidates[0].get("context_updates") or {})
    context.pop("pending_confirmation", None)
    confirmed_turn = ai_engine.ask_ai_turn(
        question,
        conversation_context=context,
    )
    if confirmed_turn["response_type"] != "answer":
        raise RuntimeError("确认供应商后没有继续生成答案")
    if confirmed_turn.get("answer_mode") != "local":
        raise RuntimeError("简单供应商事实查询不应依赖联网模型")
    if not confirmed_turn.get("sources"):
        raise RuntimeError("采购答案没有原始数据来源入口")
    aggregate_turn = ai_engine.ask_ai_turn("今年买材料花了多少钱")
    if aggregate_turn.get("answer_mode") != "local":
        raise RuntimeError("全公司采购总额没有使用本地台账直接回答")
    if "采购总额" not in aggregate_turn.get("answer", ""):
        raise RuntimeError("全公司采购总额问题仍被误判为材料名称")
    if not aggregate_turn.get("sources"):
        raise RuntimeError("全公司采购总额缺少原始记录入口")
    print(
        "AI knowledge smoke passed:",
        {
            "candidate": candidates[0]["label"],
            "answer_mode": confirmed_turn["answer_mode"],
            "source_count": len(confirmed_turn["sources"]),
            "aggregate_source_count": len(aggregate_turn["sources"]),
        },
    )


if __name__ == "__main__":
    main()
