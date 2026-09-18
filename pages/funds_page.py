"""Account-based cash management, keeping plans and business facts separate."""

from datetime import date
from decimal import Decimal
import sqlite3
from tkinter import messagebox, simpledialog
from uuid import uuid4

import ttkbootstrap as ttk
from ttkbootstrap.constants import BOTH, BOTTOM, E, LEFT, RIGHT, W, X

from services import collection_service, funds_service as funds
from ui.components import DataTable, DatePicker, KpiCard, PageHeader
from ui.dialogs import add_form_actions, build_form_dialog, safe_init_loaders
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
            except (ValueError,sqlite3.Error) as error:
                self.error.set(str(error))
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
        messagebox.showinfo('先建立账户','请先到“资金管理 → 资金总览”填写真实账户与期初余额。',parent=parent)
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
    plans = {'不关联计划':None}
    for plan in funds.list_plans():
        direction = 'in' if kind=='receipt' else 'out'
        if plan['direction'] != direction:
            continue
        if plan['source_kind'] in ('', 'project') or (
                plan['source_kind'],plan['source_id'],plan['source_period'])==(kind,source_id,period):
            plans[f"#{plan['id']} {plan['title']} · 待 {money(plan['remaining_minor'])}"] = plan['id']
    form.field('plan','关联收付计划（可选）','不关联计划',choices=plans)
    form.field('notes','备注 / 银行流水号（可选）')
    confirmed = ttk.BooleanVar(master=form.window,value=False)
    ttk.Checkbutton(form.body,text='我确认这笔钱已经实际到账 / 付出',variable=confirmed).pack(anchor=W,pady=(0,12))
    token = uuid4().hex

    def save(values):
        if not confirmed.get():
            raise ValueError('请先确认资金已经实际到账 / 付出；未发生的收付请记计划')
        if values['account'] not in accounts:
            raise ValueError('请选择实际收付款账户')
        data = {'request_key':token,'category':kind,'source_id':source_id,'source_period':period,
                'transaction_date':values['date'],'amount':values['amount'],
                'plan_id':plans.get(values['plan']),'notes':values['notes']}
        data['to_account_id' if kind=='receipt' else 'from_account_id'] = accounts[values['account']]
        if not source['verified']:
            data['opening'] = {'settled_amount':values['prior'],'payee':values['payee'],'reason':values['reason']}
        funds.record_transaction(data)
    form.submit(save,'确认登记')
    return form


class FundsPage:
    SOURCES = {'回款待入账':'receipt','采购待付':'purchase','费用待付':'cost','工资待付':'wage','项目待收':'project'}

    def __init__(self,parent,navigate=None):
        self.parent,self.navigate = parent,navigate
        self.source_rows = {}
        self.plan_rows = {}
        self.accounts = {}
        PageHeader(parent,'资金预算','看钱够用多久、未来30天预留；历史账户流水可选，不必重复记账')
        self.status_var = ttk.StringVar(master=parent)
        status = ttk.Label(parent,textvariable=self.status_var,style='CardText.TLabel',justify=LEFT)
        status.bind('<Configure>',lambda event: status.configure(wraplength=max(300,event.width)))
        self.notebook = ttk.Notebook(parent)
        self.notebook.pack(fill=BOTH,expand=True)
        self.notebook.bind('<<NotebookTabChanged>>', lambda _event: (
            status.pack_forget() if self.notebook.index(self.notebook.select()) == 0
            else status.pack(fill=X, pady=(0,10), before=self.notebook)))
        self.tabs = {}
        for key,title in [('budget','资金预算'),('overview','账户账面（可选）'),('transactions','历史流水'),('pending','原资金登记'),('reconcile','账户核对')]:
            tab = ttk.Frame(self.notebook,padding=12)
            self.notebook.add(tab,text=title)
            self.tabs[key] = tab
        from pages.cash_budget_page import CashBudgetPage
        self.budget_page = CashBudgetPage(self.tabs['budget'])
        self._build_overview()
        self._build_transactions()
        self._build_pending()
        self._build_reconcile()
        safe_init_loaders('资金管理',[self.refresh])

    def _bar(self,parent,buttons):
        bar = ttk.Frame(parent)
        bar.pack(fill=X,pady=(0,10))
        for title,command in buttons:
            ttk.Button(bar,text=title,bootstyle='secondary-outline',command=command).pack(side=LEFT,padx=(0,8))
        return bar

    def _table(self,parent,specs,empty):
        # Shared scaling installs the horizontal scrollbar exactly once.
        table = DataTable(parent,specs,empty_text=empty,padding=4)
        table.tree.configure(selectmode='browse')
        return table

    def _build_overview(self):
        tab = self.tabs['overview']
        self._bar(tab,[('新增账户',self.open_account),('修改账户',lambda:self.open_account(edit=True)),
                       ('停用 / 启用',self.toggle_account),('刷新',self.refresh)])
        grid = ttk.Frame(tab)
        grid.pack(fill=X,pady=(0,SPACING['md']))
        self.kpis = {}
        self.kpi_hints = {}
        for index,(key,label,hint) in enumerate([
            ('balance_minor','账户账面余额','期初 + 已记到账 − 已记付款'),
            ('income_minor','本月实际到账','含投入和借款；不含内部转账'),
            ('expense_minor','本月实际付出','含取款和还本；不含内部转账'),
        ]):
            self.kpis[key] = ttk.StringVar(master=tab,value='尚未启用')
            self.kpi_hints[key] = ttk.StringVar(master=tab,value=hint)
            card = KpiCard(grid,label,self.kpis[key],self.kpi_hints[key],hint_wraplength=220)
            card.grid(row=0,column=index,sticky='ew',padx=(0,8))
            grid.columnconfigure(index,weight=1,uniform='fund-kpi')
        _note(tab,'账户余额取决于录入完整性，并非银行直连余额。负数请核对遗漏到账、期初或重复付款；不能当作实际透支。')
        self.account_table = self._table(tab,[('name','账户',200,W),('balance','账面余额',130,E),
            ('opening','期初余额',115,E),('date','启用日',105,W),('kind','类型',145,W),
            ('status','状态',75,W)],'尚未建立账户。点击“新增账户”，填入启用当天开始记账前的真实余额。')

    def _build_transactions(self):
        tab = self.tabs['transactions']
        self._bar(tab,[('登记业务收付款',self.show_pending),('其他收支',self.open_other),
                       ('内部转账',self.open_transfer),('关联计划',self.link_plan),
                       ('作废错误流水',self.void_selected),('刷新',self.refresh)])
        filters = ttk.Frame(tab)
        filters.pack(fill=X,pady=(0,10))
        ttk.Label(filters,text='账户').pack(side=LEFT)
        self.account_filter = ttk.StringVar(master=tab,value='全部账户')
        self.account_combo = ttk.Combobox(filters,textvariable=self.account_filter,state='readonly',width=23)
        self.account_combo.pack(side=LEFT,padx=(8,16))
        self.account_combo.bind('<<ComboboxSelected>>',lambda _e:self.refresh_transactions())
        self.include_void = ttk.BooleanVar(master=tab,value=False)
        ttk.Checkbutton(filters,text='显示作废记录',variable=self.include_void,command=self.refresh_transactions).pack(side=LEFT)
        dates = ttk.Frame(tab)
        dates.pack(fill=X,pady=(0,10))
        self.date_start = ttk.StringVar(master=tab,value=date.today().replace(day=1).isoformat())
        self.date_end = ttk.StringVar(master=tab,value=date.today().isoformat())
        for text,var in [('从',self.date_start),('到',self.date_end)]:
            ttk.Label(dates,text=text).pack(side=LEFT,padx=(0,8))
            picker = DatePicker(dates,textvariable=var,allow_empty=True)
            picker.pack(side=LEFT,padx=(0,16))
        ttk.Button(dates,text='筛选',bootstyle='secondary-outline',command=lambda:self._action(self.refresh_transactions)).pack(side=LEFT)
        _note(tab,'业务回款必须关联原回款；付款只减少资金、不再增加成本。选中流水可查看完整来源和作废原因。')
        self.tx_detail = ttk.StringVar(master=tab,value='请选择流水查看完整信息')
        detail = ttk.Label(tab,textvariable=self.tx_detail,style='CardText.TLabel',justify=LEFT)
        detail.pack(side=BOTTOM,fill=X,pady=(8,0))
        detail.bind('<Configure>',lambda event:detail.configure(wraplength=max(300,event.width)))
        self.tx_table = self._table(tab,[('date','实际日期',105,W),('category','类别',110,W),('amount','金额',125,E),
            ('from','付款账户',140,W),('to','到账账户',140,W),('party','往来方',160,W),
            ('status','状态',70,W)],'尚无资金流水。历史付款标记不会自动生成资金记录。')
        self.tx_table.tree.bind('<<TreeviewSelect>>',lambda _e:self._transaction_detail())

    def _build_pending(self):
        tab = self.tabs['pending']
        sub = ttk.Notebook(tab)
        sub.pack(fill=BOTH,expand=True)
        business,plans = ttk.Frame(sub,padding=8),ttk.Frame(sub,padding=8)
        sub.add(business,text='业务待处理')
        sub.add(plans,text='收付计划')
        filters = ttk.Frame(business)
        filters.pack(fill=X,pady=(0,10))
        self.source_filter = ttk.StringVar(master=tab,value='回款待入账')
        combo = ttk.Combobox(filters,textvariable=self.source_filter,values=list(self.SOURCES),state='readonly',width=18)
        combo.pack(side=LEFT)
        combo.bind('<<ComboboxSelected>>',lambda _e:self.refresh_sources())
        self.show_closed = ttk.BooleanVar(master=tab,value=False)
        ttk.Checkbutton(filters,text='含历史 / 已完成',variable=self.show_closed,command=self.refresh_sources).pack(side=LEFT,padx=12)
        ttk.Button(filters,text='刷新',bootstyle='secondary-outline',command=self.refresh).pack(side=RIGHT)
        search = ttk.Frame(business)
        search.pack(fill=X,pady=(0,10))
        ttk.Label(search,text='查单号 / 往来方 / 月份').pack(side=LEFT)
        self.source_keyword = ttk.StringVar(master=tab)
        entry = ttk.Entry(search,textvariable=self.source_keyword,width=28)
        entry.pack(side=LEFT,padx=8)
        entry.bind('<Return>',lambda _e:self.refresh_sources())
        ttk.Button(search,text='查找',command=self.refresh_sources,bootstyle='secondary-outline').pack(side=LEFT)
        _note(business,'“待核实”不代表仍欠款。先确认启用前已付多少；员工垫付核实的是公司已报销多少。项目待收取收入确认减已回款，不用合同金额代替。')
        actions = ttk.Frame(business)
        actions.pack(side=BOTTOM,fill=X,pady=(10,0))
        self.pay_button = ttk.Button(actions,text='登记到账账户',command=self.pay_source,bootstyle='primary-outline')
        self.pay_button.pack(side=LEFT,padx=(0,8))
        self.verify_button = ttk.Button(actions,text='核实历史付款',command=self.open_verify,bootstyle='secondary-outline')
        self.verify_button.pack(side=LEFT,padx=(0,8))
        self.plan_button = ttk.Button(actions,text='安排收付计划',command=self.plan_from_source,bootstyle='secondary-outline')
        self.plan_button.pack(side=LEFT)
        self.source_detail = ttk.StringVar(master=tab,value='请选择业务查看完整信息')
        detail = ttk.Label(business,textvariable=self.source_detail,style='CardText.TLabel',justify=LEFT)
        detail.pack(side=BOTTOM,fill=X,pady=(8,0))
        detail.bind('<Configure>',lambda event:detail.configure(wraplength=max(300,event.width)))
        self.source_table = self._table(business,[('label','业务 / 项目',220,W),('party','往来方',140,W),
            ('total','业务金额',105,E),('remaining','未记 / 未付',105,E),('state','处理状态',115,W)],'当前没有待处理业务')
        self.source_table.tree.bind('<<TreeviewSelect>>',lambda _e:self._source_detail())
        plan_bar = self._bar(plans,[('新增计划',self.open_plan),('修改计划',self.edit_plan),('取消计划',self.cancel_plan),('刷新',self.refresh)])
        self.show_closed_plans = ttk.BooleanVar(master=tab,value=False)
        ttk.Checkbutton(plan_bar,text='含已完成 / 已取消',variable=self.show_closed_plans,command=self.refresh).pack(side=LEFT,padx=8)
        _note(plans,'日期留空就是“待安排”，不会显示逾期。计划不会增加或减少资金；实际收付款登记时选择计划，进度自动更新。')
        self.plan_detail = ttk.StringVar(master=tab,value='请选择计划查看完整事项和核对提示')
        detail = ttk.Label(plans,textvariable=self.plan_detail,style='CardText.TLabel',justify=LEFT)
        detail.pack(side=BOTTOM,fill=X,pady=(8,0))
        detail.bind('<Configure>',lambda event:detail.configure(wraplength=max(300,event.width)))
        self.plan_table = self._table(plans,[('due','预计日期',110,W),('direction','方向',70,W),
            ('title','计划事项',240,W),('party','往来方',140,W),('amount','计划金额',115,E),
            ('remaining','待完成',115,E),('state','状态',90,W)],'尚无收付计划')
        self.plan_table.tree.bind('<<TreeviewSelect>>',lambda _e:self._plan_detail())

    def _build_reconcile(self):
        tab = self.tabs['reconcile']
        self._bar(tab,[('登记余额核对',self.open_reconcile),('刷新',self.refresh)])
        _note(tab,'按当天结束时的银行余额 / 现金盘点核对。差额 = 实际余额 − 当时账面余额；不会自动补一笔调平。如果补记或作废历史流水，旧核对会提示“流水变化，需复核”。')
        self.reconcile_table = self._table(tab,[('date','核对日',110,W),('account','账户',180,W),
            ('book','核对时账面',125,E),('actual','实际余额',125,E),('difference','当时差额',125,E),
            ('state','状态',190,W)],'尚无核对记录。建立账户后，可用银行余额或现金盘点进行核对。')

    def refresh(self):
        overview = funds.get_overview()
        self.accounts = {row['id']:row for row in overview['accounts']}
        for key,var in self.kpis.items():
            var.set(money(overview[key]) if self.accounts else '尚未启用')
        self.status_var.set(
            f"资金起记日：{overview['tracking_start']} · 仅包含已建立账户和已登记流水。" if self.accounts else
            '可以直接使用资金预算；账户流水为可选功能，不必补录历史账。')
        if overview['negative_accounts']:
            self.status_var.set(self.status_var.get()+f" {overview['negative_accounts']} 个账户账面为负，请核对。")
        self.account_table.refresh(self.accounts.values(),lambda row:(row['id'],(
            row['name'],money(row['balance_minor']),money(row['opening_minor']),row['opening_date'],
            funds.ACCOUNT_KINDS[row['kind']],'正常' if row['status']=='active' else '已停用')))
        self.account_map = {'全部账户':None,**{row['name']:row['id'] for row in self.accounts.values()}}
        self.account_combo.configure(values=list(self.account_map))
        if self.account_filter.get() not in self.account_map:
            self.account_filter.set('全部账户')
        self.refresh_transactions()
        self.refresh_sources()
        self.plan_rows = {row['id']:row for row in funds.list_plans(include_closed=self.show_closed_plans.get())}
        self.plan_table.refresh(self.plan_rows.values(),lambda row:(row['id'],(
            row['due_date'] or '待安排','待收' if row['direction']=='in' else '待付',row['title'],
            row['counterparty'],money(row['amount_minor']),money(row['remaining_minor']),row['state'])))
        self.plan_detail.set('请选择计划查看完整事项和核对提示')
        self.reconcile_table.refresh(funds.list_reconciliations(),lambda row:(row['id'],(
            row['balance_date'],row['account_name'],money(row['book_minor']),money(row['actual_minor']),
            money(row['difference_minor']),'流水变化，需复核' if row['needs_review'] else
            ('一致' if row['difference_minor']==0 else '有差额，请核对'))))

    def refresh_transactions(self):
        rows = funds.list_transactions(start=self.date_start.get(),end=self.date_end.get(),
                    account_id=self.account_map.get(self.account_filter.get()),include_void=self.include_void.get())
        self.tx_rows = {row['id']:row for row in rows}
        self.tx_table.refresh(rows,lambda row:(row['id'],(row['transaction_date'],funds.CATEGORY_LABELS[row['category']],money(row['amount_minor']),
            row['from_name'] or '外部到账',row['to_name'] or '付给外部',row['counterparty'],
            '有效' if row['status']=='active' else '已作废')))
        self.tx_detail.set('请选择流水查看完整信息')

    def refresh_sources(self):
        kind = self.SOURCES[self.source_filter.get()]
        if kind=='project':
            rows = [dict(row,id=row['project_id'],label=row['project_name'],counterparty=row['customer_name'],
                         total_minor=row['settlement_minor'],remaining_minor=row['receivable_minor'],
                         state='待收款',period='') for row in collection_service.list_project_cases(include_settled=self.show_closed.get())]
        else:
            rows = funds.list_sources(kind,include_closed=self.show_closed.get())
        keyword = self.source_keyword.get().strip().casefold()
        if keyword:
            rows = [row for row in rows if keyword in ' '.join(str(row.get(field) or '') for field in
                    ('label','counterparty','source_date','period')).casefold()]
        self.source_rows = {f"{row['id']}:{row['period']}":row for row in rows}
        self.source_table.refresh(self.source_rows.items(),lambda item:(item[0],(
            item[1]['label'],item[1]['counterparty'],money(item[1]['total_minor']),
            money(item[1]['remaining_minor']) if kind=='project' or item[1]['verified'] else '—',
            '期初内回款' if item[1]['state']=='启用前回款（计入期初）' else item[1]['state'])))
        self.source_detail.set('请选择业务查看完整信息')
        self.pay_button.configure(text='去登记业务回款' if kind=='project' else ('登记到账账户' if kind=='receipt' else '登记实际付款'))
        self.verify_button.configure(state='disabled' if kind in ('receipt','project') else 'normal')
        self.plan_button.configure(state='disabled' if kind=='receipt' else 'normal')

    def _selected(self,table,rows):
        row = rows.get(table.selected_id())
        if not row:
            messagebox.showinfo('请选择记录','请先选中一条记录。',parent=self.parent)
        return row

    def _transaction_detail(self):
        row = self.tx_rows.get(self.tx_table.selected_id())
        if row:
            self.tx_detail.set(f"流水 #{row['id']} · {money(row['amount_minor'])} · {row['from_name'] or '外部'} → {row['to_name'] or '外部'}\n"
                               f"来源：{row['source_label'] or '独立资金记录'} · 往来：{row['counterparty'] or '内部转账'}\n"
                               f"备注：{row['notes'] or '无'}"+(f" · 作废原因：{row['void_reason']}" if row['void_reason'] else ''))

    def _source_detail(self):
        row = self.source_rows.get(self.source_table.selected_id())
        if not row:
            return
        if self.SOURCES[self.source_filter.get()]=='project':
            text = f"{row['label']} · 客户：{row['counterparty']}\n客户承诺日：{row.get('promised_date') or '未安排'}；待收 {money(row['remaining_minor'])}。承诺日期不代表全部金额必定到账。"
        else:
            text = f"{row['label']} · {row['source_date']} · 往来：{row['counterparty'] or '未填写'}\n启用前已付 {money(row['opening_settled_minor'])} · 资金账已记 {money(row['paid_minor'])} · 原付款标记：{row['legacy_status'] or '无'}"
        self.source_detail.set(text)

    def _plan_detail(self):
        row = self.plan_rows.get(self.plan_table.selected_id())
        if row:
            self.plan_detail.set(f"{row['title']} · {row['counterparty']}\n"
                                 f"{row['source_warning'] or row['notes'] or '计划不影响资金余额，实际收付款请关联流水。'}")

    def show_pending(self):
        self.notebook.select(self.tabs['pending'])

    def open_account(self,edit=False):
        row = self._selected(self.account_table,self.accounts) if edit else None
        if edit and not row:
            return
        form = FundForm(self.parent,'修改资金账户' if edit else '建立资金账户',self.refresh,
                        hint='期初余额填启用日开始记账前的真实余额。历史已收已付若包含在期初里，不要重复登记。无需填写完整银行卡号。')
        kinds = {label:key for key,label in funds.ACCOUNT_KINDS.items()}
        form.field('name','账户简称',row['name'] if row else '')
        form.field('kind','账户类型',funds.ACCOUNT_KINDS[row['kind']] if row else '',choices=kinds)
        form.field('date','账户启用日期',row['opening_date'] if row else date.today().isoformat(),calendar=True)
        form.field('opening','启用日记账前余额（元）',amount_text(row['opening_minor']) if row else '')
        form.field('notes','说明（可选）',row['notes'] if row else '')
        if edit:
            form.field('reason','修改原因')
        def save(values):
            funds.save_account({'name':values['name'],'kind':kinds.get(values['kind']),
                'opening_date':values['date'],'opening_amount':values['opening'],'notes':values['notes'],
                'reason':values.get('reason') or '建立真实账户'},row['id'] if row else None)
        form.submit(save)
        return form

    def _action(self,callback):
        try:
            callback()
            self.refresh()
        except (ValueError,sqlite3.Error) as error:
            messagebox.showerror('操作未完成',str(error),parent=self.parent)

    def toggle_account(self):
        row = self._selected(self.account_table,self.accounts)
        if row and messagebox.askyesno('更改账户状态',f"确认{'停用' if row['status']=='active' else '启用'} {row['name']}？历史记录保留。",parent=self.parent):
            self._action(lambda:funds.set_account_archived(row['id'],row['status']=='active'))

    def pay_source(self):
        row = self._selected(self.source_table,self.source_rows)
        if not row:
            return
        kind = self.SOURCES[self.source_filter.get()]
        if kind=='project':
            if self.navigate:
                self.navigate('finance')
            else:
                messagebox.showinfo('登记回款','请到“开票与回款”登记真实业务回款，再为它指定到账账户。',parent=self.parent)
            return
        return open_source_payment(self.parent,kind,row['id'],period=row['period'],on_saved=self.refresh)

    def open_verify(self):
        row = self._selected(self.source_table,self.source_rows)
        if not row:
            return
        kind = self.SOURCES[self.source_filter.get()]
        form = FundForm(self.parent,'核实历史付款',self.refresh,
                        hint=f"{row['label']}\n只确认资金启用前已支付 / 已报销部分，不改变账户余额。启用后的付款请登记资金流水。")
        form.field('settled_amount','启用前已付金额（元，无则填 0）',amount_text(row['opening_settled_minor']) if row['verified'] else '')
        form.field('payee','实际收款人（员工垫付请填报销员工）',row['counterparty'])
        form.field('reason','核实依据 / 更正原因')
        form.submit(lambda values:funds.verify_source(kind,row['id'],row['period'],values))
        return form

    def open_other(self,transfer=False):
        accounts = {row['name']:row['id'] for row in self.accounts.values() if row['status']=='active'}
        if not accounts:
            messagebox.showinfo('先建立账户','请先建立真实账户和期初余额。',parent=self.parent)
            return
        form = FundForm(self.parent,'内部转账' if transfer else '登记其他收支',self.refresh,
                        hint='内部转账不计入公司总收支。' if transfer else
                        '工程回款、采购、费用和工资请从“业务待处理”关联原记录。这里用于投入、借款、取款、还本等，不会自动生成收入或成本。')
        categories = {label:key for key,(label,_direction) in funds.OTHER_CATEGORIES.items()}
        if not transfer:
            form.field('category','资金性质',choices=categories)
        form.field('account','转出账户' if transfer else '资金账户',choices=accounts)
        if transfer:
            form.field('to','转入账户',choices=accounts)
        else:
            form.field('counterparty','往来单位 / 人员')
        form.field('date','实际日期',date.today().isoformat(),calendar=True)
        form.field('amount','实际金额（元）')
        plan_map = {'不关联计划':None}
        if not transfer:
            for plan in funds.list_plans():
                if not plan['source_kind']:
                    plan_map[f"#{plan['id']} {'待收' if plan['direction']=='in' else '待付'} {plan['title']}"] = plan['id']
            form.field('plan','关联计划（可选）','不关联计划',choices=plan_map)
        form.field('notes','说明 / 银行流水号')
        token = uuid4().hex
        def save(values):
            if values['account'] not in accounts or (transfer and values['to'] not in accounts):
                raise ValueError('请选择资金账户')
            category = 'transfer' if transfer else categories.get(values['category'])
            data = {'request_key':token,'category':category,'transaction_date':values['date'],
                    'amount':values['amount'],'notes':values['notes'],
                    'counterparty':values.get('counterparty'),'plan_id':plan_map.get(values.get('plan'))}
            if transfer:
                data.update(from_account_id=accounts[values['account']],to_account_id=accounts[values['to']])
            elif category in funds.OTHER_CATEGORIES:
                data['to_account_id' if funds.OTHER_CATEGORIES[category][1]=='in' else 'from_account_id'] = accounts[values['account']]
            funds.record_transaction(data)
        form.submit(save,'登记真实收支')
        return form

    def open_transfer(self):
        return self.open_other(transfer=True)

    def void_selected(self):
        row = self._selected(self.tx_table,self.tx_rows)
        if not row:
            return
        reason = simpledialog.askstring('作废错误流水',f"流水 #{row['id']}，{money(row['amount_minor'])}。\n只用于录错，会撤销其账户影响；真实退款请另记收支。\n请输入原因：",parent=self.parent)
        if reason:
            self._action(lambda:funds.void_transaction(row['id'],reason))

    def link_plan(self):
        row = self._selected(self.tx_table,self.tx_rows)
        if not row:
            return
        choices = {'不关联计划':None}
        direction = 'in' if row['to_account_id'] else 'out'
        for plan in funds.list_plans(include_closed=True):
            if plan['status']=='planned' and plan['direction']==direction:
                choices[f"#{plan['id']} {plan['title']}"] = plan['id']
        current = next((label for label,value in choices.items() if value==row['plan_id']),'不关联计划')
        form = FundForm(self.parent,'关联 / 更正收付计划',self.refresh,
                        hint=f"流水 #{row['id']} · {money(row['amount_minor'])}。只改变计划对应关系，不再记一笔钱，也不改变账户余额。")
        form.field('plan','关联计划',current,choices=choices)
        form.field('reason','关联调整说明')
        form.submit(lambda values:funds.link_transaction_plan(row['id'],choices[values['plan']],values['reason']))
        return form

    def plan_from_source(self):
        row = self._selected(self.source_table,self.source_rows)
        if row:
            kind = self.SOURCES[self.source_filter.get()]
            if kind!='project' and not row['verified']:
                messagebox.showinfo('先核实','请先核实历史付款，再安排剩余付款。',parent=self.parent)
                return
            return self.open_plan(source=(kind,row))

    def edit_plan(self):
        row = self._selected(self.plan_table,self.plan_rows)
        if row:
            return self.open_plan(existing=row)

    def open_plan(self,source=None,existing=None):
        row = existing or {}
        kind,source_row = source or (row.get('source_kind',''),{})
        source_id = source_row.get('id') or row.get('source_id')
        period = source_row.get('period') or row.get('source_period','')
        form = FundForm(self.parent,'修改收付计划' if existing else '安排收付计划',self.refresh,
                        hint='只安排时间和金额，不改变余额。业务计划请从原业务行进入；其他计划也可独立记录。')
        default_direction = 'in' if kind=='project' else 'out'
        direction = row.get('direction',default_direction)
        form.field('direction','计划方向','待收' if direction=='in' else '待付',choices=['待收','待付'])
        form.field('title','计划事项',source_row.get('label') or row.get('title',''))
        form.field('counterparty','往来单位 / 人员',source_row.get('counterparty') or row.get('counterparty',''))
        default_amount = source_row.get('remaining_minor',row.get('amount_minor'))
        form.field('amount','计划金额（元）',amount_text(default_amount) if default_amount else '')
        form.field('due_date','预计日期（可留空，表示待安排）',row.get('due_date') or '',calendar=True,optional=True)
        form.field('notes','依据 / 备注',row.get('notes',''))
        def save(values):
            funds.save_plan({**values,'direction':'in' if values['direction']=='待收' else 'out',
                             'source_kind':kind,'source_id':source_id,'source_period':period},row.get('id'))
        form.submit(save)
        return form

    def cancel_plan(self):
        row = self._selected(self.plan_table,self.plan_rows)
        if row:
            reason = simpledialog.askstring('取消计划','取消计划不撤销真实收付款。请输入取消原因：',parent=self.parent)
            if reason:
                self._action(lambda:funds.cancel_plan(row['id'],reason))

    def open_reconcile(self):
        accounts = {row['name']:row['id'] for row in self.accounts.values()}
        if not accounts:
            messagebox.showinfo('先建立账户','请先建立真实账户和期初余额。',parent=self.parent)
            return
        form = FundForm(self.parent,'登记账户余额核对',self.refresh,
                        hint='请填核对日期当天结束时的真实余额。差额只作提示，不自动生成任何调平流水。')
        form.field('account','核对账户',choices=accounts)
        form.field('date','核对日期',date.today().isoformat(),calendar=True)
        form.field('actual','实际银行 / 现金余额（元）')
        form.field('notes','核对依据与差额说明')
        def save(values):
            if values['account'] not in accounts:
                raise ValueError('请选择核对账户')
            funds.reconcile_account(accounts[values['account']],values['date'],values['actual'],values['notes'])
        form.submit(save,'保存核对，不调平')
        return form
