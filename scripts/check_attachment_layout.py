"""Check actual attachment windows without reading/writing business data."""
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ttkbootstrap as ttk
from ui.attachments import open_attachment_manager
from ui.theme import configure_design_system


def main():
    if len(sys.argv) == 1:
        for scaling in (1.33, 2.0, 2.66):
            subprocess.run([sys.executable, "-B", __file__, str(scaling)], check=True)
        return
    root = ttk.Window(themename="flatly")
    try:
        root.tk.call("tk", "scaling", float(sys.argv[1]))
        configure_design_system(root)
        root.geometry("900x650")
        root.update()
        with patch("ui.attachments.attachment_service.list_attachments", return_value=[]), \
             patch("ui.attachments.filedialog.askopenfilename", return_value="") as picker:
            dialog = open_attachment_manager(root, "purchase", 1, "采购磅单与规格清单")
            root.update()
            buttons = [child for frame in dialog.winfo_children()
                       for child in frame.winfo_children() if isinstance(child, ttk.Button)]
            assert len(buttons) == 4
            for size in (None, dialog.minsize()):
                if size:
                    dialog.geometry(f"{size[0]}x{size[1]}")
                root.update()
                for button in buttons:
                    assert button.winfo_ismapped(), button.cget("text")
                    assert button.winfo_rooty() >= dialog.winfo_rooty()
                    assert button.winfo_rooty() + button.winfo_height() <= dialog.winfo_rooty() + dialog.winfo_height()
                    assert button.winfo_rootx() + button.winfo_width() <= dialog.winfo_rootx() + dialog.winfo_width()
            next(button for button in buttons if button.cget("text") == "添加文件").invoke()
            picker.assert_called_once()
            dialog.destroy()
            print("scaling", sys.argv[1], "four actions visible at initial/minimum size; add action OK")
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
