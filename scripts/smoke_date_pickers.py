"""Smoke-test the shared readonly calendar date field."""

from datetime import date
import os
from pathlib import Path
import sys


def main():
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    os.environ.setdefault(
        "TCL_LIBRARY", str(project_root / ".venv" / "tcl" / "tcl8.6")
    )
    os.environ.setdefault(
        "TK_LIBRARY", str(project_root / ".venv" / "tcl" / "tk8.6")
    )
    import ttkbootstrap as ttk

    from ui.components import DatePicker
    from ui.components import date_picker as date_picker_module
    from ui.theme import configure_design_system

    root = ttk.Window(themename="flatly")
    root.withdraw()
    configure_design_system(root)
    host = ttk.Frame(root, padding=20)
    host.pack(fill="both", expand=True)

    selected_events = []
    required_var = ttk.StringVar(value="2026-08-13")
    required = DatePicker(host, textvariable=required_var)
    required.pack(fill="x")
    root.update_idletasks()
    required.bind(
        "<<DateSelected>>",
        lambda _event: selected_events.append(required_var.get()),
    )
    assert str(required.entry.cget("state")) == "readonly"
    assert int(required.entry.cget("width")) == 12
    assert not required.entry.pack_info()["expand"]
    assert required.clear_button is None

    original_get_date = date_picker_module.Querybox.get_date
    date_picker_module.Querybox.get_date = staticmethod(
        lambda **_kwargs: date(2026, 8, 14)
    )
    try:
        required.select_button.invoke()
        root.update_idletasks()
    finally:
        date_picker_module.Querybox.get_date = original_get_date
    assert required_var.get() == "2026-08-14"
    assert selected_events == ["2026-08-14"]

    optional_var = ttk.StringVar(value="2026-08-14")
    optional = DatePicker(host, textvariable=optional_var, allow_empty=True)
    optional.pack(fill="x")
    assert optional.clear_button is not None
    assert optional.select_button.cget("style") == optional.clear_button.cget("style")
    optional.clear_button.invoke()
    assert optional_var.get() == ""

    optional_var.set("2026-08-14")
    optional.set_enabled(False)
    assert optional.entry.instate(["disabled"])
    assert optional.select_button.instate(["disabled"])
    assert optional.clear_button.instate(["disabled"])
    optional.open_calendar()
    optional.clear()
    assert optional_var.get() == "2026-08-14"
    optional.set_enabled(True)
    assert optional.entry.instate(["readonly", "!disabled"])
    assert optional.select_button.instate(["!disabled"])

    root.destroy()
    print("Calendar date picker smoke test passed")


if __name__ == "__main__":
    main()
