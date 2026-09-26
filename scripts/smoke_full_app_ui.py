import argparse
import os
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
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        from db.backup import backup_database
        backup_database(args.database, test_database)

        import ttkbootstrap as ttk
        from tkinter import messagebox
        from main import SupplierManagerApp
        from ui.scaling import configure_main_window
        from ui.theme import style_dialog

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
        configure_main_window(root, 1200, 800, 1200, 800)
        callback_errors = []
        root.report_callback_exception = lambda *error: callback_errors.append(error)

        def settle():
            finished = ttk.BooleanVar(value=False)
            root.after(250, lambda: finished.set(True))
            root.wait_variable(finished)

        loaded = []
        try:
            root.update_idletasks()
            root.update()
            assert len(app.page_commands) >= 18
            for key in app.page_commands:
                app.navigate_to(key)
                root.update_idletasks()
                root.update()
                settle()
                assert app.current_page == key
                assert app.content_frame.winfo_children()
                loaded.append(key)
                assert app.page_transition._timer is None
                assert len(app.content_host.winfo_children()) == 1
            for key in list(app.page_commands)[:8]:
                app.navigate_to(key)
            settle()
            assert len(app.content_host.winfo_children()) == 1
            app.page_transition.set_enabled(True)
            app.navigate_to("supplier")
            root.update_idletasks()
            assert int(app.content_frame.place_info()["x"]) > 0
            settle()
            assert int(app.content_frame.place_info()["x"]) == 0
            assert not callback_errors, f"Tk callback errors: {callback_errors}"
            app.page_transition.set_enabled(False)
            app.navigate_to("home")
            root.update()
            assert app.page_transition._timer is None
            dialog = ttk.Toplevel(root)
            style_dialog(dialog, root, 500, 350)
            settle()
            assert float(dialog.attributes("-alpha")) == 1.0
            dialog.destroy()
            dialog = ttk.Toplevel(root)
            style_dialog(dialog, root, 500, 350)
            dialog.destroy()
            settle()
            assert not callback_errors, f"Tk callback errors: {callback_errors}"
            assert not dialogs, f"application raised dialogs during navigation: {dialogs}"
            for key, button in app.nav_buttons.items():
                assert button.winfo_ismapped(), (
                    f"navigation item not visible at 1200x800: {key}"
                )
                assert button.winfo_width() >= button.winfo_reqwidth(), key
        finally:
            root.destroy()

    print(f"Full application UI navigation passed: {', '.join(loaded)}")


if __name__ == "__main__":
    main()
