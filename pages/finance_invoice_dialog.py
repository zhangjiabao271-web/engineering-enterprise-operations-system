"""FinanceInvoiceDialogMixin: extracted UI behavior from pages/finance_page.py."""

from datetime import datetime
from tkinter import messagebox, filedialog

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from services import contract_service, finance_service
from ui.components import DatePicker
from ui.dialogs import add_form_actions, build_form_dialog


class FinanceInvoiceDialogMixin:
    def open_invoice_dialog(self, invoice_id=None):
        target_map = self._invoice_target_map()
        invoice = finance_service.get_invoice(invoice_id) if invoice_id else None
        if invoice_id and not invoice:
            messagebox.showwarning("提示", "发票记录不存在")
            return
        restoring = bool(invoice and invoice["status"] == "void")
        prompt = "请选择开票项目 / 合同"
        if invoice:
            target_label = next(
                (
                    label
                    for label, target in target_map.items()
                    if target["project_id"] == invoice["project_id"]
                    and target["contract_id"] == invoice["contract_id"]
                ),
                None,
            )
        else:
            target_label = (
                next(iter(target_map)) if len(target_map) == 1 else None
            )
        if restoring:
            dialog_title = "恢复并修改销项发票"
            primary_text = "恢复并保存"
            success_message = "发票已恢复并更新。"
        elif invoice:
            dialog_title = "修改销项发票"
            primary_text = "保存修改"
            success_message = "发票已更新。"
        else:
            dialog_title = "登记销项发票"
            primary_text = "保存发票"
            success_message = "发票已登记。"
        dialog = ttk.Toplevel(self.parent)
        dialog.title(dialog_title)
        body, footer = build_form_dialog(
            dialog, self.parent, 700, 590, min_width=590, min_height=450
        )
        initial_tax_rate = '0'
        if invoice:
            initial_tax_rate = f"{invoice['tax_rate_bps'] / 100:g}"
            label = invoice['tax_rate_label']
            if ' / ' in label or label == '多税率':
                initial_tax_rate = '多税率'
            elif label in ('免税', '不征税'):
                initial_tax_rate = label
        variables = {
            "target": ttk.StringVar(value=target_label or prompt),
            "no": ttk.StringVar(value=invoice["invoice_no"] if invoice else ""),
            "date": ttk.StringVar(
                value=invoice["invoice_date"]
                if invoice else datetime.now().strftime("%Y-%m-%d")
            ),
            "amount": ttk.StringVar(
                value=f"{invoice['amount_minor'] / 100:.2f}" if invoice else ""
            ),
            "tax": ttk.StringVar(value=initial_tax_rate),
            'net': ttk.StringVar(value=f"{invoice['net_amount_minor']/100:.2f}" if invoice and invoice['net_amount_minor'] is not None else ''),
            'tax_amount': ttk.StringVar(value=f"{invoice['tax_amount_minor']/100:.2f}" if invoice and invoice['tax_amount_minor'] is not None else ''),
            "buyer": ttk.StringVar(
                value=invoice["buyer_name_snapshot"] if invoice else ""
            ),
        }
        specs = (
            ("开票项目 / 合同 *", "target"),
            ("发票号码（不可重复）", "no"),
            ("开票日期 *", "date"),
            ("价税合计（元）*", "amount"),
            ("未税金额（元）", "net"),
            ("票面税额（元）", "tax_amount"),
            ("税率（% / 多税率 / 免税）", "tax"),
            ("购买方", "buyer"),
        )
        target_hint_var = ttk.StringVar()
        target_combo = None
        pdf_state = {'recognition': None, 'busy': False, 'existing_invoice': None}
        form_fields = {}
        pdf_confirmed = ttk.BooleanVar(value=False)
        pdf_hint = ttk.StringVar(value='本地识别，不上传；支持有文字的电子发票PDF。旧票税额未知可留空。')
        import_box = ttk.Frame(body)
        import_box.grid(row=0, column=0, columnspan=2, sticky=EW, pady=(0, 12))
        import_button = ttk.Button(import_box, text='选择 PDF 自动识别', bootstyle='primary-outline')
        import_button.pack(anchor=W)
        ttk.Label(import_box, textvariable=pdf_hint, wraplength=520, justify=LEFT).pack(fill=X, pady=6)
        confirm_widget = ttk.Checkbutton(import_box, text='已核对票号、日期、金额、税额及项目，随保存留存PDF附件', variable=pdf_confirmed)
        for row, (label, key) in enumerate(specs, 1):
            ttk.Label(body, text=label).grid(
                row=row, column=0, sticky=E, padx=(0, 12), pady=7
            )
            if key == "target":
                field = ttk.Frame(body)
                target_combo = ttk.Combobox(
                    field,
                    textvariable=variables[key],
                    values=[prompt, *target_map],
                    state="readonly",
                )
                target_combo.pack(fill=X, ipady=4)
                ttk.Label(
                    field,
                    textvariable=target_hint_var,
                    style="CardText.TLabel",
                ).pack(anchor=W, pady=(5, 0))
                field.grid(row=row, column=1, sticky=EW, pady=7)
            elif key == "date":
                field = DatePicker(
                    body,
                    textvariable=variables[key],
                    popup_title="选择开票日期",
                )
                field.grid(row=row, column=1, sticky=EW, pady=7)
            else:
                field = ttk.Entry(
                    body, textvariable=variables[key]
                )
                field.grid(row=row, column=1, sticky=EW, pady=7, ipady=4)
            form_fields[key] = target_combo if key == 'target' else field

        def refresh_target_hint(_event=None):
            target = target_map.get(variables["target"].get())
            if not target:
                target_hint_var.set("选择后显示项目合同的总开票进度")
                return
            if target.get('income_mode') == 'invoice':
                target_hint_var.set(
                    '随开票自动确认收入，无需另填收入确认。\n'
                    f"累计已开 {self.money(target['invoiced_minor'])}；本次金额保存后自动计入收入。"
                )
                return
            mode_hint = ''
            if target.get('income_mode') == 'receipt':
                mode_hint = '随回款补足实际结算，本次开票只关联已有结算。\n'
            target_hint_var.set(
                mode_hint +
                f"确认收入 {self.money(target['amount_minor'])} · "
                f"已开 {self.money(target['invoiced_minor'])} · "
                f"待开 {self.money(target['uninvoiced_minor'])} · "
                f"{target['settlement_count']} 笔收入确认按日期自动分配"
            )

        target_combo.bind("<<ComboboxSelected>>", refresh_target_hint)
        refresh_target_hint()

        def choose_pdf():
            from queue import Queue, Empty
            from threading import Thread
            from services import invoice_pdf_service
            path = filedialog.askopenfilename(parent=dialog, title='选择销项发票PDF', filetypes=[('PDF发票', '*.pdf')])
            if not path:
                return
            if not messagebox.askyesno('识别并填表', '识别成功后将替换本窗口的票号、日期、金额、税额及购买方。\n项目仍需核对，是否继续？', parent=dialog):
                return
            pdf_state['busy'] = True
            import_button.configure(state='disabled')
            pdf_hint.set('正在本地识别PDF，请稍候……')
            results = Queue()

            def work():
                try:
                    results.put((invoice_pdf_service.recognize(path), None))
                except Exception as error:
                    results.put((None, str(error)))

            def finish():
                if not dialog.winfo_exists():
                    return
                try:
                    result, error = results.get_nowait()
                except Empty:
                    dialog.after(100, finish)
                    return
                pdf_state['busy'] = False
                import_button.configure(state='normal')
                if error:
                    pdf_hint.set('本次识别失败，原表单及已选附件未改变。')
                    messagebox.showwarning('无法识别', error, parent=dialog)
                    return
                try:
                    existing = invoice_pdf_service.find_existing_invoice_for_pdf(result)
                except Exception as error:
                    pdf_hint.set('票号匹配失败，原表单及已选附件未改变。')
                    messagebox.showwarning('无法匹配原发票', str(error), parent=dialog)
                    return
                pdf_state['recognition'] = result
                pdf_state['existing_invoice'] = existing
                pdf_confirmed.set(False)
                set_form_enabled(not existing)
                primary_button.configure(text=primary_text, state='normal')
                confirm_widget.configure(text='已核对票号、日期、金额、税额及项目，随保存留存PDF附件')
                for key, field in [('no', 'invoice_no'), ('date', 'invoice_date'), ('amount', 'amount'),
                                   ('net', 'net_amount'), ('tax_amount', 'tax_amount'), ('tax', 'tax_rate'), ('buyer', 'buyer_name')]:
                    variables[key].set(result[field])
                if existing:
                    original_label = next((label for label, target in target_map.items()
                                           if target['project_id'] == existing['project_id']
                                           and target['contract_id'] == existing['contract_id']),
                                          f"{existing['project_name']} · {existing['contract_no']}")
                    variables['target'].set(original_label)
                    target_hint_var.set('附件归到上述原项目 / 合同，不修改原发票或收入、回款。')
                    status_hint = '（已作废，补附件不恢复）' if existing['status'] == 'void' else ''
                    warnings = list(existing['pdf_warnings'])
                    if existing['has_available_attachment']:
                        warnings.insert(0, '此票号已有可用附件，无需重复上传；可到原记录查看')
                        primary_button.configure(text='附件已存在', state='disabled')
                    elif existing['pdf_conflicts']:
                        warnings = existing['pdf_conflicts'] + warnings
                        primary_button.configure(text='请先核对原记录', state='disabled')
                    else:
                        warnings.insert(0, '此票号已登记但缺少可用附件，确认后直接补到原发票，不重复建票')
                        primary_button.configure(text='补充附件')
                    from pathlib import Path
                    pdf_hint.set('已识别：' + Path(path).name + status_hint + '\n' + '；'.join(warnings))
                    confirm_widget.configure(text='已核对同一张发票，仅补充附件，不修改原发票数据')
                    confirm_widget.pack(anchor=W, pady=5)
                    return
                contracts = {c['id']: c for c in contract_service.list_contracts()}
                matches = [label for label, target in target_map.items()
                           if result['buyer_name'] and contracts.get(target['contract_id'], {}).get('customer_name_snapshot') == result['buyer_name']]
                if not invoice:
                    variables['target'].set(matches[0] if len(matches) == 1 else prompt)
                refresh_target_hint()
                warnings = list(result['warnings'])
                if len(matches) > 1:
                    warnings.append('该客户对应多个项目/合同，请手动选择，不能仅按客户归集')
                elif not matches:
                    warnings.append('未找到唯一匹配项目，请手动选择')
                from pathlib import Path
                pdf_hint.set('已识别：' + Path(path).name + '\n' + ('；'.join(warnings) if warnings else '金额勾稽一致，请核对后保存。'))
                confirm_widget.pack(anchor=W, pady=5)

            Thread(target=work, daemon=True).start()
            dialog.after(100, finish)

        import_button.configure(command=choose_pdf)
        ttk.Label(body, text="备注").grid(
            row=len(specs)+1, column=0, sticky=NE, padx=(0, 12), pady=7
        )
        notes = ttk.Text(body, height=5, wrap="word")
        notes.grid(row=len(specs)+1, column=1, sticky=EW, pady=7)
        if invoice and invoice["notes"]:
            notes.insert("1.0", invoice["notes"])
        if restoring:
            ttk.Label(
                body,
                text="该发票已作废；“恢复并保存”会恢复并计入开票金额，仅补附件不会恢复。",
                style="CardText.TLabel",
            ).grid(row=len(specs)+2, column=1, sticky=W, pady=(2, 7))
        body.columnconfigure(1, weight=1)

        def set_form_enabled(enabled):
            for key, field in form_fields.items():
                if isinstance(field, DatePicker):
                    field.set_enabled(enabled)
                else:
                    state = 'readonly' if key == 'target' else 'normal'
                    field.configure(state=state if enabled else 'disabled')
            notes.configure(state='normal' if enabled else 'disabled')

        def finish_save(message):
            dialog.destroy()
            self.refresh()
            self.notebook.select(1)
            messagebox.showinfo('保存成功', message, parent=self.parent)

        def save():
            if pdf_state['busy']:
                messagebox.showwarning('请稍候', 'PDF仍在识别，请等待完成再保存', parent=dialog)
                return
            if pdf_state['recognition'] and not pdf_confirmed.get():
                messagebox.showwarning('请先核对', '请核对票面信息和项目归属，并勾选确认', parent=dialog)
                return
            if pdf_state['existing_invoice']:
                try:
                    from services.invoice_pdf_service import attach_pdf_to_existing
                    attach_pdf_to_existing(pdf_state['recognition'])
                except Exception as error:
                    messagebox.showwarning('无法补充附件', str(error), parent=dialog)
                    return
                finish_save('PDF已补到原发票；未重复建票，原项目、金额、收入和回款保持不变。')
                return
            if pdf_state['recognition'] and any(not variables[k].get().strip() for k in ('no','date','amount','net','tax_amount','tax','buyer')):
                messagebox.showwarning('请补充信息', '识别未确认的字段请核对原票后补全', parent=dialog)
                return
            target = target_map.get(variables["target"].get())
            if not target:
                messagebox.showwarning(
                    "无法保存", "请选择本次发票对应的项目和合同", parent=dialog
                )
                return
            if restoring and not messagebox.askyesno(
                "确认恢复",
                "保存后该发票将恢复为有效，并重新计入项目开票金额。确定继续吗？",
                parent=dialog,
            ):
                return
            try:
                data = {
                    "invoice_no": variables["no"].get(),
                    "project_id": target["project_id"],
                    "contract_id": target["contract_id"],
                    "invoice_date": variables["date"].get(),
                    "amount": variables["amount"].get(),
                    "tax_rate": variables["tax"].get(),
                    'net_amount': variables['net'].get(),
                    'tax_amount': variables['tax_amount'].get(),
                    'tax_rate_label': ((pdf_state['recognition'] or invoice or {}).get('tax_rate_label', '')
                                       if variables['tax'].get() == '多税率' else ''),
                    "buyer_name": variables["buyer"].get(),
                    "notes": notes.get("1.0", END).strip(),
                }
                if pdf_state['recognition']:
                    from services.invoice_pdf_service import save_with_pdf
                    save_with_pdf(data, pdf_state['recognition'], invoice['id'] if invoice else None)
                elif invoice:
                    finance_service.update_invoice(invoice["id"], data)
                else:
                    finance_service.create_invoice(data)
            except Exception as error:
                messagebox.showwarning("无法保存", str(error), parent=dialog)
                return
            finish_save(success_message)

        primary_button = add_form_actions(
            footer, cancel_command=dialog.destroy,
            primary_text=primary_text,
            primary_command=save,
        )
