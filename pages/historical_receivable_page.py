from datetime import date
from uuid import uuid4
from tkinter import messagebox
import sqlite3
import ttkbootstrap as ttk
from services import historical_receivable_service as history, master_data_service
from ui.components import DatePicker
from ui.dialogs import build_form_dialog, add_form_actions


def money(value):
    return '待确认' if value is None else f'¥{value / 100:,.2f}'


class HistoricalReceivablePage:
    def __init__(self, parent):
        self.parent = parent
        ttk.Label(parent, text='历史旧账回收', style='CardTitle.TLabel').pack(anchor='w', pady=6)
        ttk.Label(parent, text='本页独立管理历史欠款；回款按实际收到日期登记，不计入本年度新增业务收入。上方工程项目筛选不作用于本页。', wraplength=850).pack(anchor='w', pady=6)
        bar = ttk.Frame(parent)
        bar.pack(fill='x', pady=6)
        for label, command in [('新建历史项目', self.project_dialog), ('修改欠款总额', lambda: self.project_dialog(True)), ('登记历史回款', self.receipt_dialog), ('刷新', self.refresh)]:
            ttk.Button(bar, text=label, command=command).pack(side='left', padx=(0,8))
        self.projects = self.table(parent, [('customer','客户',240),('name','历史项目',300),('opening','历史欠款总额',140),('received','累计收回',140),('remaining','剩余欠款',140),('state','状态',130)], 6)
        self.projects.bind('<<TreeviewSelect>>', lambda _: self.refresh_receipts())
        ttk.Label(parent, text='所选历史项目的回款明细（到账账户需在确认后另行登记）').pack(anchor='w', pady=8)
        self.receipts = self.table(parent, [('date','收款日期',130),('amount','本次回款',150),('payer','付款人',240),('method','收款方式',130),('notes','备注',480)], 6)
        self.rows = {}
        self.refresh()

    @staticmethod
    def table(parent, columns, height):
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True)
        tree = ttk.Treeview(frame, columns=[col[0] for col in columns], show='headings', height=height, selectmode='browse')
        for key,label,width in columns:
            tree.heading(key,text=label)
            tree.column(key,width=width,minwidth=100)
        vertical = ttk.Scrollbar(frame,orient='vertical',command=tree.yview)
        horizontal = ttk.Scrollbar(frame,orient='horizontal',command=tree.xview)
        tree.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
        frame.rowconfigure(0,weight=1)
        frame.columnconfigure(0,weight=1)
        tree.grid(row=0,column=0,sticky='nsew')
        vertical.grid(row=0,column=1,sticky='ns')
        horizontal.grid(row=1,column=0,sticky='ew')
        return tree

    def selected(self):
        ids = self.projects.selection()
        return self.rows.get(int(ids[0])) if ids else None

    def refresh(self):
        selected = self.selected() if self.rows else None
        self.rows = {row['id']: row for row in history.list_projects()}
        self.projects.delete(*self.projects.get_children())
        for row in self.rows.values():
            self.projects.insert('', 'end', iid=str(row['id']), values=(row['customer_name'],row['name'],money(row['opening_minor']),money(row['received_minor']),money(row['remaining_minor']),row['state']))
        if self.rows:
            target = selected['id'] if selected and selected['id'] in self.rows else next(iter(self.rows))
            self.projects.selection_set(str(target))
        self.refresh_receipts()

    def refresh_receipts(self):
        self.receipts.delete(*self.receipts.get_children())
        row = self.selected()
        if row:
            for receipt in history.list_receipts(row['id']):
                self.receipts.insert('', 'end', iid=str(receipt['id']), values=(receipt['receipt_date'],money(receipt['amount_minor']),receipt['payer_name_snapshot'] or '未记录',receipt['payment_method'],receipt['notes']))

    def project_dialog(self, edit=False):
        current = self.selected() if edit else None
        if edit and not current:
            return
        dialog = ttk.Toplevel(self.parent)
        dialog.title('维护历史旧账项目')
        body,footer = build_form_dialog(dialog,self.parent,640,460)
        customers = {row['name']:row['id'] for row in master_data_service.list_customers()}
        selected = next((label for label,customer_id in customers.items() if current and current['customer_id']==customer_id),'')
        customer = ttk.StringVar(value=selected)
        name = ttk.StringVar(value=current['name'] if current else '')
        amount = ttk.StringVar(value=f"{current['opening_minor']/100:.2f}" if current and current['opening_minor'] is not None else '')
        notes = ttk.StringVar(value=current['notes'] if current else '')
        for label, widget in [('客户',ttk.Combobox(body,textvariable=customer,values=list(customers),state='disabled' if edit else 'readonly')),('历史项目名称',ttk.Entry(body,textvariable=name)),('历史欠款总额（元，未知请留空）',ttk.Entry(body,textvariable=amount)),('备注',ttk.Entry(body,textvariable=notes))]:
            ttk.Label(body,text=label).pack(anchor='w',pady=(8,2))
            widget.pack(fill='x')
        def save():
            try:
                history.save_project(customers.get(customer.get()),name.get(),amount.get(),notes.get(),current['id'] if current else None)
            except (ValueError,sqlite3.IntegrityError) as error:
                messagebox.showwarning('无法保存',str(error),parent=dialog)
                return
            dialog.destroy()
            self.refresh()
        add_form_actions(footer,cancel_command=dialog.destroy,primary_text='保存',primary_command=save)

    def receipt_dialog(self):
        row = self.selected()
        if not row:
            messagebox.showinfo('选择历史项目','请先选择或新建历史旧账项目。',parent=self.parent)
            return
        dialog = ttk.Toplevel(self.parent)
        dialog.title('登记历史旧账回款')
        body,footer = build_form_dialog(dialog,self.parent,650,460)
        ttk.Label(body,text=f"{row['name']}\n累计收回：{money(row['received_minor'])}；剩余：{money(row['remaining_minor'])}",wraplength=550).pack(anchor='w',pady=8)
        receipt_date = ttk.StringVar(value=date.today().isoformat())
        amount = ttk.StringVar()
        method = ttk.StringVar(value='待确认')
        payer = ttk.StringVar(value=row['customer_name'])
        notes = ttk.StringVar()
        request_key = str(uuid4())
        for label,widget in [('收款日期',DatePicker(body,textvariable=receipt_date)),('本次收到（元）',ttk.Entry(body,textvariable=amount)),('付款人（可修改）',ttk.Entry(body,textvariable=payer)),('收款方式',ttk.Combobox(body,textvariable=method,values=['待确认','银行转账','现金','微信','支付宝'],state='readonly')),('备注',ttk.Entry(body,textvariable=notes))]:
            ttk.Label(body,text=label).pack(anchor='w',pady=(8,2))
            widget.pack(anchor='w',fill='x' if label!='收款日期' else None)
        def save():
            try:
                history.record_receipt(row['id'],receipt_date.get(),amount.get(),request_key,method.get(),notes.get(),payer_name=payer.get())
            except ValueError as error:
                messagebox.showwarning('无法保存',str(error),parent=dialog)
                return
            dialog.destroy()
            self.refresh()
        add_form_actions(footer,cancel_command=dialog.destroy,primary_text='保存回款',primary_command=save)
