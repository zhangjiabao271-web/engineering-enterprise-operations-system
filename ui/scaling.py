"""DPI-aware layout helpers for the Windows desktop application."""

from ctypes import byref, windll, wintypes

BASELINE_TK_SCALING = 96 / 72


def ui_scale(widget):
    """Return the layout scale relative to a 96-DPI Windows desktop."""
    current = float(widget.tk.call("tk", "scaling"))
    return max(1.0, current / BASELINE_TK_SCALING)


def scale_px(widget, value):
    return max(1, round(value * ui_scale(widget)))


def working_area(widget):
    """Return the usable Windows desktop rectangle, excluding the taskbar."""
    try:
        rect = wintypes.RECT()
        if windll.user32.SystemParametersInfoW(48, 0, byref(rect), 0):
            return rect.left, rect.top, rect.right, rect.bottom
    except (AttributeError, OSError):
        pass
    return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight()


def window_frame_size(window):
    """Return the non-client width and height added by the window manager."""
    window.update_idletasks()
    side = max(0, window.winfo_rootx() - window.winfo_x())
    top = max(0, window.winfo_rooty() - window.winfo_y())
    return side * 2, top + side


def configure_main_window(root, width, height, min_width, min_height):
    """Scale and center the window inside the usable Windows work area."""
    root.update_idletasks()
    left, top, right, bottom = working_area(root)
    work_width = right - left
    work_height = bottom - top
    margin = scale_px(root, 12)
    frame_width, frame_height = window_frame_size(root)
    safe_width = max(800, work_width - margin * 2 - frame_width)
    safe_height = max(600, work_height - margin * 2 - frame_height)

    target_width = min(scale_px(root, width), safe_width)
    target_height = min(scale_px(root, height), safe_height)
    minimum_width = min(scale_px(root, min_width), target_width)
    minimum_height = min(scale_px(root, min_height), target_height)

    outer_width = target_width + frame_width
    outer_height = target_height + frame_height
    x = left + max(margin, (work_width - outer_width) // 2)
    y = top + max(margin, (work_height - outer_height) // 2)
    root.geometry(f"{target_width}x{target_height}+{x}+{y}")
    root.minsize(minimum_width, minimum_height)


def scale_treeview_columns(container):
    """Scale Treeview columns and preserve access to every wide column."""
    import ttkbootstrap as ttk

    for child in container.winfo_children():
        if isinstance(child, ttk.Treeview) and not getattr(
            child, "_dpi_columns_scaled", False
        ):
            factor = ui_scale(child)
            if factor > 1.01:
                for column in child.cget("columns"):
                    width = int(child.column(column, "width"))
                    min_width = int(child.column(column, "minwidth"))
                    child.column(
                        column,
                        width=max(1, round(width * factor)),
                        minwidth=max(1, round(min_width * factor)),
                    )
            child._dpi_columns_scaled = True
            _ensure_horizontal_scrollbar(child)
        scale_treeview_columns(child)


def _ensure_horizontal_scrollbar(tree):
    """Add a horizontal scrollbar without disturbing surrounding content."""
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import BOTH, BOTTOM, LEFT, RIGHT, X, Y

    if getattr(tree, "_horizontal_scrollbar_added", False):
        return
    if tree.winfo_manager() != "pack":
        return

    parent = tree.master
    vertical_scrollbars = [
        child
        for child in parent.winfo_children()
        if isinstance(child, ttk.Scrollbar)
        and str(child.cget("orient")) == "vertical"
    ]
    tree.pack_forget()
    for scrollbar in vertical_scrollbars:
        scrollbar.pack_forget()

    horizontal = ttk.Scrollbar(
        parent, orient="horizontal", command=tree.xview
    )
    tree.configure(xscrollcommand=horizontal.set)
    horizontal.pack(side=BOTTOM, fill=X)
    for scrollbar in vertical_scrollbars:
        scrollbar.pack(side=RIGHT, fill=Y)
    tree.pack(side=LEFT, fill=BOTH, expand=True)
    tree._horizontal_scrollbar_added = True
