import ttkbootstrap as ttk
from ttkbootstrap.constants import BOTH, BOTTOM, LEFT, RIGHT, X
from ttkbootstrap.widgets.scrolled import ScrolledFrame

from ui.theme import style_dialog


def build_form_dialog(
    dialog,
    parent,
    width,
    height,
    *,
    padding=22,
    min_width=None,
    min_height=None,
):
    """Create a consistent modal form with scrolling content and fixed actions."""
    style_dialog(
        dialog,
        parent,
        width,
        height,
        resizable=True,
        min_width=min_width,
        min_height=min_height,
    )
    footer = ttk.Frame(dialog, padding=(padding, 10, padding, 16))
    footer.pack(side=BOTTOM, fill=X)
    body = ScrolledFrame(
        dialog,
        padding=padding,
        autohide=False,
        height=max(220, height - 72),
        width=width,
    )
    body.pack(fill=BOTH, expand=True)
    dialog.after_idle(body.enable_scrolling)
    return body, footer


def add_form_actions(
    footer,
    *,
    cancel_command,
    primary_text,
    primary_command,
    primary_style="primary",
    secondary_text=None,
    secondary_command=None,
    secondary_style="primary-outline",
):
    """Add predictable cancel, optional secondary, and primary form actions."""
    actions = ttk.Frame(footer)
    actions.pack(side=RIGHT)
    ttk.Button(
        actions,
        text="取消",
        bootstyle="secondary-outline",
        command=cancel_command,
    ).pack(side=LEFT, padx=(0, 8))
    if secondary_text and secondary_command:
        ttk.Button(
            actions,
            text=secondary_text,
            bootstyle=secondary_style,
            command=secondary_command,
        ).pack(side=LEFT, padx=(0, 8))
    primary = ttk.Button(
        actions,
        text=primary_text,
        bootstyle=primary_style,
        command=primary_command,
    )
    primary.pack(side=LEFT)
    return primary


def safe_init_loaders(page_name, loaders):
    """Run page startup loaders with per-loader error fallback.

    A single failed loader must not crash the whole desktop app.  Each loader
    is wrapped so the remaining ones still run, failures are logged and shown
    as a non-fatal warning dialog.

    Args:
        page_name: label used in the error dialog, e.g. "经营驾驶舱".
        loaders: list of zero-arg callables to run in order.
    """
    import logging
    from tkinter import messagebox

    logger = logging.getLogger(__name__)
    for loader in loaders:
        try:
            loader()
        except Exception as error:
            logger.exception("%s 初始化失败：%s", page_name, getattr(loader, "__name__", loader))
            messagebox.showwarning(
                "初始化提示",
                f"{page_name} 加载失败（{getattr(loader, '__name__', '数据')}）：\n{error}",
            )
