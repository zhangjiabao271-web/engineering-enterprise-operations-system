"""Page layout primitives shared by all pages (report-led, no color blocks).

Replaces the hand-built header / filter-bar / panel code repeated across
pages/ (15 pages build a PageTitle header by hand today). These components
only own *layout*; data logic stays in the pages.
"""

import ttkbootstrap as ttk
from ttkbootstrap.constants import BOTTOM, LEFT, RIGHT, W, X, Y

from ui.theme import SPACING


def _dock(widget, bar, container, **opts):
    """把预先创建的控件挂进条组件，并抬升到条的可见层之上。

    调用方以页面/标签页为 master 先创建控件、后创建条（参数的求值顺序
    决定了这一点），Tk 的堆叠顺序按创建先后排列，控件会落在条的底层；
    而条有不透明背景，会把控件整个盖住——几何映射正常（winfo_ismapped=1）
    但视觉上看不见。pack(in_=) 只解决几何归属，不解决 z-order，必须再
    tkraise 把控件抬到条的整棵子树之上。这里同时断言几何归属真的生效，
    让错位/遮挡在页面构建时就响亮报错，而不是静默缺控件。
    """
    widget.pack(in_=container, **opts)
    actual = widget.pack_info().get("in")
    if str(actual) != str(container):
        raise RuntimeError(
            f"组件挂载失败：{widget} 的容器是 {actual}，预期 {container}"
        )
    try:
        widget.tkraise(bar)
    except Exception as exc:
        raise RuntimeError(
            f"组件层级调整失败：{widget} 与 {bar} 不在同一父容器，"
            "请以页面/标签页为 master 创建控件"
        ) from exc


class PageHeader(ttk.Frame):
    """Standard page heading: title + subtitle on the left, actions on the right."""

    def __init__(self, parent, title, subtitle="", *, actions=None, **kwargs):
        super().__init__(parent, **kwargs)
        self.pack(fill=X, pady=(0, SPACING["page_gap"]))
        title_box = ttk.Frame(self)
        title_box.pack(side=LEFT)
        ttk.Label(
            title_box, text=title, style="PageTitle.TLabel"
        ).pack(anchor=W)
        if subtitle:
            ttk.Label(
                title_box, text=subtitle, style="PageSub.TLabel"
            ).pack(anchor=W, pady=(4, 0))
        if actions:
            box = ttk.Frame(self)
            box.pack(side=RIGHT)
            for widget in actions:
                # in_=box：允许调用方以页面为 master 预先创建按钮，
                # 仍正确挂进头部右侧容器（pack 默认只进 master）。
                _dock(widget, self, box, side=LEFT, padx=(8, 0))
            self.actions_box = box
        else:
            self.actions_box = None


class FilterBar(ttk.Frame):
    """Standard filter strip: label + control pairs, actions docked right."""

    def __init__(self, parent, *controls, actions=None, **kwargs):
        """controls: iterable of (label_text, widget) or widgets."""
        super().__init__(
            parent, style="Toolbar.TFrame", padding=(12, 8), **kwargs
        )
        self.pack(fill=X, pady=(0, SPACING["md"]))
        for item in controls:
            if isinstance(item, tuple) and len(item) == 2:
                label, widget = item
                ttk.Label(
                    self, text=label, style="Toolbar.TLabel"
                ).pack(side=LEFT)
                _dock(widget, self, self, side=LEFT, padx=(8, SPACING["lg"]))
            else:
                _dock(item, self, self, side=LEFT, padx=(0, SPACING["lg"]))
        if actions:
            box = ttk.Frame(self)
            box.pack(side=RIGHT)
            for widget in actions:
                _dock(widget, self, box, side=LEFT, padx=(8, 0))
            self.actions_box = box
        else:
            self.actions_box = None


class SectionPanel(ttk.Frame):
    """Bordered card with an optional title — the report sheet block."""

    def __init__(self, parent, title=None, *, padding=SPACING["card_pad"], **kwargs):
        super().__init__(parent, style="Card.TFrame", padding=padding, **kwargs)
        self.pack(fill=X, pady=(0, SPACING["md"]))
        if title:
            ttk.Label(
                self, text=title, style="CardTitle.TLabel"
            ).pack(anchor=W, pady=(0, 6))


class BottomToolbar(ttk.Frame):
    """Action strip pinned under a table / panel."""

    def __init__(self, parent, *buttons, **kwargs):
        super().__init__(parent, **kwargs)
        self.pack(side=BOTTOM, fill=X, pady=(8, 0))
        for button in buttons:
            # in_=self：按钮可用页/标签页为 master 预创建，仍挂进本工具条
            _dock(button, self, self, side=LEFT, padx=(0, 8))
