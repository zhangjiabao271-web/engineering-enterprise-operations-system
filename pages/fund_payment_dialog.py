"""Shared actual-payment dialogs; cash budgeting has been retired."""

from datetime import date
from decimal import Decimal
from tkinter import messagebox
from uuid import uuid4

import ttkbootstrap as ttk
from ttkbootstrap.constants import LEFT, W, X

from services import funds_service as funds
from ui.components import DatePicker
from ui.dialogs import add_form_actions, build_form_dialog
from ui.error_handling import show_unexpected_error
from ui.theme import SPACING


def money(value):
    if value is None:
        return '未启用'
    value = int(value)
    return f"{'-' if value < 0 else ''}¥{Decimal(abs(value))/100:,.2f}"


def amount_text(value):
    return f'{Decimal(value)/100:.2f}'


def _note(parent, text):
    label = ttk.Label(parent, text=text, style='CardText.TLabel', justify=LEFT)
    label.pack(fill=X, pady=(0, 10))
    label.bind('<Configure>', lambda event: label.configure(wraplength=max(220, event.width)))
    return label


class FundForm:
    """Native scrollable forms with compact calendars and fixed save actions."""

    def __init__(self, parent, title, on_saved, *, hint=''):
        self.window = ttk.Toplevel(parent)
        self.window.title(title)
        self.body, self.footer = build_form_dialog(self.window,parent,680,680,min_width=520,min_height=380)
        self.on_saved = on_saved
        self.values = {}
        self.controls = {}
        if hint:
            _note(self.body,hint)
        self.error = ttk.StringVar(master=self.window)
        error_label = ttk.Label(self.footer,textvariable=self.error,style='FormError.TLabel',justify=LEFT)
        error_label.pack(fill=X,pady=(0,8))
        error_label.bind('<Configure>',lambda event:error_label.configure(wraplength=max(240,event.width)))

    def field(self, key, label, value='', *, choices=None, calendar=False, optional=False):
        row = ttk.Frame(self.body)
        row.pack(fill=X,pady=(0,12))
        ttk.Label(row,text=label).pack(anchor=W,pady=(0,5))
        var = ttk.StringVar(master=self.window,value=value)
        if calendar:
            widget = DatePicker(row,textvariable=var,allow_empty=optional)
            widget.pack(anchor=W)
        elif choices is not None:
            widget = ttk.Combobox(row,textvariable=var,values=list(choices),state='readonly',width=44)
            widget.pack(fill=X)
        else:
            widget = ttk.Entry(row,textvariable=var)
            widget.pack(fill=X)
        self.values[key],self.controls[key] = var,widget
        return var

    def submit(self, callback, text='保存'):
        def save():
            button.configure(state='disabled')
            try:
                callback({key: var.get() for key,var in self.values.items()})
            except ValueError as error:
                self.error.set(str(error))
                button.configure(state='normal')
                return
            except Exception:
                show_unexpected_error('保存失败', parent=self.window)
                button.configure(state='normal')
                return
            self.window.destroy()
            self.on_saved()
        button = add_form_actions(self.footer,cancel_command=self.window.destroy,
                                  primary_text=text,primary_command=save)


def open_source_payment(parent, kind, source_id, *, period='', on_saved=lambda: None):
    """Also used by existing business pages; never creates a second cost/receipt."""
    sources = funds.list_sources(kind,include_closed=True)
    source = next((row for row in sources if row['id']==source_id and row['period']==period),None)
    if not source:
        messagebox.showwarning('无法登记','业务记录已不存在或已作废。',parent=parent)
        return
    accounts = {f"{row['name']} · {money(row['balance_minor'])}":row['id']
                for row in funds.list_accounts() if row['status']=='active'}
    if not accounts:
        messagebox.showinfo('先建立账户','尚未建立真实资金账户，暂不能登记账户流水；业务记录不受影响。',parent=parent)
        return
    if source['state']=='启用前回款（计入期初）':
        messagebox.showinfo('启用前回款','这笔回款早于资金启用日，应包含在真实期初余额内，不再重复到账。',parent=parent)
        return
    if source['remaining_minor']<=0:
        messagebox.showinfo('无需登记','这笔业务已登记完毕，或金额存在异常；请先核对历史流水。',parent=parent)
        return
    title = '指定回款到账账户' if kind=='receipt' else '登记实际付款'
    hint = f"{source['label']}\n业务金额 {money(source['total_minor'])}；资金账已记 {money(source['paid_minor'])}。只记录真实资金，不新增收入或成本。"
    if source['legacy_status']:
        hint += f"\n原记录：{source['legacy_status']}。"
    if '员工垫付' in source['legacy_status']:
        hint += '应登记实际报销给员工的金额。'
    if kind=='receipt' and source['legacy_status']=='票据':
        hint += '\n票据收到不等于现金到账；仅在实际兑付后登记。'
    form = FundForm(parent,title,on_saved,hint=hint)
    form.field('account','到账账户' if kind=='receipt' else '付款账户',choices=accounts)
    default_date = source['source_date'] if kind=='receipt' and source['legacy_status']!='票据' else date.today().isoformat()
    form.field('date','实际到账日期' if kind=='receipt' else '实际付款日期',default_date,calendar=True)
    form.field('amount','本次实际金额（元）',amount_text(source['remaining_minor']) if source['verified'] else '')
    if not source['verified']:
        form.field('prior','资金启用前已付金额（无则填 0，不扣账户）')
        form.field('payee','实际收款人 / 报销员工',source['counterparty'])
        form.field('reason','历史付款核实依据（例如已查银行流水）')
    form.field('notes','备注 / 银行流水号（可选）')
    confirmed = ttk.BooleanVar(master=form.window,value=False)
    ttk.Checkbutton(form.body,text='我确认这笔钱已经实际到账 / 付出',variable=confirmed).pack(anchor=W,pady=(0,12))
    token = uuid4().hex

    def save(values):
        if not confirmed.get():
            raise ValueError('请先确认资金已经实际到账 / 付出；尚未发生的收付不能登记为实际流水')
        if values['account'] not in accounts:
            raise ValueError('请选择实际收付款账户')
        data = {'request_key':token,'category':kind,'source_id':source_id,'source_period':period,
                'transaction_date':values['date'],'amount':values['amount'],
                'notes':values['notes']}
        data['to_account_id' if kind=='receipt' else 'from_account_id'] = accounts[values['account']]
        if not source['verified']:
            data['opening'] = {'settled_amount':values['prior'],'payee':values['payee'],'reason':values['reason']}
        funds.record_transaction(data)
    form.submit(save,'确认登记')
    return form
