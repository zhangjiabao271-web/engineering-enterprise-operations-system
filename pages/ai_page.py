import queue
import threading
from datetime import datetime
from tkinter import messagebox

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

import ai_engine
from ai_client import AIError
from services import (
    ai_conversation_service,
    ai_operating_query_service,
    project_service,
)
from ui.dialogs import safe_init_loaders
from ui.theme import COLORS, FONT_BODY
from ui.scaling import scale_px

from pages.ai_layout_mixin import AILayoutMixin
from pages.ai_source_mixin import AISourceMixin
from pages.ai_config_mixin import AIConfigMixin


class AIAssistantPage(AILayoutMixin, AISourceMixin, AIConfigMixin):
    """Continuous, read-only operating conversation workspace."""

    def __init__(self, parent, navigate_to=None):
        self.parent = parent
        self.navigate_to = navigate_to
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
        self._generation_token = 0
        self._cancel_event = None
        self._stream_text = ""
        self._stream_label = None
        self.build_ui()
        safe_init_loaders(
            "AI 经营助手",
            [
                self.load_projects,
                self.check_config,
                self.refresh_conversation_list,
                self.load_daily_briefing,
            ],
        )

    def build_ui(self):
        style = ttk.Style()
        style.configure("AssistantBubble.TFrame", background=COLORS["surface_muted"])
        style.configure("AssistantBubble.TLabel", background=COLORS["surface_muted"],
                        foreground=COLORS["text"], font=FONT_BODY, padding=0)
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
            text="聊经营、查台账，每个数字都有据可查",
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
        workspace.columnconfigure(1, weight=1)
        workspace.rowconfigure(0, weight=1)

        self._build_session_panel(workspace)
        self._build_chat_panel(workspace)
        self._build_context_panel(workspace)
        self.session_panel.grid_remove()
        self.context_panel.grid_remove()
        self._side_panel = None

    def _toggle_side_panel(self, name):
        selected = None if self._side_panel == name else name
        for key, panel, column in (("history", self.session_panel, 0),
                                   ("context", self.context_panel, 2)):
            if selected == key:
                panel.grid()
                self.workspace.columnconfigure(column, minsize=scale_px(self.parent, 230))
            else:
                panel.grid_remove()
                self.workspace.columnconfigure(column, minsize=0)
        self._side_panel = selected





    def load_projects(self):
        self.scope_map = {"全公司经营总览": None}
        for project in project_service.list_projects(active_only=False):
            label = f"{project['name']} · {project['project_code']}"
            self.scope_map[label] = project["id"]
        self.scope_combo["values"] = list(self.scope_map)

    def load_daily_briefing(self):
        briefing = ai_operating_query_service.get_daily_briefing()
        for widget in self.suggestions.winfo_children():
            widget.destroy()
        suggestions = briefing.get("suggestions") or [
            {"label": "数据缺口", "question": "哪些项目还有数据缺口？"}
        ]
        for index, suggestion in enumerate(suggestions[:3]):
            question = suggestion.get("question") or ""
            ttk.Button(
                self.suggestions,
                text=suggestion.get("label") or question,
                bootstyle="secondary-outline",
                command=lambda value=question: self.set_input(value),
            ).pack(side=LEFT, padx=(0 if index == 0 else 6, 0))

        for widget in self.reminder_frame.winfo_children():
            widget.destroy()
        for row, reminder in enumerate((briefing.get("reminders") or [])[:5]):
            card = ttk.Frame(
                self.reminder_frame,
                style="ChatCandidate.TFrame",
                padding=(8, 7),
            )
            card.grid(row=row, column=0, sticky=EW, pady=(0, 5))
            card.columnconfigure(0, weight=1)
            ttk.Label(
                card,
                text=reminder.get("title") or "经营提醒",
                style="ChatCandidateTitle.TLabel",
            ).grid(row=0, column=0, sticky=W)
            summary_label = ttk.Label(
                card,
                text=reminder.get("value") or "--",
                style="ChatCandidateText.TLabel",
                wraplength=205,
                justify=LEFT,
            )
            summary_label.grid(row=1, column=0, sticky=EW, pady=(2, 0))
            card.bind(
                "<Configure>",
                lambda event, label=summary_label: label.configure(wraplength=max(120, event.width - 20)),
            )
            actions = ttk.Frame(card, style="ChatCandidate.TFrame")
            actions.grid(row=2, column=0, sticky=W, pady=(5, 0))
            ttk.Button(
                actions,
                text="问助手",
                bootstyle="link",
                command=lambda value=reminder.get("question"): self.set_input(value),
            ).pack(side=LEFT)
            if self.navigate_to and reminder.get("page_key"):
                ttk.Button(
                    actions,
                    text="打开页面",
                    bootstyle="link",
                    command=lambda key=reminder.get("page_key"): self.open_business_page(key),
                ).pack(side=LEFT, padx=(5, 0))

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
                "今天想了解哪一笔生意？\n\n"
                "可以问：今年的毛利率是多少？哪些项目还没收款？\n"
                "需要限定项目时，点击上方“范围与条件”。后续可以直接追问。"
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
        outer.columnconfigure(0, weight=1)

        mode = metadata.get("answer_mode")
        if role == "user":
            sender = "你"
            frame_style = "AssistantBubble.TFrame"
            label_style = "AssistantBubble.TLabel"
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
        ).grid(row=0, column=0, sticky=E if role == "user" else W, pady=(0, 6))
        content = ttk.Frame(outer, style=frame_style, padding=(16, 12))
        content.grid(row=1, column=0, sticky=E if role == "user" else EW,
                     padx=(80, 0) if role == "user" else (0, 30))
        label = ttk.Label(
            content,
            text=message.get("content") or "",
            style=label_style,
            justify=LEFT,
            wraplength=getattr(self, "_message_wrap_width", 510),
        )
        label.pack(anchor=W, fill=X)
        self._wrap_labels.append((label, 180 if role == "user" else 100))

        if message.get("message_type") == "confirmation":
            self._render_confirmation_actions(content, message, metadata)
        elif message.get("message_type") == "answer":
            self._render_answer_actions(content, message, metadata)
        elif message.get("message_type") == "error":
            ttk.Button(
                content,
                text="重试这次问题",
                bootstyle="secondary-outline",
                command=lambda q=metadata.get("question"): self.retry_question(q),
            ).pack(anchor=W, pady=(9, 0))

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

    def _render_answer_actions(self, parent, message, metadata):
        sources = metadata.get("sources") or []
        if sources:
            source_row = ttk.Frame(parent, style="ChatAssistant.TFrame")
            source_row.pack(fill=X, pady=(10, 0))
            for source in sources[:3]:
                ttk.Button(
                    source_row,
                    text=source.get("label") or source.get("module") or "查看数据来源",
                    bootstyle="secondary-outline",
                    command=lambda value=source: self.open_source_records(value),
                ).pack(anchor=W, pady=(0, 4))
        feedback = ttk.Frame(parent, style="ChatAssistant.TFrame")
        feedback.pack(anchor=W, pady=(8, 0))
        question = metadata.get("question")
        if question:
            ttk.Button(
                feedback,
                text="重新生成",
                bootstyle="link",
                command=lambda value=question: self.retry_question(value),
            ).pack(side=LEFT, padx=(0, 10))
        selected = message.get("feedback")
        for rating, text in (("useful", "有用"), ("not_useful", "没用")):
            ttk.Button(
                feedback,
                text=("✓ " if selected == rating else "") + text,
                bootstyle="link",
                command=lambda value=rating, message_id=message.get("id"): self.set_feedback(
                    message_id, value
                ),
            ).pack(side=LEFT, padx=(5, 0))

    def set_feedback(self, message_id, rating):
        if not message_id:
            return
        ai_conversation_service.set_message_feedback(message_id, rating)
        if self.current_conversation_id:
            self.load_conversation(self.current_conversation_id)

    def open_business_page(self, page_key):
        if self.navigate_to and page_key:
            self.navigate_to(page_key)

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
        self._generation_token += 1
        generation_token = self._generation_token
        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        self._set_busy(True)
        self._schedule_turn_poll()

        def on_chunk(value):
            if not cancel_event.is_set():
                self._turn_results.put(
                    {
                        "type": "stream",
                        "token": generation_token,
                        "text": value,
                    }
                )

        def task():
            try:
                result = ai_engine.ask_ai_turn(
                    question,
                    project_id=project_id,
                    conversation_context=context,
                    history=history,
                    on_chunk=on_chunk,
                    cancel_event=cancel_event,
                )
                if cancel_event.is_set():
                    return
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
                if error.code == "cancelled" or cancel_event.is_set():
                    return
                error_text = str(error or "未知 AI 错误")
                next_step = (
                    "请检查 AI 设置后重试。"
                    if error.code in {"missing_key", "authentication", "not_found", "invalid_request"}
                    else "请重试这次问题；若持续失败，可更换模型或检查连接。"
                )
                ai_conversation_service.add_message(
                    conversation_id,
                    "assistant",
                    f"这次分析没有完成。\n\n原因：{error_text}\n\n{next_step}本地台账没有被修改。",
                    message_type="error",
                    metadata={
                        "question": question,
                        "error_code": error.code,
                        "retryable": error.retryable,
                    },
                )
            except Exception as error:
                if cancel_event.is_set():
                    return
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
            finally:
                self._turn_results.put(
                    {
                        "type": "done",
                        "token": generation_token,
                        "conversation_id": conversation_id,
                        "cancelled": cancel_event.is_set(),
                    }
                )

        threading.Thread(target=task, daemon=True).start()

    def _schedule_turn_poll(self):
        if self._turn_poll_scheduled:
            return
        self._turn_poll_scheduled = True
        self.parent.after(40, self._poll_turn_results)

    def _poll_turn_results(self):
        self._turn_poll_scheduled = False
        try:
            event = self._turn_results.get_nowait()
        except queue.Empty:
            if self.busy and self.parent.winfo_exists():
                self._schedule_turn_poll()
            return
        if event.get("token") != self._generation_token:
            if self.busy:
                self._schedule_turn_poll()
            return
        if event.get("type") == "stream":
            self._update_stream(event.get("text") or "")
            self._schedule_turn_poll()
            return
        self._finish_turn(
            event.get("conversation_id"), cancelled=event.get("cancelled", False)
        )

    def _set_busy(self, busy):
        self.busy = busy
        self.send_btn.config(state="disabled" if busy else "normal")
        if busy:
            self.send_btn.grid_remove()
            self.stop_btn.grid()
        else:
            self.stop_btn.grid_remove()
            self.send_btn.grid()
        if busy:
            self._stream_text = ""
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
            self._stream_label = ttk.Label(
                self.pending_frame,
                text="正在核对项目、供应商、材料、时间范围和数据口径…",
                style="ChatAssistant.TLabel",
                justify=LEFT,
                wraplength=500,
            )
            self._stream_label.pack(anchor=W, fill=X)
            self.thread.after_idle(lambda: self.thread.yview_moveto(1.0))
        elif self.pending_frame and self.pending_frame.winfo_exists():
            self.pending_frame.destroy()
            self.pending_frame = None
            self._stream_label = None

    def _update_stream(self, text):
        self._stream_text += text
        if self._stream_label and self._stream_label.winfo_exists():
            self.turn_status_var.set("DeepSeek 正在生成回答，可随时停止")
            self._stream_label.configure(
                text=self._stream_text or "正在生成回答…"
            )
            self.thread.after_idle(lambda: self.thread.yview_moveto(1.0))

    def stop_generation(self):
        if not self.busy or not self._cancel_event:
            return
        self._cancel_event.set()
        self.turn_status_var.set("正在停止；不会保存未完成的回答…")
        self.stop_btn.config(state="disabled")

    def _finish_turn(self, conversation_id, cancelled=False):
        self._set_busy(False)
        self.stop_btn.config(state="normal")
        self._cancel_event = None
        if cancelled:
            self.turn_status_var.set("本次生成已停止，未保存未完成回答")
        self.check_config()
        self.load_daily_briefing()
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
        gutter = max(0, (int(event.width) - scale_px(self.parent, 760)) // 2)
        self.thread.grid_configure(padx=gutter)
        self.composer.grid_configure(padx=gutter)
        wrap = max(220, int(event.width) - 2*gutter - 120)
        self._message_wrap_width = wrap
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
