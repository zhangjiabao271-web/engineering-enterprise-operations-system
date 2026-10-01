"""Isolated chat layout checks: fake messages, no database or network calls."""
import sys
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ttkbootstrap as ttk
from pages.ai_page import AIAssistantPage
from ui.theme import configure_design_system


def main():
    if len(sys.argv) == 1:
        for scaling, size in ((1.33, "980x760"), (2.0, "1500x1000"), (2.66, "1900x1300")):
            subprocess.run([sys.executable, "-B", __file__, str(scaling), size], check=True)
        return
    root = ttk.Window(themename="flatly")
    try:
        for scaling, size in ((float(sys.argv[1]), sys.argv[2]),):
            root.tk.call("tk", "scaling", scaling)
            root.geometry(size)
            configure_design_system(root)
            frame = ttk.Frame(root, padding=12)
            frame.pack(fill="both", expand=True)
            with patch("pages.ai_page.safe_init_loaders"):
                page = AIAssistantPage(frame)
            root.update()
            page.render_messages([
                {"role": "user", "content": "今年这个项目的毛利和毛利率是多少？"},
                {"role": "assistant", "message_type": "answer", "content":
                    "这里是布局测试，不是真实经营数据。\n\n" + "确认收入、成本与回款分别核对，来源可查看。" * 8,
                 "metadata": {"answer_mode": "local", "question": "测试问题"}},
            ])
            root.update()
            for side in ("history", "context", "context"):
                page._toggle_side_panel(side)
                root.update()
                assert page.input_text.winfo_ismapped()
                assert page.input_text.winfo_rooty() + page.input_text.winfo_height() <= root.winfo_rooty() + root.winfo_height()
                assert page.send_btn.winfo_rootx() + page.send_btn.winfo_width() <= root.winfo_rootx() + root.winfo_width()
                assert page.thread.winfo_height() > 200
            print(size, "input and messages visible; side panels toggle OK")
            if scaling == 2.0:
                from PIL import ImageGrab
                preview = Path(tempfile.gettempdir()) / "ai_chat_layout_preview.png"
                ImageGrab.grab(window=root.winfo_id()).save(preview)
                print(preview)
            frame.destroy()
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
