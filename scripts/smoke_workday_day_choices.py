import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def verify_choices(dialog, expected_initial):
    import ttkbootstrap as ttk

    dialog.update_idletasks()
    choices = {
        child.cget("text"): child
        for child in descendants(dialog)
        if isinstance(child, ttk.Checkbutton)
        and child.cget("text") in {"0.5 工天", "1 工天"}
    }
    assert set(choices) == {"0.5 工天", "1 工天"}, choices
    assert all("round" in choice.cget("style").lower() for choice in choices.values())
    assert choices[expected_initial].instate(["selected"])

    choices[expected_initial].invoke()
    assert choices[expected_initial].instate(["selected"])

    choices["0.5 工天"].invoke()
    assert choices["0.5 工天"].instate(["selected"])
    assert choices["1 工天"].instate(["!selected"])

    choices["1 工天"].invoke()
    assert choices["1 工天"].instate(["selected"])
    assert choices["0.5 工天"].instate(["!selected"])


def main():
    parser = argparse.ArgumentParser(
        description="Smoke-test fixed 0.5/1 work-day choices"
    )
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="workday_choices_") as temp_dir:
        test_database = Path(temp_dir) / "supplier_data.db"
        shutil.copy2(args.database, test_database)
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        import ttkbootstrap as ttk
        from pages.workday_page import WorkdayDashboardPage
        from services import labor_service
        from ui.theme import configure_design_system

        root = ttk.Window(themename="flatly")
        root.withdraw()
        configure_design_system(root)
        host = ttk.Frame(root, padding=24)
        host.pack(fill="both", expand=True)
        page = WorkdayDashboardPage(host)

        page.open_batch_log_dialog()
        batch_dialog = root.winfo_children()[-1]
        verify_choices(batch_dialog, "1 工天")
        batch_dialog.destroy()

        work_log = next(
            row
            for row in labor_service.get_work_logs()
            if float(row.get("work_days") or 0) in {0.5, 1.0}
        )
        page.open_log_dialog(work_log["id"])
        edit_dialog = root.winfo_children()[-1]
        expected = "0.5 工天" if float(work_log["work_days"]) == 0.5 else "1 工天"
        verify_choices(edit_dialog, expected)
        edit_dialog.destroy()
        root.destroy()

    print("Work-day 0.5/1 choice smoke test passed for add and edit dialogs")


if __name__ == "__main__":
    main()
