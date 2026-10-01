"""AILayoutMixin: extracted UI behavior from pages/ai_page.py."""

from tkinter import scrolledtext

import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from ttkbootstrap.widgets.scrolled import ScrolledFrame

from ui.theme import COLORS
from ui.scaling import scale_px


class AILayoutMixin:
    def _build_session_panel(self, workspace):
        panel = ttk.Frame(
            workspace,
            style="Card.TFrame",
            padding=(10, 10),
        )
        panel.grid(row=0, column=0, sticky=NSEW, padx=(0, 8))
        self.session_panel = panel
        panel.configure(width=scale_px(self.parent, 230))
        panel.grid_propagate(False)
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(2, weight=1)

        ttk.Label(panel, text="对话历史", style="CardTitle.TLabel").grid(
            row=0, column=0, sticky=W
        )
        ttk.Button(
            panel,
            text="新建对话",
            bootstyle="primary",
            command=self.new_conversation,
        ).grid(row=1, column=0, sticky=EW, pady=(9, 10))

        tree_box = ttk.Frame(panel, style="Card.TFrame")
        tree_box.grid(row=2, column=0, sticky=NSEW)
        tree_box.columnconfigure(0, weight=1)
        tree_box.rowconfigure(0, weight=1)
        self.conversation_tree = ttk.Treeview(
            tree_box,
            show="tree",
            selectmode="browse",
            height=14,
        )
        self.conversation_tree.column("#0", width=158, minwidth=110, stretch=True)
        session_scroll = ttk.Scrollbar(
            tree_box,
            orient=VERTICAL,
            command=self.conversation_tree.yview,
        )
        self.conversation_tree.configure(yscrollcommand=session_scroll.set)
        self.conversation_tree.grid(row=0, column=0, sticky=NSEW)
        session_scroll.grid(row=0, column=1, sticky=NS)
        self.conversation_tree.bind(
            "<<TreeviewSelect>>",
            self.on_conversation_selected,
        )

        ttk.Label(
            panel,
            textvariable=self.conversation_meta_var,
            style="CardText.TLabel",
            wraplength=155,
            justify=LEFT,
        ).grid(row=3, column=0, sticky=EW, pady=(9, 6))
        ttk.Button(
            panel,
            text="归档当前对话",
            bootstyle="secondary-outline",
            command=self.archive_current_conversation,
        ).grid(row=4, column=0, sticky=EW)

    def _build_chat_panel(self, workspace):
        panel = ttk.Frame(workspace, style="Card.TFrame")
        panel.grid(row=0, column=1, sticky=NSEW, padx=(0, 8))
        self.center_panel = panel
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        self.chat_panel = panel
        self.chat_panel.bind("<Configure>", self._on_chat_resize)

        chat_header = ttk.Frame(panel, style="ChatPane.TFrame", padding=(14, 11))
        chat_header.grid(row=0, column=0, sticky=EW)
        chat_header.columnconfigure(0, weight=1)
        actions = ttk.Frame(chat_header)
        actions.grid(row=0, column=1, rowspan=2, sticky=E)
        for text, command in (("历史", lambda: self._toggle_side_panel("history")),
                              ("新对话", self.new_conversation),
                              ("范围与条件", lambda: self._toggle_side_panel("context"))):
            ttk.Button(actions, text=text, command=command, bootstyle="secondary-outline").pack(side=LEFT, padx=(6, 0))
        ttk.Label(
            chat_header,
            textvariable=self.scope_var,
            style="CardTitle.TLabel",
        ).grid(row=0, column=0, sticky=W)
        ttk.Label(
            chat_header,
            textvariable=self.turn_status_var,
            style="CardText.TLabel",
        ).grid(row=1, column=0, sticky=W, pady=(3, 0))

        self.thread = ScrolledFrame(
            panel,
            padding=(14, 12),
            autohide=False,
            style="ChatThread.TFrame",
        )
        self.thread.grid(row=1, column=0, sticky=NSEW)

        composer = ttk.Frame(panel, style="ChatPane.TFrame", padding=(14, 10))
        self.composer = composer
        composer.grid(row=2, column=0, sticky=EW)
        composer.columnconfigure(0, weight=1)
        self.suggestions = ttk.Frame(composer, style="ChatPane.TFrame")
        self.suggestions.grid(
            row=0, column=0, columnspan=2, sticky=EW, pady=(0, 7)
        )

        self.input_text = scrolledtext.ScrolledText(
            composer,
            height=3,
            width=1,
            wrap=WORD,
            font=("Microsoft YaHei UI", 10),
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=8,
            background=COLORS["surface_muted"],
            foreground=COLORS["text"],
            insertbackground=COLORS["text"],
            selectbackground=COLORS["primary_soft"],
            selectforeground=COLORS["text"],
        )
        self.input_text.grid(row=1, column=0, sticky=EW, padx=(0, 8))
        self.input_text.configure(background=COLORS["surface_muted"])
        self.input_text.bind("<Control-Return>", self.on_ctrl_enter)
        self.send_btn = ttk.Button(
            composer,
            text="发送",
            bootstyle="primary",
            command=self.send,
        )
        self.send_btn.grid(row=1, column=1, sticky=SE)
        self.stop_btn = ttk.Button(
            composer,
            text="停止生成",
            bootstyle="danger-outline",
            command=self.stop_generation,
        )
        self.stop_btn.grid(row=1, column=1, sticky=SE)
        self.stop_btn.grid_remove()
        ttk.Label(
            composer,
            text="Ctrl + Enter 发送 · 可连续追问 · 只读查账，不修改业务数据",
            style="CardText.TLabel",
        ).grid(row=2, column=0, columnspan=2, sticky=W, pady=(6, 0))

    def _build_context_panel(self, workspace):
        panel = ScrolledFrame(
            workspace,
            style="Card.TFrame",
            padding=(13, 12),
            autohide=False,
            width=scale_px(self.parent, 230),
        )
        panel.grid(row=0, column=2, sticky=NSEW)
        self.context_panel = panel
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(11, weight=1)
        panel.bind("<Configure>", self._on_context_resize)

        ttk.Label(panel, text="当前上下文", style="CardTitle.TLabel").grid(
            row=0, column=0, sticky=W
        )
        ttk.Label(
            panel,
            text="这些条件会影响下一次追问，并且可以随时清除。",
            style="CardText.TLabel",
            wraplength=205,
            justify=LEFT,
        ).grid(row=1, column=0, sticky=EW, pady=(3, 12))

        self._context_section(panel, 4, "项目范围")
        self.scope_combo = ttk.Combobox(
            panel,
            textvariable=self.scope_var,
            state="readonly",
        )
        self.scope_combo.grid(row=5, column=0, sticky=EW, pady=(4, 10))
        self.scope_combo.bind("<<ComboboxSelected>>", self.on_scope_changed)

        self._context_section(panel, 6, "已识别条件")
        context_box = ttk.Frame(panel, style="Card.TFrame")
        context_box.grid(row=7, column=0, sticky=EW, pady=(4, 10))
        context_box.columnconfigure(0, weight=1)
        for row, variable in enumerate(
            (
                self.context_time_var,
                self.context_supplier_var,
                self.context_material_var,
                self.context_pending_var,
            )
        ):
            label = ttk.Label(
                context_box,
                textvariable=variable,
                style="CardText.TLabel",
                justify=LEFT,
                wraplength=205,
            )
            label.grid(row=row, column=0, sticky=EW, pady=(0, 5))
            self._context_labels.append(label)

        self._context_section(panel, 8, "数据口径")
        policy = ttk.Label(
            panel,
            text=(
                "采购总额按含税材料价与运费计算。利润、现金、施工金额、"
                "结算收入分别表达，项目之间不合并核算。"
            ),
            style="CardText.TLabel",
            wraplength=205,
            justify=LEFT,
        )
        policy.grid(row=9, column=0, sticky=EW, pady=(4, 10))
        self._context_labels.append(policy)

        self._context_section(panel, 10, "本次已加载")
        modules_label = ttk.Label(
            panel,
            textvariable=self.context_modules_var,
            style="CardText.TLabel",
            wraplength=205,
            justify=LEFT,
        )
        modules_label.grid(row=11, column=0, sticky=EW, pady=(4, 10))
        self._context_labels.append(modules_label)

        self._context_section(panel, 2, "今日经营行动")
        self.reminder_frame = ttk.Frame(panel, style="Card.TFrame")
        self.reminder_frame.grid(row=3, column=0, sticky=NSEW, pady=(4, 10))
        self.reminder_frame.columnconfigure(0, weight=1)

        ttk.Button(
            panel,
            text="清空追问条件",
            bootstyle="secondary-outline",
            command=self.clear_context,
        ).grid(row=12, column=0, sticky=EW)

    @staticmethod
    def _context_section(parent, row, text):
        box = ttk.Frame(parent, style="Card.TFrame")
        box.grid(row=row, column=0, sticky=EW)
        ttk.Separator(box).pack(fill=X, pady=(0, 7))
        ttk.Label(box, text=text, style="ContextValue.TLabel").pack(anchor=W)
