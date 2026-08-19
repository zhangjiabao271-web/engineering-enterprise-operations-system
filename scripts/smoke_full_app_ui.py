import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Navigate every application page at the minimum window size"
    )
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="full_app_ui_") as temp_dir:
        test_database = Path(temp_dir) / "supplier_data.db"
        shutil.copy2(args.database, test_database)
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        import ttkbootstrap as ttk
        from tkinter import messagebox
        from main import SupplierManagerApp

        dialogs = []

        def capture_dialog(kind):
            def handler(title, message, **_kwargs):
                dialogs.append((kind, str(title), str(message)))
                return True if kind == "question" else "ok"

            return handler

        messagebox.showwarning = capture_dialog("warning")
        messagebox.showerror = capture_dialog("error")
        messagebox.showinfo = capture_dialog("info")
        messagebox.askyesno = capture_dialog("question")

        root = ttk.Window(themename="flatly")
        root.geometry("1200x800")
        app = SupplierManagerApp(root)
        loaded = []
        try:
            root.update_idletasks()
            root.update()
            assert len(app.page_commands) == 17
            for key in app.page_commands:
                app.navigate_to(key)
                root.update_idletasks()
                root.update()
                assert app.current_page == key
                assert app.content_frame.winfo_children()
                loaded.append(key)
            assert not dialogs, f"application raised dialogs during navigation: {dialogs}"
            for key, button in app.nav_buttons.items():
                assert button.winfo_ismapped(), (
                    f"navigation item not visible at 1200x800: {key}"
                )
                assert button.winfo_y() >= 0
                assert (
                    button.winfo_y() + button.winfo_height()
                    <= app.nav_frame.winfo_height()
                ), f"navigation item outside sidebar: {key}"
        finally:
            root.destroy()

    print(f"Full application UI navigation passed: {', '.join(loaded)}")


if __name__ == "__main__":
    main()
