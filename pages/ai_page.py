import queue
import threading
from collections import defaultdict
from datetime import datetime
from tkinter import messagebox, scrolledtext

import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from ttkbootstrap.widgets.scrolled import ScrolledFrame

import ai_engine
from ai_client import AIError, DEFAULT_API_BASE, DEFAULT_MODEL
from services import ai_conversation_service, project_service
from ui.charts import HorizontalBreakdown, MonthlyBarChart
from ui.dialogs import add_form_actions, build_form_dialog, safe_init_loaders
from ui.theme import COLORS, FONT_BODY, style_dialog


class AIAssistantPage:
    """Continuous, read-only operating conversation workspace."""

    def __init__(self, parent):
        self.parent = parent
        self.scope_map = {"全公司经营总览": None}
        self.scope_var = ttk.StringVar(value="全公司经营总览")
        self.model_status_var = ttk.StringVar()
        self.conversation_title_var = ttk.StringVar(value="新对话")
        self.conversation_meta_var = ttk.StringVar(value="")
        self.turn_status_var = ttk.StringVar(value="准备就绪")
        self.context_project_var = ttk.StringVar(value="全公司")
        self.context_time_var = ttk.StringVar(value="未限定时间")
        self.context_supplier_var = ttk.StringVar(value="未限定供应商")
        self.context_material_var = ttk.StringVar(value="未限定材料")
        self.context_pending_var = ttk.StringVar(value="没有待确认对象")
        self.context_modules_var = ttk.StringVar(value="尚未读取业务模块")
        self.current_conversation_id = None
        self.current_context = {}
        self.busy = False
        self.pending_frame = None
        self._loading_sessions = False
        self._loading_context = False
        self._wrap_labels = []
        self._context_labels = []
        self._turn_results = queue.Queue()
        self._turn_poll_scheduled = False
        self.build_ui()
        safe_init_loaders(
            "AI 经营助手",
            [self.load_projects, self.check_config, self.refresh_conversation_list],
        )

    def build_ui(self):
        header = ttk.Frame(self.parent)
        header.pack(fill=X, pady=(0, 12))
        title_box = ttk.Frame(header)
        title_box.pack(side=LEFT, fill=X, expand=True)
        ttk.Label(
            title_box,
            text="AI 经营助手",
            style="PageTitle.TLabel",
        ).pack(anchor=W)
        ttk.Label(
            title_box,
            text="连续追问、上下文可见；每个经营数字都可以回到原始台账复核",
            style="PageSub.TLabel",
        ).pack(anchor=W, pady=(3, 0))
        header_actions = ttk.Frame(header)
        header_actions.pack(side=RIGHT, padx=(12, 0))
        ttk.Label(
            header_actions,
            textvariable=self.model_status_var,
            style="MutedStatus.TLabel",
        ).pack(side=LEFT, padx=(0, 8))
        ttk.Button(
            header_actions,
            text="AI 设置",
            bootstyle="secondary-outline",
            command=self.open_config_dialog,
        ).pack(side=LEFT)

        workspace = ttk.Frame(self.parent)
        workspace.pack(fill=BOTH, expand=True)
        self.workspace = workspace
        workspace.columnconfigure(0, minsize=178)
        workspace.columnconfigure(1, weight=1, minsize=390)
        workspace.columnconfigure(2, minsize=230)
        workspace.rowconfigure(0, weight=1)

        self._build_session_panel(workspace)
        self._build_chat_panel(workspace)
        self._build_context_panel(workspace)

    def _build_session_panel(self, workspace):
        panel = ttk.Frame(
            workspace,
            style="Card.TFrame",
            padding=(10, 10),
        )
        panel.grid(row=0, column=0, sticky=NSEW, padx=(0, 8))
        self.session_panel = panel
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
        ttk.Label(
            chat_header,
            textvariable=self.conversation_title_var,
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
        composer.grid(row=2, column=0, sticky=EW)
        composer.columnconfigure(0, weight=1)
        suggestions = ttk.Frame(composer, style="ChatPane.TFrame")
        suggestions.grid(row=0, column=0, columnspan=2, sticky=EW, pady=(0, 7))
        for index, question in enumerate(
            (
                "按项目拆开看",
                "查看数据缺口",
                "和去年比较",
            )
        ):
            ttk.Button(
                suggestions,
                text=question,
                bootstyle="secondary-outline",
                command=lambda value=question: self.set_input(value),
            ).pack(side=LEFT, padx=(0 if index == 0 else 6, 0))

        self.input_text = scrolledtext.ScrolledText(
            composer,
            height=3,
            wrap=WORD,
            font=("Microsoft YaHei UI", 10),
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=8,
            background=COLORS["surface"],
            foreground=COLORS["text"],
            insertbackground=COLORS["text"],
            selectbackground=COLORS["primary_soft"],
            selectforeground=COLORS["text"],
        )
        self.input_text.grid(row=1, column=0, sticky=EW, padx=(0, 8))
        self.input_text.bind("<Control-Return>", self.on_ctrl_enter)
        self.send_btn = ttk.Button(
            composer,
            text="发送",
            bootstyle="primary",
            command=self.send,
        )
        self.send_btn.grid(row=1, column=1, sticky=NS)
        ttk.Label(
            composer,
            text="Ctrl + Enter 发送 · 当前对话会继承右侧范围",
            style="CardText.TLabel",
        ).grid(row=2, column=0, columnspan=2, sticky=W, pady=(6, 0))

    def _build_context_panel(self, workspace):
        panel = ttk.Frame(
            workspace,
            style="Card.TFrame",
            padding=(13, 12),
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

        self._context_section(panel, 2, "项目范围")
        self.scope_combo = ttk.Combobox(
            panel,
            textvariable=self.scope_var,
            state="readonly",
        )
        self.scope_combo.grid(row=3, column=0, sticky=EW, pady=(4, 10))
        self.scope_combo.bind("<<ComboboxSelected>>", self.on_scope_changed)

        self._context_section(panel, 4, "已识别条件")
        context_box = ttk.Frame(panel, style="Card.TFrame")
        context_box.grid(row=5, column=0, sticky=EW, pady=(4, 10))
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

        self._context_section(panel, 6, "数据口径")
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
        policy.grid(row=7, column=0, sticky=EW, pady=(4, 10))
        self._context_labels.append(policy)

        self._context_section(panel, 8, "本次已加载")
        modules_label = ttk.Label(
            panel,
            textvariable=self.context_modules_var,
            style="CardText.TLabel",
            wraplength=205,
            justify=LEFT,
        )
        modules_label.grid(row=9, column=0, sticky=EW, pady=(4, 10))
        self._context_labels.append(modules_label)

        ttk.Button(
            panel,
            text="清空时间、供应商和材料条件",
            bootstyle="secondary-outline",
            command=self.clear_context,
        ).grid(row=12, column=0, sticky=EW)

    @staticmethod
    def _context_section(parent, row, text):
        box = ttk.Frame(parent, style="Card.TFrame")
        box.grid(row=row, column=0, sticky=EW)
        ttk.Separator(box).pack(fill=X, pady=(0, 7))
        ttk.Label(box, text=text, style="ContextValue.TLabel").pack(anchor=W)

    def load_projects(self):
        self.scope_map = {"全公司经营总览": None}
        for project in project_service.list_projects(active_only=False):
            label = f"{project['name']} · {project['project_code']}"
            self.scope_map[label] = project["id"]
        self.scope_combo["values"] = list(self.scope_map)

    def check_config(self):
        cfg = ai_engine.get_ai_config()
        self.model_status_var.set(
            f"本地知识库可用 · {cfg['model']}"
            if cfg["api_key"]
            else "本地知识库可用 · DeepSeek 未配置"
        )

    def refresh_conversation_list(self, select_id=None):
        conversations = ai_conversation_service.list_conversations()
        if not conversations:
            created = ai_conversation_service.create_conversation()
            conversations = [created]
        if select_id is None:
            select_id = self.current_conversation_id or conversations[0]["id"]
        valid_ids = {item["id"] for item in conversations}
        if select_id not in valid_ids:
            select_id = conversations[0]["id"]

        self._loading_sessions = True
        for item_id in self.conversation_tree.get_children():
            self.conversation_tree.delete(item_id)
        for conversation in conversations:
            title = conversation.get("title") or "新对话"
            self.conversation_tree.insert(
                "",
                END,
                iid=str(conversation["id"]),
                text=title,
            )
        self.conversation_tree.selection_set(str(select_id))
        self.conversation_tree.focus(str(select_id))
        self.conversation_tree.see(str(select_id))
        self._loading_sessions = False
        self.load_conversation(select_id)

    def load_conversation(self, conversation_id):
        conversation = ai_conversation_service.get_conversation(conversation_id)
        self.current_conversation_id = conversation["id"]
        self.current_context = dict(conversation.get("context") or {})
        self.conversation_title_var.set(conversation.get("title") or "新对话")
        scope_text = conversation.get("project_name") or "全公司"
        self.conversation_meta_var.set(
            f"{scope_text}\n{self._format_updated(conversation.get('updated_at'))}"
        )
        self._loading_context = True
        self.scope_var.set(self._scope_label(conversation.get("project_id")))
        self._loading_context = False
        self.render_messages(
            ai_conversation_service.list_messages(conversation["id"])
        )
        self.update_context_panel(conversation)
        if not self.busy:
            self.turn_status_var.set("准备就绪 · 可以继续追问")

    def render_messages(self, messages):
        for child in self.thread.winfo_children():
            child.destroy()
        self._wrap_labels = []
        if not messages:
            self._render_welcome()
        else:
            for message in messages:
                self._render_message(message)
        self.thread.after_idle(self.thread.enable_scrolling)
        self.thread.after_idle(lambda: self.thread.yview_moveto(1.0))

    def _render_welcome(self):
        message = {
            "role": "assistant",
            "message_type": "notice",
            "content": (
                "这是一个新的经营对话。你可以直接问“锦帆那里今年买了多少东西”，"
                "也可以先在右侧限定项目。后续追问会沿用已经确认的范围。"
            ),
            "metadata": {"answer_mode": "local"},
            "created_at": "",
        }
        self._render_message(message)

    def _render_message(self, message):
        role = message.get("role")
        metadata = message.get("metadata") or {}
        outer = ttk.Frame(
            self.thread,
            style="ChatThread.TFrame",
            padding=(0, 7),
        )
        outer.pack(fill=X)
        outer.columnconfigure(1, weight=1)

        mode = metadata.get("answer_mode")
        if role == "user":
            sender = "你"
            frame_style = "ChatUser.TFrame"
            label_style = "ChatUser.TLabel"
        else:
            sender = "AI 经营助手"
            if mode == "local":
                sender += "\n本地台账计算"
            elif mode == "deepseek":
                sender += "\nDeepSeek 分析"
            frame_style = "ChatAssistant.TFrame"
            label_style = "ChatAssistant.TLabel"

        ttk.Label(
            outer,
            text=sender,
            style="ChatMeta.TLabel",
            justify=LEFT,
        ).grid(row=0, column=0, sticky=NW, padx=(0, 14))
        content = ttk.Frame(outer, style=frame_style)
        content.grid(row=0, column=1, sticky=EW)
        label = ttk.Label(
            content,
            text=message.get("content") or "",
            style=label_style,
            justify=LEFT,
            wraplength=510,
        )
        label.pack(anchor=W, fill=X)
        self._wrap_labels.append((label, 130))

        if message.get("message_type") == "confirmation":
            self._render_confirmation_actions(content, message, metadata)
        elif message.get("message_type") == "answer":
            self._render_answer_actions(content, metadata)
        elif message.get("message_type") == "error":
            ttk.Button(
                content,
                text="重试这次问题",
                bootstyle="secondary-outline",
                command=lambda q=metadata.get("question"): self.retry_question(q),
            ).pack(anchor=W, pady=(9, 0))
        ttk.Separator(outer).grid(
            row=1, column=0, columnspan=2, sticky=EW, pady=(12, 0)
        )

    def _render_confirmation_actions(self, parent, message, metadata):
        pending = self.current_context.get("pending_confirmation") or {}
        is_pending = pending.get("message_id") in (None, message.get("id")) and bool(
            pending
        )
        if not is_pending:
            ttk.Label(
                parent,
                text="这项确认已经处理；后续回答使用右侧当前上下文。",
                style="ChatAssistant.TLabel",
            ).pack(anchor=W, pady=(9, 0))
            return

        for candidate in metadata.get("candidates") or []:
            candidate_frame = ttk.Frame(
                parent,
                style="ChatCandidate.TFrame",
                padding=(10, 9),
            )
            candidate_frame.pack(fill=X, pady=(9, 0))
            title = ttk.Label(
                candidate_frame,
                text=candidate.get("label") or "未命名候选",
                style="ChatCandidateTitle.TLabel",
                justify=LEFT,
                wraplength=450,
            )
            title.pack(anchor=W)
            self._wrap_labels.append((title, 130))
            ttk.Label(
                candidate_frame,
                text=candidate.get("subtitle") or "",
                style="ChatCandidateText.TLabel",
            ).pack(anchor=W, pady=(3, 0))
            actions = ttk.Frame(candidate_frame, style="ChatCandidate.TFrame")
            actions.pack(fill=X, pady=(8, 0))
            ttk.Button(
                actions,
                text="确认并继续",
                bootstyle="primary",
                command=lambda item=candidate, data=metadata: self.confirm_candidate(
                    data, item
                ),
            ).pack(side=LEFT)
            source = candidate.get("source") or {}
            if source.get("details"):
                ttk.Button(
                    actions,
                    text="查看候选记录",
                    bootstyle="secondary-outline",
                    command=lambda value=source: self.open_source_records(value),
                ).pack(side=LEFT, padx=(7, 0))
        ttk.Button(
            parent,
            text="取消本次限定",
            bootstyle="link",
            command=self.cancel_confirmation,
        ).pack(anchor=W, pady=(8, 0))

    def _render_answer_actions(self, parent, metadata):
        sources = metadata.get("sources") or []
        if sources:
            ttk.Separator(parent).pack(fill=X, pady=(10, 8))
            source_row = ttk.Frame(parent, style="ChatAssistant.TFrame")
            source_row.pack(fill=X)
            for source in sources[:3]:
                ttk.Button(
                    source_row,
                    text=source.get("label") or source.get("module") or "查看数据来源",
                    bootstyle="secondary-outline",
                    command=lambda value=source: self.open_source_records(value),
                ).pack(side=LEFT, padx=(0, 7))
        question = metadata.get("question")
        if question:
            ttk.Button(
                parent,
                text="重新生成",
                bootstyle="link",
                command=lambda value=question: self.retry_question(value),
            ).pack(anchor=W, pady=(7, 0))

    def send(self):
        self.dispatch_question(
            self.input_text.get("1.0", "end").strip(),
            append_user=True,
        )

    def retry_question(self, question):
        if question:
            self.dispatch_question(question, append_user=False)

    def dispatch_question(self, question, append_user=True):
        question = str(question or "").strip()
        if not question:
            messagebox.showwarning("提示", "请输入需要分析的经营问题。")
            self.input_text.focus_set()
            return
        if self.busy:
            messagebox.showinfo("正在分析", "请等待当前回答完成后再继续提问。")
            return
        if not self.current_conversation_id:
            self.new_conversation()

        conversation_id = self.current_conversation_id
        conversation = ai_conversation_service.get_conversation(conversation_id)
        history = ai_conversation_service.list_messages(conversation_id, limit=12)
        if append_user:
            ai_conversation_service.add_message(
                conversation_id,
                "user",
                question,
                message_type="text",
            )
            if (conversation.get("title") or "") == "新对话":
                ai_conversation_service.update_title(
                    conversation_id,
                    self._title_from_question(question),
                )
            self.input_text.delete("1.0", "end")
            self.render_messages(
                ai_conversation_service.list_messages(conversation_id)
            )

        conversation = ai_conversation_service.get_conversation(conversation_id)
        project_id = conversation.get("project_id")
        context = dict(conversation.get("context") or {})
        self._set_busy(True)
        self._schedule_turn_poll()

        def task():
            try:
                result = ai_engine.ask_ai_turn(
                    question,
                    project_id=project_id,
                    conversation_context=context,
                    history=history,
                )
                updated = ai_conversation_service.update_context(
                    conversation_id,
                    result.get("context_updates") or {},
                )
                metadata = {
                    "question": question,
                    "answer_mode": result.get("answer_mode"),
                    "sources": result.get("sources") or [],
                    "candidates": result.get("candidates") or [],
                }
                assistant_message = ai_conversation_service.add_message(
                    conversation_id,
                    "assistant",
                    result["answer"],
                    message_type=result.get("message_type") or "answer",
                    metadata=metadata,
                )
                if result.get("response_type") == "confirmation":
                    pending = dict(
                        (updated.get("context") or {}).get(
                            "pending_confirmation"
                        )
                        or {}
                    )
                    pending["message_id"] = assistant_message["id"]
                    ai_conversation_service.update_context(
                        conversation_id,
                        {"pending_confirmation": pending},
                    )
            except AIError as error:
                error_text = str(error or "未知 AI 错误")
                ai_conversation_service.add_message(
                    conversation_id,
                    "assistant",
                    f"这次分析没有完成。\n\n原因：{error_text}\n\n你可以检查 AI 设置后重试；本地台账没有被修改。",
                    message_type="error",
                    metadata={
                        "question": question,
                        "error_code": error.code,
                        "retryable": error.retryable,
                    },
                )
            except Exception as error:
                ai_conversation_service.add_message(
                    conversation_id,
                    "assistant",
                    (
                        "这次分析没有完成。\n\n"
                        f"本地处理异常：{type(error).__name__}：{error}\n\n"
                        "业务台账没有被修改。"
                    ),
                    message_type="error",
                    metadata={"question": question},
                )
            self._turn_results.put(conversation_id)

        threading.Thread(target=task, daemon=True).start()

    def _schedule_turn_poll(self):
        if self._turn_poll_scheduled:
            return
        self._turn_poll_scheduled = True
        self.parent.after(40, self._poll_turn_results)

    def _poll_turn_results(self):
        self._turn_poll_scheduled = False
        try:
            conversation_id = self._turn_results.get_nowait()
        except queue.Empty:
            if self.busy and self.parent.winfo_exists():
                self._schedule_turn_poll()
            return
        self._finish_turn(conversation_id)

    def _set_busy(self, busy):
        self.busy = busy
        self.send_btn.config(state="disabled" if busy else "normal")
        if busy:
            self.turn_status_var.set("正在读取本地经营台账并分析…")
            self.pending_frame = ttk.Frame(
                self.thread,
                style="ChatAssistant.TFrame",
                padding=(12, 9),
            )
            self.pending_frame.pack(
                anchor=W,
                fill=X,
                padx=(4, 44),
                pady=(0, 12),
            )
            ttk.Label(
                self.pending_frame,
                text="正在核对项目、供应商、材料、时间范围和数据口径…",
                style="ChatAssistant.TLabel",
            ).pack(anchor=W)
            self.thread.after_idle(lambda: self.thread.yview_moveto(1.0))
        elif self.pending_frame and self.pending_frame.winfo_exists():
            self.pending_frame.destroy()
            self.pending_frame = None

    def _finish_turn(self, conversation_id):
        self._set_busy(False)
        self.check_config()
        self.refresh_conversation_list(
            select_id=(
                conversation_id
                if self.current_conversation_id == conversation_id
                else self.current_conversation_id
            )
        )

    def confirm_candidate(self, message_metadata, candidate):
        if self.busy or not self.current_conversation_id:
            return
        updates = dict(candidate.get("context_updates") or {})
        updates["pending_confirmation"] = None
        ai_conversation_service.update_context(
            self.current_conversation_id,
            updates,
        )
        ai_conversation_service.add_message(
            self.current_conversation_id,
            "user",
            f"确认：{candidate.get('label') or '该候选'}",
            message_type="text",
            metadata={"action": "confirm_entity"},
        )
        question = message_metadata.get("question")
        self.load_conversation(self.current_conversation_id)
        self.dispatch_question(question, append_user=False)

    def cancel_confirmation(self):
        if not self.current_conversation_id:
            return
        ai_conversation_service.update_context(
            self.current_conversation_id,
            {"pending_confirmation": None},
        )
        ai_conversation_service.add_message(
            self.current_conversation_id,
            "assistant",
            "已取消这次对象限定。你可以换一种说法，或直接在右侧调整分析范围。",
            message_type="notice",
            metadata={"answer_mode": "local"},
        )
        self.load_conversation(self.current_conversation_id)

    @staticmethod
    def _procurement_breakdowns(details):
        by_month = defaultdict(int)
        by_material = defaultdict(lambda: {"amount_minor": 0, "count": 0})
        by_project = defaultdict(lambda: {"amount_minor": 0, "count": 0})
        for item in details:
            amount = int(item.get("amount_cents") or 0)
            month = str(item.get("purchase_date") or "")[:7]
            if month:
                by_month[month] += amount
            material = item.get("material") or "未命名材料"
            by_material[material]["amount_minor"] += amount
            by_material[material]["count"] += 1
            project = item.get("project") or "未归集项目"
            by_project[project]["amount_minor"] += amount
            by_project[project]["count"] += 1

        monthly = [
            {"month": month, "amount_minor": amount}
            for month, amount in sorted(by_month.items())
        ]
        ranking_source = by_material
        ranking_title = "材料金额排行"
        ranking_subtitle = "按含税材料金额排序，不含运费"
        if len(by_material) < 2 and len(by_project) >= 2:
            ranking_source = by_project
            ranking_title = "项目材料金额排行"
            ranking_subtitle = "按项目汇总含税材料金额，不含运费"
        ranking = [
            {
                "label": label,
                "amount_minor": values["amount_minor"],
                "detail": f"{values['count']} 条明细",
            }
            for label, values in ranking_source.items()
        ]
        return monthly, ranking, ranking_title, ranking_subtitle

    @staticmethod
    def _source_kpis(source):
        summary = source.get("summary") or {}
        if source.get("view_type") == "labor":
            return (
                ("人工成本", AIAssistantPage._money(summary.get("total_minor"))),
                ("工天记录", f"{int(summary.get('record_count') or 0)} 条"),
                ("涉及工人", f"{int(summary.get('worker_count') or 0)} 人"),
                ("合计工天", f"{summary.get('work_days') or 0:g} 工天"),
            )
        return (
            ("采购总额", AIAssistantPage._money(summary.get("total_minor"))),
            ("含税材料", AIAssistantPage._money(summary.get("material_minor"))),
            ("运费", AIAssistantPage._money(summary.get("freight_minor"))),
            (
                "明细 / 材料",
                f"{int(summary.get('record_count') or 0)} 条 · "
                f"{int(summary.get('material_count') or 0)} 种",
            ),
        )

    @staticmethod
    def _source_table_spec(view_type):
        if view_type == "labor":
            columns = (
                "date", "worker", "project", "site", "work_type",
                "days", "overtime", "amount",
            )
            headings = {
                "date": "日期", "worker": "工人", "project": "项目",
                "site": "施工地点", "work_type": "工作内容",
                "days": "工天", "overtime": "加班", "amount": "人工金额",
            }
            widths = {
                "date": 92, "worker": 100, "project": 120, "site": 150,
                "work_type": 180, "days": 70, "overtime": 60, "amount": 105,
            }

            def values(item):
                return (
                    item.get("work_date") or "",
                    item.get("worker_name") or "",
                    item.get("project_name") or "待归集",
                    item.get("construction_site") or "",
                    item.get("work_type") or "",
                    f"{item.get('work_days') or 0:g}",
                    "是" if item.get("is_overtime") else "否",
                    AIAssistantPage._money(item.get("amount_minor")),
                )
            numeric = {"days", "amount"}
            return columns, headings, widths, numeric, values

        columns = (
            "date", "order", "project", "supplier", "material",
            "spec", "quantity", "amount",
        )
        headings = {
            "date": "采购日期", "order": "采购单号", "project": "项目",
            "supplier": "供应商", "material": "材料", "spec": "规格",
            "quantity": "数量", "amount": "含税材料金额",
        }
        widths = {
            "date": 90, "order": 125, "project": 125, "supplier": 170,
            "material": 135, "spec": 135, "quantity": 90, "amount": 115,
        }

        def values(item):
            quantity = f"{item.get('quantity') or ''}{item.get('unit') or ''}"
            return (
                item.get("purchase_date") or "",
                item.get("order_no") or "",
                item.get("project") or "未归集项目",
                item.get("supplier") or "",
                item.get("material") or "",
                item.get("specification") or "",
                quantity,
                AIAssistantPage._money(item.get("amount_cents")),
            )
        return columns, headings, widths, {"quantity", "amount"}, values

    def open_source_records(self, source):
        details = list(source.get("details") or [])
        view_type = source.get("view_type") or "procurement"
        dialog = ttk.Toplevel(self.parent)
        dialog.title(source.get("label") or "经营数据透视")
        style_dialog(
            dialog,
            self.parent,
            1120,
            720,
            resizable=True,
            min_width=900,
            min_height=600,
        )
        footer = ttk.Frame(dialog, padding=(14, 8))
        footer.pack(side=BOTTOM, fill=X)
        body = ttk.Frame(dialog, padding=(18, 14))
        body.pack(fill=BOTH, expand=True)
        ttk.Label(
            body,
            text=source.get("module") or "本地经营数据",
            style="CardTitle.TLabel",
        ).pack(anchor=W)
        ttk.Label(
            body,
            text=f"{source.get('scope_label') or '当前范围'} · 数据来自已生效台账",
            style="PageSub.TLabel",
        ).pack(anchor=W, pady=(2, 10))

        kpi_strip = ttk.Frame(body)
        kpi_strip.pack(fill=X, pady=(0, 10))
        for index, (label, value) in enumerate(self._source_kpis(source)):
            card = ttk.Frame(kpi_strip, style="Card.TFrame", padding=(12, 9))
            card.grid(
                row=0,
                column=index,
                sticky=EW,
                padx=(0 if index == 0 else 5, 0 if index == 3 else 5),
            )
            ttk.Label(card, text=label, style="KpiLabel.TLabel").pack(anchor=W)
            ttk.Label(card, text=value, style="SummaryValue.TLabel").pack(
                anchor=W, pady=(3, 0)
            )
            kpi_strip.columnconfigure(index, weight=1)

        if view_type == "labor":
            monthly = list(source.get("by_month") or [])
            ranking = list(source.get("by_rank") or [])
            monthly_title = "月度人工成本"
            monthly_subtitle = "按工天日期汇总，金额从零基线比较"
            ranking_title = "人员人工成本排行"
            ranking_subtitle = "按金额排序，同时显示工天与记录数"
        else:
            monthly, ranking, ranking_title, ranking_subtitle = (
                self._procurement_breakdowns(details)
            )
            monthly_title = "月度含税材料金额"
            monthly_subtitle = "按采购日期汇总，不含运费"

        charts = ttk.Frame(body)
        charts.pack(fill=X, pady=(0, 10))
        trend_card = ttk.Frame(charts, style="Card.TFrame", padding=(12, 10))
        trend_card.grid(row=0, column=0, sticky=NSEW, padx=(0, 5))
        ttk.Label(trend_card, text=monthly_title, style="CardTitle.TLabel").pack(anchor=W)
        ttk.Label(trend_card, text=monthly_subtitle, style="CardText.TLabel").pack(
            anchor=W, pady=(2, 6)
        )
        trend_chart = MonthlyBarChart(trend_card, height=220)
        trend_chart.pack(fill=BOTH, expand=True)
        trend_chart.set_data(monthly)

        rank_card = ttk.Frame(charts, style="Card.TFrame", padding=(12, 10))
        rank_card.grid(row=0, column=1, sticky=NSEW, padx=(5, 0))
        ttk.Label(rank_card, text=ranking_title, style="CardTitle.TLabel").pack(anchor=W)
        ttk.Label(rank_card, text=ranking_subtitle, style="CardText.TLabel").pack(
            anchor=W, pady=(2, 6)
        )
        rank_chart = HorizontalBreakdown(
            rank_card,
            limit=6,
            height=220,
            empty_text=(
                "当前范围暂无人工成本"
                if view_type == "labor"
                else "当前范围暂无材料采购"
            ),
            other_label="其他人员" if view_type == "labor" else "其他材料",
        )
        rank_chart.pack(fill=BOTH, expand=True)
        rank_chart.set_data(ranking)
        charts.columnconfigure(0, weight=1, uniform="source_chart")
        charts.columnconfigure(1, weight=1, uniform="source_chart")

        detail_header = ttk.Frame(body)
        detail_header.pack(fill=X, pady=(0, 6))
        ttk.Label(detail_header, text="详细台账", style="CardTitle.TLabel").pack(side=LEFT)
        ttk.Label(
            detail_header,
            text=f"共 {source.get('record_count', len(details))} 条，可滚动核对",
            style="CardText.TLabel",
        ).pack(side=LEFT, padx=(10, 0))
        table_box = ttk.Frame(body)
        table_box.pack(fill=BOTH, expand=True)
        columns, headings, widths, numeric, row_values = self._source_table_spec(
            view_type
        )
        tree = ttk.Treeview(table_box, columns=columns, show="headings")
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(
                column,
                width=widths[column],
                minwidth=70,
                anchor=E if column in numeric else W,
            )
        for item in details:
            tree.insert("", END, values=row_values(item))
        if not details:
            tree.insert("", END, values=("当前范围暂无逐条明细",) + ("",) * (len(columns) - 1))
        vertical = ttk.Scrollbar(table_box, orient=VERTICAL, command=tree.yview)
        horizontal = ttk.Scrollbar(
            table_box,
            orient=HORIZONTAL,
            command=tree.xview,
        )
        tree.configure(
            yscrollcommand=vertical.set,
            xscrollcommand=horizontal.set,
        )
        tree.grid(row=0, column=0, sticky=NSEW)
        vertical.grid(row=0, column=1, sticky=NS)
        horizontal.grid(row=1, column=0, sticky=EW)
        table_box.rowconfigure(0, weight=1)
        table_box.columnconfigure(0, weight=1)
        ttk.Button(
            footer,
            text="关闭",
            bootstyle="secondary",
            command=dialog.destroy,
        ).pack(side=RIGHT)

    def new_conversation(self):
        if self.busy:
            messagebox.showinfo("正在分析", "请等待当前回答完成后再新建对话。")
            return
        current_project_id = self.scope_map.get(self.scope_var.get())
        created = ai_conversation_service.create_conversation(
            project_id=current_project_id,
        )
        self.refresh_conversation_list(select_id=created["id"])
        self.input_text.focus_set()

    def archive_current_conversation(self):
        if not self.current_conversation_id or self.busy:
            return
        if not messagebox.askyesno(
            "归档对话",
            "归档后，这个对话不会出现在左侧最近列表中。业务台账不会受到影响。",
        ):
            return
        ai_conversation_service.archive_conversation(self.current_conversation_id)
        self.current_conversation_id = None
        self.refresh_conversation_list()

    def clear_context(self):
        if not self.current_conversation_id:
            return
        project_id = self.scope_map.get(self.scope_var.get())
        ai_conversation_service.replace_context(
            self.current_conversation_id,
            context={},
            project_id=project_id,
        )
        ai_conversation_service.add_message(
            self.current_conversation_id,
            "assistant",
            "已清除本次对话的时间、供应商和材料条件；项目范围保持不变。",
            message_type="notice",
            metadata={"answer_mode": "local"},
        )
        self.load_conversation(self.current_conversation_id)

    def on_conversation_selected(self, _event=None):
        if self._loading_sessions:
            return
        selected = self.conversation_tree.selection()
        if selected:
            self.load_conversation(int(selected[0]))

    def on_scope_changed(self, _event=None):
        if self._loading_context or not self.current_conversation_id:
            return
        project_id = self.scope_map.get(self.scope_var.get())
        ai_conversation_service.update_context(
            self.current_conversation_id,
            {},
            project_id=project_id or 0,
        )
        self.load_conversation(self.current_conversation_id)

    def update_context_panel(self, conversation):
        context = dict(conversation.get("context") or {})
        self.current_context = context
        self.context_project_var.set(
            conversation.get("project_name") or "全公司"
        )
        time_scope = context.get("time") or {}
        self.context_time_var.set(
            f"时间：{time_scope.get('label')}"
            if time_scope.get("label")
            else "时间：未限定"
        )
        self.context_supplier_var.set(
            f"供应商：{context.get('supplier_name')}"
            if context.get("supplier_name")
            else "供应商：未限定"
        )
        self.context_material_var.set(
            f"材料：{context.get('material_name')}"
            if context.get("material_name")
            else "材料：未限定"
        )
        pending = context.get("pending_confirmation") or {}
        if pending:
            self.context_pending_var.set(
                f"待确认：{pending.get('label') or '业务对象'}"
                f"（{pending.get('candidate_count', 0)} 个候选）"
            )
        else:
            self.context_pending_var.set("待确认：没有")
        modules = context.get("data_modules") or []
        self.context_modules_var.set(
            "、".join(modules) if modules else "尚未读取业务模块"
        )

    def set_input(self, text):
        self.input_text.delete("1.0", "end")
        if text:
            self.input_text.insert("1.0", text)
        self.input_text.focus_set()

    def on_ctrl_enter(self, _event):
        self.send()
        return "break"

    def _on_chat_resize(self, event):
        wrap = max(250, int(event.width) - 120)
        for label, offset in list(self._wrap_labels):
            if label.winfo_exists():
                label.configure(wraplength=max(220, wrap - max(0, offset - 100)))

    def _on_context_resize(self, event):
        wrap = max(150, int(event.width) - 34)
        for label in self._context_labels:
            if label.winfo_exists():
                label.configure(wraplength=wrap)

    def _scope_label(self, project_id):
        for label, value in self.scope_map.items():
            if value == project_id:
                return label
        return "全公司经营总览"

    @staticmethod
    def _title_from_question(question):
        clean = " ".join(str(question or "").split())
        return clean if len(clean) <= 20 else clean[:20] + "…"

    @staticmethod
    def _format_updated(value):
        try:
            parsed = datetime.fromisoformat(str(value))
            return parsed.strftime("%m-%d %H:%M")
        except (TypeError, ValueError):
            return str(value or "")

    @staticmethod
    def _money(cents):
        return f"¥{int(cents or 0) / 100:,.2f}"

    def open_config_dialog(self):
        dialog = ttk.Toplevel(self.parent)
        dialog.title("配置 DeepSeek")
        body, footer = build_form_dialog(
            dialog,
            self.parent,
            610,
            470,
            min_width=540,
            min_height=390,
        )
        cfg = ai_engine.get_ai_config()
        key_var = ttk.StringVar(value=cfg["api_key"])
        model_var = ttk.StringVar(value=cfg["model"] or DEFAULT_MODEL)
        base_var = ttk.StringVar(value=cfg["api_base"] or DEFAULT_API_BASE)
        proxy_var = ttk.BooleanVar(value=cfg.get("use_system_proxy", False))
        test_status_var = ttk.StringVar(value="可先测试连接，再保存配置。")

        fields = (
            ("API Key *", ttk.Entry(body, textvariable=key_var, show="*")),
            (
                "模型 *",
                ttk.Combobox(
                    body,
                    textvariable=model_var,
                    values=("deepseek-v4-flash", "deepseek-v4-pro"),
                    state="normal",
                ),
            ),
            ("API Base *", ttk.Entry(body, textvariable=base_var)),
        )
        for row, (label, widget) in enumerate(fields):
            ttk.Label(body, text=label).grid(
                row=row, column=0, sticky=E, padx=(0, 12), pady=8
            )
            widget.grid(row=row, column=1, sticky=EW, pady=8, ipady=5)
        ttk.Checkbutton(
            body,
            text="使用系统代理",
            variable=proxy_var,
            bootstyle="round-toggle",
        ).grid(row=3, column=1, sticky=W, pady=8)
        ttk.Label(
            body,
            text=(
                "本地事实查询不依赖模型；复杂经营分析使用 DeepSeek。"
                "密钥只保存在本机 config.ini。"
            ),
            style="PageSub.TLabel",
            wraplength=470,
            justify=LEFT,
        ).grid(row=4, column=0, columnspan=2, sticky=W, pady=(6, 10))
        ttk.Label(
            body,
            textvariable=test_status_var,
            style="PageSub.TLabel",
            wraplength=470,
            justify=LEFT,
        ).grid(row=5, column=0, columnspan=2, sticky=W)
        body.columnconfigure(1, weight=1)

        def config_values():
            return {
                "api_key": key_var.get().strip(),
                "model": model_var.get().strip(),
                "api_base": base_var.get().strip().rstrip("/"),
                "use_system_proxy": proxy_var.get(),
            }

        def validate_config():
            values = config_values()
            if not all((values["api_key"], values["model"], values["api_base"])):
                messagebox.showwarning(
                    "提示",
                    "请完整填写 API Key、模型和 API Base。",
                    parent=dialog,
                )
                return None
            return values

        def save_config():
            values = validate_config()
            if not values:
                return
            ai_engine.save_ai_config(**values)
            self.check_config()
            dialog.destroy()
            messagebox.showinfo("成功", "DeepSeek 配置已保存。")

        def finish_test(result=None, error=None):
            test_button.config(state="normal")
            if error:
                test_status_var.set(f"连接失败：{error}")
                return
            models = "、".join(result["models"])
            test_status_var.set(
                f"连接成功；当前模型：{result['model']}；账户可用模型：{models}"
            )

        def test_connection():
            values = validate_config()
            if not values:
                return
            test_button.config(state="disabled")
            test_status_var.set("正在连接 DeepSeek 并验证模型权限…")

            def task():
                try:
                    result = ai_engine.test_ai_connection(values)
                except Exception as caught_error:
                    message = str(caught_error or "未知连接错误")
                    self.parent.after(
                        0,
                        lambda value=message: finish_test(error=value),
                    )
                    return
                self.parent.after(
                    0,
                    lambda value=result: finish_test(result=value),
                )

            threading.Thread(target=task, daemon=True).start()

        test_button = ttk.Button(
            footer,
            text="测试连接",
            bootstyle="secondary-outline",
            command=test_connection,
        )
        test_button.pack(side=LEFT)
        add_form_actions(
            footer,
            cancel_command=dialog.destroy,
            primary_text="保存配置",
            primary_command=save_config,
        )
