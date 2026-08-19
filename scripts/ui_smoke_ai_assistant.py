import os
import shutil
import sys
import tempfile
import time
from pathlib import Path


def wait_for_turn(root, page, timeout=8):
    deadline = time.monotonic() + timeout
    while page.busy and time.monotonic() < deadline:
        root.update()
        time.sleep(0.02)
    root.update()
    if page.busy:
        raise RuntimeError("AI 页面等待回答超时")


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def verify_source_dashboard(
    root,
    page,
    source,
    chart_title,
    expected_rows,
    screenshot_path=None,
):
    import ttkbootstrap as ttk
    from ui.charts import HorizontalBreakdown, MonthlyBarChart

    page.open_source_records(source)
    root.update_idletasks()
    root.update()
    dialog = root.winfo_children()[-1]
    children = list(descendants(dialog))
    labels = {
        child.cget("text")
        for child in children
        if isinstance(child, ttk.Label)
    }
    if chart_title not in labels or "详细台账" not in labels:
        raise RuntimeError(f"来源透视窗缺少图表或明细标题：{labels}")
    if not any(isinstance(child, MonthlyBarChart) for child in children):
        raise RuntimeError("来源透视窗缺少月度金额图")
    if not any(isinstance(child, HorizontalBreakdown) for child in children):
        raise RuntimeError("来源透视窗缺少分类排行图")
    trees = [child for child in children if isinstance(child, ttk.Treeview)]
    if not trees or len(trees[0].get_children()) != expected_rows:
        actual = len(trees[0].get_children()) if trees else 0
        raise RuntimeError(f"来源明细数量错误：{actual} != {expected_rows}")
    if screenshot_path:
        from PIL import ImageGrab

        ImageGrab.grab(window=dialog.winfo_id()).save(screenshot_path)
    dialog.destroy()
    root.update_idletasks()


def main():
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    os.environ["TCL_LIBRARY"] = str(
        project_root / ".venv" / "tcl" / "tcl8.6"
    )
    os.environ["TK_LIBRARY"] = str(
        project_root / ".venv" / "tcl" / "tk8.6"
    )
    screenshot_dir = os.environ.get("AI_UI_SCREENSHOT_DIR")
    if screenshot_dir:
        Path(screenshot_dir).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="supply-chain-ui-") as temp_dir:
        test_db = Path(temp_dir) / "ui-smoke.db"
        shutil.copy2(project_root / "supplier_data.db", test_db)
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_db)

        import ttkbootstrap as ttk

        import database
        from pages.ai_page import AIAssistantPage
        from services import ai_conversation_service
        from ui.theme import configure_design_system

        root = ttk.Window(themename="flatly")
        root.title("AI经营助手界面验收")
        root.geometry("1200x800+0+0")
        root.minsize(1200, 800)
        root.attributes("-alpha", 1.0 if screenshot_dir else 0.0)
        database.init_db()
        configure_design_system(root)
        content = ttk.Frame(root, padding=24)
        content.pack(fill="both", expand=True)
        page = AIAssistantPage(content)
        root.update()

        widths = {
            "sessions": page.session_panel.winfo_width(),
            "conversation": page.center_panel.winfo_width(),
            "context": page.context_panel.winfo_width(),
        }
        if widths["sessions"] < 170:
            raise RuntimeError(f"会话栏过窄：{widths}")
        if widths["conversation"] < 390:
            raise RuntimeError(f"对话栏过窄：{widths}")
        if widths["context"] < 220:
            raise RuntimeError(f"上下文栏过窄：{widths}")
        if not page.send_btn.winfo_ismapped() or not page.input_text.winfo_ismapped():
            raise RuntimeError("固定输入区或发送按钮不可见")

        page.set_input("锦帆那里今年买了多少东西？")
        page.send()
        wait_for_turn(root, page)
        conversation = ai_conversation_service.get_conversation(
            page.current_conversation_id
        )
        pending = (conversation.get("context") or {}).get(
            "pending_confirmation"
        )
        if not pending:
            raise RuntimeError("简称问题没有显示待确认上下文")
        messages = ai_conversation_service.list_messages(
            page.current_conversation_id
        )
        confirmation = messages[-1]
        candidates = (confirmation.get("metadata") or {}).get("candidates") or []
        if confirmation.get("message_type") != "confirmation" or not candidates:
            raise RuntimeError("没有生成可操作的候选确认卡片")

        page.confirm_candidate(confirmation["metadata"], candidates[0])
        wait_for_turn(root, page)
        messages = ai_conversation_service.list_messages(
            page.current_conversation_id
        )
        answer = messages[-1]
        if answer.get("message_type") != "answer":
            raise RuntimeError("确认供应商后没有自动继续回答")
        metadata = answer.get("metadata") or {}
        if metadata.get("answer_mode") != "local" or not metadata.get("sources"):
            raise RuntimeError("本地事实回答或数据来源入口缺失")

        page.new_conversation()
        page.set_input("今年买材料花了多少钱")
        page.send()
        wait_for_turn(root, page)
        aggregate_messages = ai_conversation_service.list_messages(
            page.current_conversation_id
        )
        aggregate_answer = aggregate_messages[-1]
        if aggregate_answer.get("message_type") != "answer":
            raise RuntimeError("全公司采购总额问题没有生成答案")
        if "采购总额" not in aggregate_answer.get("content", ""):
            raise RuntimeError("全公司采购总额仍被误判为具体材料")
        if not (aggregate_answer.get("metadata") or {}).get("sources"):
            raise RuntimeError("全公司采购总额没有原始记录入口")
        procurement_source = aggregate_answer["metadata"]["sources"][0]
        verify_source_dashboard(
            root,
            page,
            procurement_source,
            "月度含税材料金额",
            len(procurement_source.get("details") or []),
            (
                Path(screenshot_dir) / "ai-procurement-source.png"
                if screenshot_dir
                else None
            ),
        )

        page.new_conversation()
        page.set_input("青枫今年的人工成本是多少")
        page.send()
        wait_for_turn(root, page)
        labor_messages = ai_conversation_service.list_messages(
            page.current_conversation_id
        )
        labor_answer = labor_messages[-1]
        labor_sources = (labor_answer.get("metadata") or {}).get("sources") or []
        if not labor_sources:
            raise RuntimeError("人工成本答案没有数据透视入口")
        labor_source = labor_sources[0]
        verify_source_dashboard(
            root,
            page,
            labor_source,
            "月度人工成本",
            len(labor_source.get("details") or []),
            (
                Path(screenshot_dir) / "ai-labor-source.png"
                if screenshot_dir
                else None
            ),
        )

        for index in range(18):
            ai_conversation_service.add_message(
                page.current_conversation_id,
                "assistant",
                f"滚动验收记录 {index + 1}",
                message_type="notice",
                metadata={"answer_mode": "local"},
            )
        page.load_conversation(page.current_conversation_id)
        root.update()
        before = page.thread.vscroll.get()[0]
        page.thread.yview_moveto(0.0)
        root.update()
        top = page.thread.vscroll.get()[0]
        page.thread.yview_moveto(1.0)
        root.update()
        bottom = page.thread.vscroll.get()[0]
        if not (top <= before <= bottom and bottom > top):
            raise RuntimeError(
                f"消息滚动区域未正常工作：{top}, {before}, {bottom}"
            )

        root.destroy()
        print(
            "AI assistant UI smoke passed:",
            {
                "layout_widths": widths,
                "confirmation_candidates": len(candidates),
                "source_buttons": len(metadata["sources"]),
                "procurement_detail_rows": len(procurement_source.get("details") or []),
                "labor_detail_rows": len(labor_source.get("details") or []),
                "scroll_range": [top, bottom],
            },
        )


if __name__ == "__main__":
    main()
