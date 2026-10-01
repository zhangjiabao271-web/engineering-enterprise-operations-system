"""AIConfigMixin: extracted UI behavior from pages/ai_page.py."""

import queue
import threading
from tkinter import messagebox

import ttkbootstrap as ttk
from ttkbootstrap.constants import *
import ai_engine
from ai_client import DEFAULT_API_BASE, DEFAULT_MODEL
from ui.dialogs import add_form_actions, build_form_dialog


class AIConfigMixin:
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
                "密钥使用当前 Windows 账户加密保存，不写入配置文件。"
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
            if not dialog.winfo_exists():
                return
            test_button.config(state="normal")
            if error:
                test_status_var.set(f"连接失败：{error}")
                return
            models = "、".join(result["models"])
            test_status_var.set(
                f"连接成功；当前模型：{result['model']}；账户可用模型：{models}"
            )

        test_results = queue.Queue(maxsize=1)
        poll_host = dialog.winfo_toplevel()

        def poll_test_result():
            if not dialog.winfo_exists():
                return
            try:
                result, error = test_results.get_nowait()
            except queue.Empty:
                poll_host.after(50, poll_test_result)
                return
            finish_test(result=result, error=error)

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
                    test_results.put((None, message))
                    return
                test_results.put((result, None))

            poll_host.after(50, poll_test_result)
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
