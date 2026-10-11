"""Cash-flow evidence and draft review inside the existing desktop workbench."""

import json
import logging
import os
import queue
import threading
from decimal import Decimal, InvalidOperation
from tkinter import filedialog, messagebox, simpledialog

import ttkbootstrap as ttk
from ttkbootstrap.constants import BOTH, END, LEFT, RIGHT, X
from ttkbootstrap.widgets.scrolled import ScrolledText

from services import document_cashflow_service as service
from services.document_cashflow_report import money
from services.document_cashflow_extract import KINDS, read_document
from ui.components import DataTable
from ui.dialogs import add_form_actions, build_form_dialog
from ui.theme import FONT_BODY, style_dialog


class DocumentCashflowDialog:
    def __init__(self, parent, contract_id=None, project_id=None):
        self.contract_id = contract_id
        self.project_id = project_id
        self.analysis_id = None
        self._source_reading = False
        self.dialog = ttk.Toplevel(parent)
        self.dialog.title('资料现金流分析')
        preference = getattr(parent.winfo_toplevel(), '_motion_enabled', None)
        if preference is not None:
            self.dialog._motion_enabled = preference
        style_dialog(self.dialog, parent, 1160, 800, resizable=True, min_width=840, min_height=600)
        self.scope = {'contract_id': contract_id, 'project_id': project_id}
        header = ttk.Frame(self.dialog, padding=16)
        header.pack(fill=X)
        ttk.Label(header, text=service.scope_label(**self.scope), style='CardTitle.TLabel').pack(anchor='w')
        ttk.Label(header, text='签约前后均可使用 · 本地规则提取 · 草稿经核对确认才进入收付计划',
                  wraplength=1050).pack(anchor='w', pady=6)
        self.tabs = ttk.Notebook(self.dialog)
        self.tabs.pack(fill=BOTH, expand=True, padx=16)
        documents_tab = ttk.Frame(self.tabs)
        draft_tab = ttk.Frame(self.tabs)
        report_tab = ttk.Frame(self.tabs)
        self.tabs.add(documents_tab, text='1 关联资料')
        self.tabs.add(draft_tab, text='2 节点与来源')
        self.tabs.add(report_tab, text='3 时间表与协商依据')
        actions = ttk.Frame(documents_tab, padding=(0, 10))
        actions.pack(fill=X)
        for label, command in (('关联本地文件', self.import_file), ('关联已有附件', self.link_attachment),
                               ('人工摘录/补充', self.manual_document), ('查看原文/历史', self.show_document),
                               ('移出分析', self.retire_document)):
            ttk.Button(actions, text=label, bootstyle='secondary-outline', command=command).pack(side=LEFT, padx=3)
        ttk.Label(documents_tab, text='合同正文、材料清单、预算、采购付款安排、施工计划均可关联；各范围独立分析。',
                  wraplength=1040).pack(anchor='w', pady=6)
        self.documents = DataTable(documents_tab, specs=(('name', '资料名称', 220, 'w'),
            ('kind', '类型', 140, 'w'), ('version', '版本', 60, 'center'),
            ('sha', '内容指纹', 180, 'w'), ('reason', '换版/移出原因', 260, 'w')),
            empty_text='请关联资料开始分析', stretch=('name', 'reason'), horizontal=True)
        options = ttk.Frame(draft_tab, padding=(0, 10))
        options.pack(fill=X)
        self.opening = ttk.StringVar(value='0')
        self.delay = ttk.StringVar(value='30')
        self.increase = ttk.StringVar(value='10')
        for label, variable in (('可用启动资金（元）', self.opening), ('情景：回款延后天数', self.delay),
                                ('支出增加（%）', self.increase)):
            ttk.Label(options, text=label).pack(side=LEFT, padx=(5, 3))
            ttk.Entry(options, textvariable=variable, width=8).pack(side=LEFT)
        ttk.Button(options, text='生成分析草稿', bootstyle='primary', command=self.analyze).pack(side=LEFT, padx=8)
        self.history_var = ttk.StringVar()
        self.history = ttk.Combobox(draft_tab, textvariable=self.history_var, state='readonly', width=60)
        self.history.pack(anchor='w', pady=4)
        self.history.bind('<<ComboboxSelected>>', lambda _event: self.load_selected_analysis())
        self.state = ttk.StringVar(value='尚未生成草稿')
        ttk.Label(draft_tab, textvariable=self.state, wraplength=1040).pack(anchor='w', pady=5)
        self.rows = DataTable(draft_tab, specs=(('title', '收付节点', 180, 'w'),
            ('direction', '方向', 55, 'center'), ('amount', '人民币金额', 110, 'e'),
            ('date', '日期', 105, 'center'), ('condition', '前置条件', 190, 'w'),
            ('issues', '待补/冲突', 250, 'w')),
            height=5, empty_text='生成草稿后逐项核对；双击查看全部来源', stretch=('title', 'condition', 'issues'), horizontal=True)
        self.rows.tree.bind('<Double-1>', lambda _event: self.show_row_sources())
        self.rows.tree.bind('<<TreeviewSelect>>', lambda _event: self.render_row_details())
        ttk.Label(draft_tab, text='节点原文与核对项 · 点选上表查看，双击可打开完整来源', style='CardTitle.TLabel').pack(anchor='w', pady=(8, 2))
        self.source_preview = ScrolledText(draft_tab, wrap='word', autohide=False, height=5, padding=8)
        self.source_preview.pack(fill=X)
        self.source_preview.text.configure(font=FONT_BODY, padx=12, pady=8, state='disabled')
        from ui.theme import COLORS
        self.rows.tree.tag_configure('conflict', foreground=COLORS['danger'])
        report_actions = ttk.Frame(report_tab, padding=(0, 10))
        report_actions.pack(fill=X)
        self.summary = ttk.StringVar(value='生成草稿后查看垫资缺口与时间表')
        ttk.Label(report_actions, textvariable=self.summary, style='CardTitle.TLabel', wraplength=700).pack(side=LEFT)
        ttk.Button(report_actions, text='导出本地报告', bootstyle='secondary-outline', command=self.export_report).pack(side=RIGHT, padx=3)
        ttk.Button(report_actions, text='已确认计划', bootstyle='secondary-outline', command=self.show_plans).pack(side=RIGHT, padx=3)
        self.schedule_mode = ttk.StringVar(value='按资料日期估算')
        selector = ttk.Combobox(report_tab, textvariable=self.schedule_mode,
            values=('按资料日期估算', '用户设定情景估算'), state='readonly', width=28)
        selector.pack(anchor='w', pady=6)
        selector.bind('<<ComboboxSelected>>', lambda _event: self.run(self.render_analysis))
        self.schedule = DataTable(report_tab, specs=(('date', '日期', 110, 'w'),
            ('in', '计划收款', 130, 'e'), ('out', '计划付款', 130, 'e'),
            ('balance', '预计资金余额', 160, 'e'), ('source', '来源节点编号', 460, 'w')),
            height=5, pack_expand=False, horizontal=True, stretch=('source',))
        ttk.Label(report_tab, text='以下估算以条件按期满足为前提；未知、冲突和未识别条款未计入。来源与协商依据可向下查看。',
            wraplength=1040).pack(anchor='w', pady=8)
        self.report = ScrolledText(report_tab, wrap='word', autohide=False, padding=12, height=8)
        self.report.pack(fill=BOTH, expand=True)
        self.report.text.configure(font=FONT_BODY, padx=12, pady=8)
        footer = ttk.Frame(self.dialog, padding=16)
        footer.pack(fill=X, side='bottom', before=self.tabs)
        for label, command in (('关闭', self.dialog.destroy), ('刷新', self.refresh), ('取消草稿', self.cancel)):
            ttk.Button(footer, text=label, bootstyle='secondary-outline', command=command).pack(side=LEFT, padx=3)
        ttk.Button(footer, text='核对并转入收付计划', bootstyle='primary', command=self.confirm).pack(side=RIGHT)
        self.refresh()

    def run(self, action):
        try:
            return action()
        except ValueError as error:
            messagebox.showwarning('资料分析', str(error), parent=self.dialog)
        except Exception:
            logging.exception('资料现金流操作失败')
            messagebox.showerror('状态待核对', '操作未完成，状态待核对。请保留输入并查看应用日志；勿重复确认。',
                                 parent=self.dialog)

    def refresh(self):
        def load():
            self.document_map = {str(row['id']): row for row in service.list_documents(**self.scope)}
            self.documents.refresh(list(self.document_map.values()), lambda row: (str(row['id']), (
                row['name'], row['kind'], f'v{row["version"]}', row['sha256'][:20], row['change_reason'])))
            analyses = service.list_analyses(**self.scope)
            self.history_map = {f'#{row["id"]} · {row["created_at"]} · ' + {'draft': '草稿', 'confirmed': '已确认', 'cancelled': '已取消'}[row['status']]: row['id'] for row in analyses}
            self.history.configure(values=list(self.history_map))
            if self.analysis_id is None and analyses:
                self.analysis_id = analyses[0]['id']
            if self.analysis_id:
                self.history_var.set(next((label for label, value in self.history_map.items()
                                           if value == self.analysis_id), ''))
                self.render_analysis()
        self.run(load)

    def load_selected_analysis(self):
        self.analysis_id = self.history_map.get(self.history_var.get())
        self.run(self.render_analysis)

    def document_editor(self, data=None, document=None):
        editor = ttk.Toplevel(self.dialog)
        editor.title('关联资料 / 保留换版历史')
        body, footer = build_form_dialog(editor, self.dialog, 800, 650)
        variables = {name: ttk.StringVar(value=value) for name, value in {
            'name': (data or document or {}).get('name', ''),
            'kind': (document or {}).get('kind', KINDS[0]), 'reason': '',
            'locator': '人工摘录：请改为原条款/页码'}.items()}
        for key, label in (('name', '资料名称（同名同类型自动换版）'), ('kind', '资料类型'),
                           ('reason', '换版原因 / 人工补充依据'), ('locator', '人工摘录来源位置（条款/页码）')):
            ttk.Label(body, text=label).pack(anchor='w', pady=(7, 2))
            if key == 'kind':
                ttk.Combobox(body, textvariable=variables[key], values=KINDS, state='readonly').pack(fill=X)
            else:
                ttk.Entry(body, textvariable=variables[key]).pack(fill=X)
        ttk.Label(body, text='规则支持明确字段，例如：收款 进度款；金额：10000元；日期：2026-12-01；条件：验收完成；对方：甲方\n'
                  '这是格式说明，不是分析数据。相对日期、百分比、公式须人工核对补充。', wraplength=720).pack(anchor='w', pady=8)
        preview = ScrolledText(body, height=10, wrap='word', autohide=False)
        preview.pack(fill=BOTH, expand=True)
        preview.text.configure(font=FONT_BODY, padx=12, pady=8)
        if data:
            warnings = data.get('recognition', {}).get('warnings', [])
            if warnings:
                ttk.Label(body, text='\n'.join(warnings), wraplength=720,
                          style='CardText.TLabel').pack(anchor='w', pady=6)
            preview.insert('1.0', '\n'.join(f'{f["locator"]}：{f["text"]}' for f in data['fragments']))
            preview.text.configure(state='disabled')
            ttk.Label(body, text='文件原文与定位保持原样；无需改写文件即可关联。', wraplength=720).pack(anchor='w')
        elif document:
            preview.insert('1.0', '\n'.join(f['text'] for f in json.loads(document['fragments_json'])))
        def save():
            def commit():
                entry = dict(data or {})
                if not data:
                    locator = variables['locator'].get().strip()
                    if not locator or locator == '人工摘录：请改为原条款/页码':
                        raise ValueError('请填写原文条款/页码；人工补充请注明依据')
                    entry['fragments'] = [{'locator': f'{locator} / 摘录第{i}行', 'text': line}
                                          for i, line in enumerate(preview.get('1.0', END).splitlines(), 1) if line.strip()]
                entry.update(name=variables['name'].get(), kind=variables['kind'].get(),
                             change_reason=variables['reason'].get())
                result = service.save_document(entry, **self.scope)
                editor.destroy()
                self.refresh()
                if result['duplicate']:
                    messagebox.showinfo('已关联', '内容相同，复用原版本，未重复导入。', parent=self.dialog)
            self.run(commit)
        add_form_actions(footer, cancel_command=editor.destroy, primary_text='保存资料版本', primary_command=save)
        return editor, preview, variables

    def import_file(self):
        path = filedialog.askopenfilename(parent=self.dialog, title='选择本地合同/项目资料',
            filetypes=[('本地资料', '*.txt *.md *.csv *.pdf *.docx *.xlsx'), ('全部文件', '*.*')])
        if path:
            self.run(lambda: self.read_source(path))

    def read_source(self, path, attachment=None):
        if self._source_reading:
            raise ValueError('上一份资料仍在后台读取，请稍后再选择')
        self._source_reading = True
        results = queue.SimpleQueue()
        cancelled = False
        progress = ttk.Toplevel(self.dialog)
        progress.title('本机读取资料')
        style_dialog(progress, self.dialog, 620, 340, resizable=True)
        ttk.Label(progress, text='正在本机读取资料，扫描PDF可能需要一至两分钟。\n'
                  '不上传附件、不修改账目；取消后不会打开识别结果。',
                  wraplength=540, padding=20).pack(fill=X)
        def cancel():
            nonlocal cancelled
            cancelled = True
            progress.destroy()
            if self.dialog.winfo_exists():
                self.dialog.grab_set()
        ttk.Button(progress, text='取消本次预览', command=cancel,
                   bootstyle='secondary-outline').pack(pady=20)
        progress.protocol('WM_DELETE_WINDOW', cancel)
        progress.bind('<Escape>', lambda _event: cancel())
        def worker():
            try:
                data = read_document(path)
                if attachment:
                    data.update(name=attachment['original_name'], attachment_id=attachment['id'])
                results.put((data, None))
            except Exception as error:
                results.put((None, error))
        def poll():
            try:
                data, error = results.get_nowait()
            except queue.Empty:
                self.dialog.after(100, poll)
                return
            self._source_reading = False
            if cancelled or not self.dialog.winfo_exists():
                return
            progress.destroy()
            self.dialog.grab_set()
            def finish():
                if error:
                    raise error
                self.document_editor(data=data)
            self.run(finish)
        threading.Thread(target=worker, daemon=True).start()
        self.dialog.after(100, poll)

    def manual_document(self):
        selected = self.documents.tree.selection()
        document = self.document_map.get(selected[0]) if selected else None
        self.document_editor(document=document)

    def link_attachment(self):
        def open_choices():
            attachments = service.available_attachments(**self.scope)
            if not attachments:
                raise ValueError('当前范围没有合同/采购/施工附件，可关联本地文件或人工摘录')
            picker = ttk.Toplevel(self.dialog)
            picker.title('关联已有业务附件')
            body, footer = build_form_dialog(picker, self.dialog, 680, 300)
            choices = {f'#{row["id"]} · {row["original_name"]}': row for row in attachments}
            choice = ttk.StringVar()
            ttk.Combobox(body, textvariable=choice, values=list(choices), state='readonly', width=65).pack(fill=X)
            def choose():
                def load():
                    if choice.get() not in choices:
                        raise ValueError('请选择附件')
                    row = choices[choice.get()]
                    picker.destroy()
                    self.read_source(row['absolute_path'], attachment=row)
                self.run(load)
            add_form_actions(footer, cancel_command=picker.destroy, primary_text='读取并核对', primary_command=choose)
        self.run(open_choices)

    def show_text(self, title, text):
        viewer = ttk.Toplevel(self.dialog)
        viewer.title(title)
        style_dialog(viewer, self.dialog, 850, 640, resizable=True)
        widget = ScrolledText(viewer, wrap='word', autohide=False, padding=16)
        widget.pack(fill=BOTH, expand=True)
        widget.insert('1.0', text)
        widget.text.configure(state='disabled', font=FONT_BODY, padx=12, pady=8)
        return viewer

    def show_document(self):
        selected = self.documents.tree.selection()
        history = service.list_documents(**self.scope, include_history=True)
        if selected:
            current = self.document_map[selected[0]]
            history = [doc for doc in history if doc['name'] == current['name'] and doc['kind'] == current['kind']]
        sections = []
        for document in history:
            sections.append(f'{document["name"]} v{document["version"]} · {document["created_at"]}\n'
                            f'SHA-256 {document["sha256"]}\n来源文件：{document["source_path"] or "人工摘录/补充快照"}\n'
                            f'换版/移出原因：{document["change_reason"]}\n' +
                            '\n'.join(f'{f["locator"]}：{f["text"]}' for f in json.loads(document['fragments_json'])))
        return self.show_text('资料原文与版本', '\n\n'.join(sections) or '当前范围尚未关联资料。')

    def retire_document(self):
        selected = self.documents.tree.selection()
        if selected:
            reason = simpledialog.askstring('移出分析', '填写原因。旧资料和分析历史仍保留，旧草稿将失效。', parent=self.dialog)
            if reason:
                self.run(lambda: service.retire_document(int(selected[0]), reason))
                self.refresh()

    def analyze(self):
        def create():
            try:
                amount = Decimal(self.opening.get()) * 100
                if not amount.is_finite() or amount != amount.to_integral_value():
                    raise ValueError
                opening_minor = int(amount)
                delay = int(self.delay.get())
                increase = int(self.increase.get())
            except (ValueError, InvalidOperation, OverflowError):
                raise ValueError('启动资金最多两位小数；天数及比例请填写整数') from None
            self.analysis_id = service.create_analysis(**self.scope, opening_minor=opening_minor,
                income_delay_days=delay, expense_increase_percent=increase)
            self.refresh()
            self.tabs.select(1)
        self.run(create)

    def render_analysis(self):
        from services.document_cashflow_report import report_text
        analysis = service.get_analysis(self.analysis_id)
        self.row_map = {row['key']: row for row in analysis['payload']['rows']}
        labels = {'draft': '草稿', 'confirmed': '已确认', 'cancelled': '已取消'}
        self.state.set(f'分析 #{analysis["id"]} · {labels[analysis["status"]]} · '
                       f'{analysis["stale_reason"] or "请逐项核对原文、条件和未覆盖条款"}')
        self.rows.refresh(list(self.row_map.values()), lambda row: (row['key'], (row['title'],
            {'in': '收款', 'out': '付款'}.get(row['direction'], '待核对'), money(row['amount_minor']),
            row['due_date'] or '待补充', row['condition'], '；'.join(row['issues']) or '待人工确认')))
        for key, row in self.row_map.items():
            self.rows.tree.item(key, tags=('conflict',) if row['conflict'] else ())
        if self.row_map:
            self.rows.tree.selection_set(next(iter(self.row_map)))
        self.render_row_details()
        payload = analysis['payload']
        self.summary.set(f'按资料日期垫资 {money(payload["base"]["gap_minor"])}  ·  情景垫资 {money(payload["scenario"]["gap_minor"])}  ·  待补/冲突 {len(payload["base"]["excluded_keys"])} 项')
        schedule_key = 'base' if self.schedule_mode.get() == '按资料日期估算' else 'scenario'
        self.schedule.refresh(payload[schedule_key]['schedule'], lambda event: (event['date'], (
            event['date'], money(event['in_minor']), money(event['out_minor']), money(event['balance_minor']), ', '.join(event['row_keys']))))
        self.report.text.configure(state='normal')
        self.report.delete('1.0', END)
        self.report.insert('1.0', report_text(analysis))
        self.report.text.configure(state='disabled')

    def render_row_details(self):
        self.source_preview.text.configure(state='normal')
        self.source_preview.delete('1.0', END)
        self.source_preview.text.configure(state='disabled')
        selected = self.rows.tree.selection()
        if not selected or not hasattr(self, 'row_map'):
            return
        row = self.row_map[selected[0]]
        text = '核对项：' + ('；'.join(row['issues']) or '待人工核对来源及条件') + '\n\n'
        text += '\n\n'.join(f'{source["name"]} v{source["version"]} / {source["locator"]}\n'
                            f'{source["excerpt"]}\nSHA-256 {source["sha256"]}' for source in row['sources'])
        self.source_preview.text.configure(state='normal')
        self.source_preview.delete('1.0', END)
        self.source_preview.insert('1.0', text)
        self.source_preview.text.configure(state='disabled')

    def show_row_sources(self):
        selected = self.rows.tree.selection()
        if selected:
            row = self.row_map[selected[0]]
            self.show_text('节点来源 · ' + row['title'], '\n\n'.join(
                f'{source["name"]} v{source["version"]} / {source["locator"]}\n'
                f'SHA-256 {source["sha256"]}\n{source["excerpt"]}' for source in row['sources']))

    def cancel(self):
        if self.analysis_id and messagebox.askyesno('取消草稿', '取消此草稿？不会生成任何计划或账目。', parent=self.dialog):
            self.run(lambda: service.cancel_analysis(self.analysis_id))
            self.refresh()

    def confirm(self):
        if not self.analysis_id:
            return
        def open_review():
            analysis = service.get_analysis(self.analysis_id)
            if analysis['status'] != 'draft' or analysis['stale_reason']:
                raise ValueError(analysis['stale_reason'] or '只能确认尚未取消/确认的草稿')
            editor = ttk.Toplevel(self.dialog)
            editor.title('逐项核对并转入正式收付计划')
            body, footer = build_form_dialog(editor, self.dialog, 950, 660)
            ttk.Label(body, text='仅选择已核对的节点（Ctrl/Shift多选）。待补充或冲突节点会被服务层拒绝。\n'
                '计划金额使用原始节点；情景调整不进入计划。此操作不记录实际收付款、收入或成本。', wraplength=880).pack(anchor='w')
            table = DataTable(body, specs=(('title', '节点', 180, 'w'), ('amount', '金额', 110, 'e'),
                ('date', '日期', 100, 'w'), ('condition', '条件/问题', 390, 'w')), empty_text='无节点可确认', horizontal=True, pack_expand=False)
            table.tree.configure(height=7, selectmode='extended')
            table.refresh(analysis['payload']['rows'], lambda row: (row['key'], (row['title'], money(row['amount_minor']),
                row['due_date'] or '待补充', '；'.join(row['issues']) or row['condition'])))
            acknowledged = ttk.BooleanVar(value=False)
            ttk.Checkbutton(body, text='我已核对来源、日期、金额、前置条件和已有计划，确认所选节点可作为计划依据',
                variable=acknowledged).pack(anchor='w', pady=10)
            ttk.Label(body, text='确认依据 / 经办说明').pack(anchor='w')
            reason = ttk.StringVar()
            ttk.Entry(body, textvariable=reason).pack(fill=X)
            def commit():
                def write():
                    ids = service.confirm_analysis(analysis['id'], list(table.tree.selection()),
                        acknowledged=acknowledged.get(), reason=reason.get())
                    editor.destroy()
                    self.refresh()
                    messagebox.showinfo('计划已生成', '正式计划编号：' + '、'.join(map(str, ids)) +
                                        '\n实际收付款仍需在原业务入口登记。', parent=self.dialog)
                self.run(write)
            add_form_actions(footer, cancel_command=editor.destroy, primary_text='确认所选节点并生成计划', primary_command=commit)
            return editor, table, acknowledged, reason
        return self.run(open_review)

    def show_plans(self):
        if self.analysis_id:
            from services.document_cashflow_report import confirmed_plans_text
            self.run(lambda: self.show_text('已确认收付计划', confirmed_plans_text(self.analysis_id)))

    def export_report(self):
        if not self.analysis_id:
            return
        path = filedialog.asksaveasfilename(parent=self.dialog, title='保存本地可追溯报告',
            defaultextension='.html', initialfile=f'资料现金流分析_{self.analysis_id}.html', filetypes=[('HTML报告', '*.html')])
        if path:
            def export():
                from services.document_cashflow_report import export_html
                export_html(self.analysis_id, path)
                os.startfile(path)
            self.run(export)


def open_document_cashflow(parent, contract_id=None, project_id=None):
    try:
        return DocumentCashflowDialog(parent, contract_id, project_id)
    except ValueError as error:
        messagebox.showwarning('资料分析', str(error), parent=parent)
    except Exception:
        logging.exception('资料现金流入口加载失败')
        messagebox.showerror('加载未完成', '资料分析无法加载，请保留输入并核对日志。新功能需正常重启应用完成迁移。', parent=parent)
