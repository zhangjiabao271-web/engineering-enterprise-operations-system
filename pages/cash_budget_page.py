"""A planning-first view: no historical ledger onboarding required."""
from datetime import date
from tkinter import messagebox

import ttkbootstrap as ttk
from ttkbootstrap.constants import BOTH, E, LEFT, W, X

from pages.funds_page import FundForm, money, amount_text
from services import cash_budget_service as budget
from services.expense_categories import CATEGORIES
from ui.components import DataTable


class CashBudgetPage:
    def __init__(self, parent):
        self.parent = parent
        self.book = ttk.Notebook(parent)
        self.book.pack(fill=BOTH, expand=True)
        self.tabs = {}
        for key, title in [('overview', '钱够用多久'), ('rules', '固定与日常开支'),
                           ('events', '未来收付安排'), ('classification', '费用分类整理')]:
            tab = ttk.Frame(self.book, padding=8)
            self.book.add(tab, text=title)
            self.tabs[key] = tab
        tab = self.tabs['overview']
        self.bar(tab, [('更新实际可用资金', self.open_snapshot), ('刷新预测', self.refresh),
                       ('查看预测口径', lambda: messagebox.showinfo('预测口径与缺口', '\n'.join(self.data['warnings']), parent=self.parent))])
        self.balance_text = ttk.StringVar(master=parent)
        ttk.Label(tab, textvariable=self.balance_text, wraplength=850).pack(fill=X, pady=8)
        cards = ttk.Frame(tab)
        cards.pack(fill=X)
        self.values = {}
        for i, (key, title) in enumerate([('zero', '无新回款可撑到'), ('reserve', '未来30天预留'), ('available', '当前还能动用')]):
            self.values[key] = ttk.StringVar(master=parent, value='待设置')
            card = ttk.Frame(cards, padding=8)
            card.grid(row=0, column=i, sticky='ew', padx=4)
            ttk.Label(card, text=title).pack(anchor=W)
            ttk.Label(card, textvariable=self.values[key], font=('Microsoft YaHei UI', 14, 'bold')).pack(anchor=W, pady=5)
            cards.columnconfigure(i, weight=1, uniform='budget')
        self.warning = ttk.StringVar(master=parent)
        ttk.Label(tab, textvariable=self.warning, wraplength=880, justify=LEFT).pack(fill=X, pady=10)
        self.timeline = self.table(tab, [('date', '日期', 110, W), ('out', '预计支出', 125, E),
            ('in', '计划回款', 125, E), ('base', '无新回款余额', 145, E), ('expected', '按期回款余额', 145, E)], '先填实际可用资金，不需要建立账户或补历史流水。')
        tab = self.tabs['rules']
        self.bar(tab, [('新增预算', self.open_rule), ('修改选中', lambda: self.open_rule(edit=True)),
                       ('查看两月均值', self.open_averages)])
        ttk.Label(tab, text='同一用途只设一份预算。车贷按整笔月供预测，可选“一次性支出待核实”；不自动作为工程成本。', wraplength=880).pack(fill=X, pady=8)
        self.rules = self.table(tab, [('title', '开支', 145, W), ('category', '用途', 230, W),
            ('amount', '每月 / 每次预算', 140, E), ('cadence', '方式', 145, W), ('date', '起始 / 扣款日', 115, W), ('end', '结束日', 110, W), ('active', '状态', 65, W)], '添加每月固定支出或日常生活预算。')
        tab = self.tabs['events']
        self.bar(tab, [('新增大额收付', self.open_event), ('修改计划', lambda: self.open_event(edit=True)),
                       ('已完成', lambda: self.close_event('done')), ('取消计划', lambda: self.close_event('cancelled'))])
        self.events = self.table(tab, [('title', '计划事项', 240, W), ('direction', '方向', 65, W), ('amount', '尚未完成金额', 140, E), ('date', '日期', 115, W), ('warning', '说明', 260, W)], '没有手动安排。下面列出可直接选用的现有业务。')
        self.events.tree.configure(height=5)
        self.bar(tab, [('把选中业务安排到日期', self.event_from_source)])
        self.pending = self.table(tab, [('title', '现有待收待付 / 工资待核实', 400, W), ('direction', '方向', 70, W), ('amount', '余额 / 待核实金额', 150, E)], '没有可安排的来源。工资不会仅凭工天被当成未支付。')
        tab = self.tabs['classification']
        self.bar(tab, [('确认选中用途', self.classify_selected)])
        self.show_classified = ttk.BooleanVar(master=parent, value=False)
        ttk.Checkbutton(tab, text='也显示已整理的费用', variable=self.show_classified, command=self.refresh).pack(anchor=W)
        ttk.Label(tab, text='只改费用用途，不改金额、付款或项目分摊。家庭、还本、购置类不能直接归集项目；不明用途集中确认。', wraplength=880).pack(fill=X, pady=8)
        self.classifications = self.table(tab, [('date', '日期', 105, W), ('category', '现有分类', 230, W),
            ('party', '说明 / 往来', 240, W), ('amount', '金额', 110, E), ('suggested', '建议用途', 245, W)], '分类已整理。')
        self.refresh()

    def bar(self, parent, actions):
        row = ttk.Frame(parent)
        row.pack(fill=X, pady=5)
        for label, action in actions:
            ttk.Button(row, text=label, command=action, bootstyle='secondary-outline').pack(side=LEFT, padx=(0, 6))

    def table(self, parent, columns, empty):
        result = DataTable(parent, columns, empty_text=empty)
        result.tree.configure(selectmode='browse')
        return result

    def selected(self, table, rows):
        selection = table.tree.selection()
        if not selection:
            messagebox.showinfo('提示', '请先选择一条记录', parent=self.parent)
            return None
        return rows.get(str(selection[0]))

    def refresh(self):
        self.data = budget.forecast()
        snapshot = self.data['snapshot']
        if snapshot:
            self.balance_text.set(f"基于 {snapshot['balance_date']} 日终可用资金 {money(snapshot['amount_minor'])}，预测次日起180天。")
            self.values['zero'].set(self.data['zero_date'] or '180天内未耗尽')
            self.values['reserve'].set(money(self.data['reserve30_minor']))
            self.values['available'].set(money(self.data['available_minor']))
            if not any(r['active'] for r in budget.list_rules()):
                self.values['zero'].set('先设置基础开支')
        else:
            self.balance_text.set('只需确认现在能用的钱，预算不依赖历史流水齐全。请按当天收付结束后的资金填写。')
        extra = ''
        if snapshot:
            extra = f"安全线：{self.data['warning_date'] or '未触及'}；按期回款耗尽日：{self.data['expected_zero_date'] or '180天内未耗尽'}。\n"
        self.warning.set(extra + (f"未排期支出预留 {money(self.data.get('undated_minor', 0))}；另有 {len(self.data['warnings'])} 项口径/缺口提示，请查看。" if snapshot else '先填写实际可用资金，再设置日常和固定开支。'))
        self.timeline.refresh(self.data['daily'], lambda r: (r['date'], (r['date'], money(r['out_minor']), money(r['in_minor']), money(r['balance_minor']), money(r['expected_minor']))))
        self.rule_rows = {str(r['id']): r for r in budget.list_rules()}
        labels = {'daily': '月预算按日展开', 'monthly': '每月固定扣款', 'yearly': '每年定日缴费'}
        self.rules.refresh(self.rule_rows.values(), lambda r: (r['id'], (r['title'], r['category'], money(r['amount_minor']), labels[r['cadence']], r['next_date'], r['end_date'] or '未设', '启用' if r['active'] else '暂停')))
        self.event_rows = {str(r['id']): r for r in self.data['events']}
        self.events.refresh(self.event_rows.values(), lambda r: (r['id'], (r['title'], '待收' if r['direction'] == 'in' else '待付', money(r['remaining_minor']), r['due_date'] or '未安排', r['warning'])))
        self.source_rows = {str(i): r for i, r in enumerate(self.data['pending'])}
        self.pending.refresh(self.source_rows.items(), lambda pair: (pair[0], (pair[1]['title'], '待收' if pair[1]['direction'] == 'in' else '待付', money(pair[1]['amount_minor']))))
        self.class_rows = {str(r['id']): r for r in budget.classification_rows()
                           if self.show_classified.get() or r['category'] not in CATEGORIES or '待核实' in r['category']}
        self.classifications.refresh(self.class_rows.values(), lambda r: (r['id'], (r['cost_date'], r['category'], (r['counterparty_name_snapshot'] or '') + ' ' + (r['notes'] or ''), money(r['amount_minor']), r['suggested'])))

    def open_snapshot(self):
        old = self.data.get('snapshot') or {}
        form = FundForm(self.parent, '更新实际可用资金', self.refresh,
                        hint='填写公司、经营用个人账户和现金中真正能动用的总额，排除冻结或不能动用的钱。不会改期初或新增流水。')
        form.field('balance_date', '截至日期（当天收付结束后）', date.today().isoformat(), calendar=True)
        form.field('amount', '实际可用金额（元）')
        form.field('reserve', '额外备用金 / 安全线（元，可填0）', amount_text(old.get('reserve_minor', 0)))
        form.submit(budget.save_snapshot)

    def open_rule(self, edit=False, suggested=None):
        row = self.selected(self.rules, self.rule_rows) if edit else (suggested or {})
        if row is None:
            return
        form = FundForm(self.parent, '固定与日常开支预算', self.refresh,
                        hint='这只是预测，不产生费用或付款。日常开支填整月预算；固定支出填每次扣款。已有同用途实际费用会抵掉本期预算，避免重复。')
        form.field('title', '开支名称', row.get('title', ''))
        form.field('category', '用途', row.get('category', CATEGORIES[0]), choices=CATEGORIES)
        form.field('amount', '整月 / 每次预算金额（元）', amount_text(row['amount_minor']) if row.get('amount_minor') is not None else '')
        form.field('use_average', '金额来源', '两月均值自动更新' if row.get('use_average') else '手动预算', choices=['手动预算', '两月均值自动更新'])
        labels = {'月预算按日展开': 'daily', '每月固定扣款': 'monthly', '每年定日缴费': 'yearly'}
        form.field('cadence', '预测方式', next((k for k, v in labels.items() if v == row.get('cadence')), '月预算按日展开'), choices=labels)
        form.field('next_date', '起始日期 / 首次扣款日', row.get('next_date', date.today().isoformat()), calendar=True)
        form.field('end_date', '结束日（例如车贷最后一期，可空）', row.get('end_date') or '', calendar=True, optional=True)
        form.field('active', '状态', '启用' if row.get('active', 1) else '暂停', choices=['启用', '暂停'])
        form.submit(lambda v: budget.save_rule({**v, 'cadence': labels[v['cadence']], 'active': int(v['active'] == '启用'),
                    'use_average': int(v['use_average'] == '两月均值自动更新')}, row.get('id')))

    def open_averages(self):
        rows = budget.historical_suggestions()
        form = FundForm(self.parent, '前两个完整月的日常开支', self.refresh,
                        hint='只有确认两个自然月都已录完整，才提供平均建议；没有记录不能当零。建议不会自动变成预算。')
        months = rows[0]['months']
        for row in rows:
            text = '；'.join(f'{m}：{money(row["totals"][m])}' for m in months)
            ttk.Label(form.body, text=f"{row['category']}\n{text}\n{row['state']}", wraplength=530).pack(fill=X, pady=7)
            if row['amount_minor']:
                ttk.Button(form.body, text=f"参考均值 {money(row['amount_minor'])} 设置预算", command=lambda r=row: (form.window.destroy(), self.open_rule(suggested=r))).pack(anchor=W)
        form.field('month', '选择已核实录入完整的月份', months[0], choices=months)
        confirmed = ttk.BooleanVar(master=form.window, value=False)
        ttk.Checkbutton(form.body, text='我已核实该月日常开支完整；无记录的用途确实没有支出', variable=confirmed).pack(anchor=W)
        def save(v):
            if not confirmed.get():
                raise ValueError('请核实月份完整性后再确认')
            budget.confirm_month(v['month'])
        form.submit(save, '确认该月完整')

    def event_from_source(self):
        row = self.selected(self.pending, self.source_rows)
        if row:
            self.open_event(source=row)

    def open_event(self, edit=False, source=None):
        row = self.selected(self.events, self.event_rows) if edit else (source or {})
        if row is None:
            return
        form = FundForm(self.parent, '安排未来收付', self.refresh,
                        hint='只安排未来一次收付；不要再填写已包含在固定/日常预算里的同一笔开支。日期可空。来源余额减少后自动缩减，不重复记账。')
        form.field('title', '事项', row.get('title', ''))
        form.field('direction', '方向', '待收' if row.get('direction') == 'in' else '待付', choices=['待收', '待付'])
        form.field('amount', '预计金额（元）', amount_text(row['amount_minor']) if row.get('amount_minor') else '')
        form.field('due_date', '预计日期（可空）', row.get('due_date') or '', calendar=True, optional=True)
        form.submit(lambda v: budget.save_event({**v, 'direction': 'in' if v['direction'] == '待收' else 'out',
                    'source_kind': row.get('source_kind', ''), 'source_id': row.get('source_id'),
                    'source_period': row.get('source_period', '')}, row.get('id') if edit else None))

    def close_event(self, state):
        row = self.selected(self.events, self.event_rows)
        if row and messagebox.askyesno('确认', '关闭这条预测计划，不新增或删除实际收付款。完成后请更新实际可用资金。', parent=self.parent):
            budget.close_event(row['id'], state)
            self.refresh()

    def classify_selected(self):
        row = self.selected(self.classifications, self.class_rows)
        if not row:
            return
        form = FundForm(self.parent, '确认费用用途', self.refresh,
                        hint=f"原分类：{row['category']}；金额 {money(row['amount_minor'])}。修改保留历史，不改变资金流水。")
        form.field('category', '实际用途', row['suggested'] if row['certain'] and row['suggested'] in CATEGORIES else '', choices=CATEGORIES)
        form.submit(lambda v: budget.classify(row['id'], v['category']))
