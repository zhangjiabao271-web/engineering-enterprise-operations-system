import argparse
import os
import shutil
import sys
import tempfile
import tkinter.font as tkfont
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def _text_width(root, font_spec, text):
    font = tkfont.Font(root=root, font=font_spec)
    return font.measure(str(text))


def inspect_page(root, app, page_key):
    import ttkbootstrap as ttk

    app.navigate_to(page_key)
    root.update_idletasks()
    root.update()

    issues = []
    style = root.style
    for widget in descendants(app.content_frame):
        if not widget.winfo_ismapped():
            continue
        if isinstance(widget, (ttk.Label, ttk.Button)):
            text = str(widget.cget("text") or "")
            if text and widget.winfo_width() + 2 < widget.winfo_reqwidth():
                issues.append(
                    f"{page_key}: {widget.winfo_class()} “{text}” "
                    f"{widget.winfo_width()}px < {widget.winfo_reqwidth()}px"
                )
            if text and widget.winfo_height() + 2 < widget.winfo_reqheight():
                issues.append(
                    f"{page_key}: {widget.winfo_class()} “{text}”高度 "
                    f"{widget.winfo_height()}px < {widget.winfo_reqheight()}px"
                )
        if not isinstance(widget, ttk.Treeview):
            continue

        tree_style = widget.cget("style") or "Treeview"
        body_font = style.lookup(tree_style, "font")
        heading_style = f"{tree_style}.Heading"
        heading_font = (
            style.lookup(heading_style, "font")
            or style.lookup("Treeview.Heading", "font")
        )
        for column in widget.cget("columns"):
            width = int(widget.column(column, "width"))
            heading = str(widget.heading(column, "text") or "")
            heading_required = _text_width(
                root, heading_font, heading
            ) + 20
            if heading and width < heading_required:
                issues.append(
                    f"{page_key}: 表头“{heading}” "
                    f"{width}px < {heading_required}px"
                )

            for item in widget.get_children():
                value = str(widget.set(item, column) or "")
                if not value or len(value) > 8:
                    continue
                value_required = _text_width(root, body_font, value) + 18
                if width < value_required:
                    issues.append(
                        f"{page_key}: 列“{heading}”内容“{value}” "
                        f"{width}px < {value_required}px"
                    )
                    break
        total_width = sum(
            int(widget.column(column, "width"))
            for column in widget.cget("columns")
        )
        if (
            total_width > widget.winfo_width() + 2
            and not str(widget.cget("xscrollcommand") or "")
        ):
            issues.append(
                f"{page_key}: 宽表格缺少横向滚动 "
                f"{total_width}px > {widget.winfo_width()}px"
            )
    return issues


def main():
    parser = argparse.ArgumentParser(
        description="Audit visible short text for clipping at minimum window size"
    )
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="text_clipping_") as temp_dir:
        test_database = Path(temp_dir) / "supplier_data.db"
        shutil.copy2(args.database, test_database)
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        import ttkbootstrap as ttk
        from main import SupplierManagerApp

        root = ttk.Window(themename="flatly")
        root.geometry("1200x800")
        app = SupplierManagerApp(root)
        root.geometry("1200x800")
        root.update_idletasks()
        root.update()
        print(
            "DISPLAY "
            f"screen={root.winfo_screenwidth()}x{root.winfo_screenheight()} "
            f"window={root.winfo_width()}x{root.winfo_height()} "
            f"tk_scaling={float(root.tk.call('tk', 'scaling')):.3f}"
        )

        issues = []
        brand_and_nav = [app.nav_frame, *descendants(app.nav_frame)]
        nav_left = app.nav_frame.winfo_rootx()
        nav_top = app.nav_frame.winfo_rooty()
        nav_right = nav_left + app.nav_frame.winfo_width()
        nav_bottom = nav_top + app.nav_frame.winfo_height()
        for widget in brand_and_nav:
            if (
                isinstance(widget, (ttk.Label, ttk.Button))
                and widget.winfo_ismapped()
            ):
                text = str(widget.cget("text") or "")
                if text and widget.winfo_width() + 2 < widget.winfo_reqwidth():
                    issues.append(
                        f"navigation: {widget.winfo_class()} “{text}” "
                        f"{widget.winfo_width()}px < "
                        f"{widget.winfo_reqwidth()}px"
                    )
                if (
                    text
                    and widget.winfo_height() + 2
                    < widget.winfo_reqheight()
                ):
                    issues.append(
                        f"navigation: {widget.winfo_class()} “{text}”高度 "
                        f"{widget.winfo_height()}px < "
                        f"{widget.winfo_reqheight()}px"
                    )
                if (
                    widget.winfo_rootx() < nav_left
                    or widget.winfo_rooty() < nav_top
                    or widget.winfo_rootx() + widget.winfo_width()
                    > nav_right
                    or widget.winfo_rooty() + widget.winfo_height()
                    > nav_bottom
                ):
                    issues.append(
                        f"navigation: “{text}”超出侧栏可视边界"
                    )

        for page_key in app.page_commands:
            issues.extend(inspect_page(root, app, page_key))
        root.destroy()

    if issues:
        print("\n".join(dict.fromkeys(issues)))
        raise AssertionError(
            f"Visible text clipping audit failed: {len(set(issues))} issues"
        )
    print("Visible text clipping audit passed for navigation and 17 pages")


if __name__ == "__main__":
    main()
