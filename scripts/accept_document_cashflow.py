"""Native UI acceptance with synthetic records; never reads the operating DB."""

import argparse
import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def button(widget, label):
    import ttkbootstrap as ttk
    matches = [child for child in descendants(widget)
               if isinstance(child, ttk.Button) and child.cget('text') == label]
    assert len(matches) == 1, (label, len(matches))
    return matches[0]


def settle(root, seconds=.24):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        root.update()
        time.sleep(.01)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True, help='New isolated directory for synthetic DB and screenshots')
    args = parser.parse_args()
    output = args.output.resolve()
    assert not (output / 'synthetic.db').exists(), 'Choose a fresh QA output directory'
    output.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    os.environ['SUPPLY_CHAIN_DB_PATH'] = str(output / 'synthetic.db')
    os.environ['SUPPLY_CHAIN_ATTACHMENTS_PATH'] = str(output / 'attachments')
    os.environ['SUPPLY_CHAIN_BACKUP_DIR'] = str(output / 'backups')
    os.environ['ENGINEERING_REDUCED_MOTION'] = '1'
    os.environ.setdefault('TCL_LIBRARY', str(repo / '.venv' / 'tcl' / 'tcl8.6'))
    os.environ.setdefault('TK_LIBRARY', str(repo / '.venv' / 'tcl' / 'tk8.6'))

    import ttkbootstrap as ttk
    from PIL import ImageGrab
    from db.schema import init_db
    from services import contract_service, project_service, document_cashflow_service as service
    from pages.contract_page import ContractManagementPage
    from pages.project_page import ProjectManagementPage
    from services.document_cashflow_report import export_html
    from main import SupplierManagerApp

    init_db()
    project_id = project_service.create_project({'name': '合成验收 · 厂房钢结构项目',
        'project_code': 'QA-SYNTH-001', 'status': '筹备中'})
    contract_id = contract_service.create_contract({'name': '合成验收 · 钢结构施工合同',
        'contract_no': 'QA-SYNTH-HT-001', 'contract_type': 'project', 'pricing_mode': 'fixed',
        'amount': '500000', 'status': 'draft', 'sign_date': '2026-10-01'})
    contract_service.create_allocation({'contract_id': contract_id, 'project_id': project_id, 'amount': '500000'})
    scope = {'contract_id': contract_id, 'project_id': project_id}
    materials = output / '合成采购付款安排.txt'
    materials.write_text('付款 钢材定金；金额：180000元；日期：2026-11-05；条件：下单确认；对方：合成钢材供应商\n'
                         '付款 安装人工；金额：70000元；日期：2026-12-05；条件：进场完成；对方：合成安装班组', encoding='utf-8')
    from services.document_cashflow_extract import read_document
    service.save_document({**read_document(materials), 'kind': '采购付款安排'}, **scope)
    service.save_document({'name': '合成合同摘录', 'kind': '合同正文', 'fragments': [
        {'locator': '第4.1条 / 第2页', 'text': '收款 预付款；金额：100000元；日期：2026-11-01；条件：合同签订且开票；对方：合成业主'},
        {'locator': '第4.2条 / 第2页', 'text': '收款 进度款；金额：220000元；日期：2026-12-15；条件：阶段验收及审批；对方：合成业主'},
        {'locator': '第4.3条 / 第3页', 'text': '收款 质保金；金额：合同金额5%；日期：验收后两年；条件：质保期满无异议'}]}, **scope)
    root = ttk.Window(themename='cosmo')
    root.title('合成资料验收 · 工程企业经营系统')
    errors = []
    root.report_callback_exception = lambda kind, value, tb: errors.append(str(value))
    with patch('tkinter.messagebox.showwarning', side_effect=lambda *a, **k: errors.append(str(a))), \
         patch('tkinter.messagebox.showerror', side_effect=lambda *a, **k: errors.append(str(a))), \
         patch('tkinter.messagebox.showinfo'):
        app = SupplierManagerApp(root)
        root.title('合成资料验收 · 工程企业经营系统')
        captures = []

        def capture(window, name):
            window.lift()
            settle(root)
            path = output / name
            ImageGrab.grab(window=window.winfo_id()).save(path)
            captures.append({'file': name, 'size': [window.winfo_width(), window.winfo_height()]})

        capture(root, '01_current_home.png')
        app.set_active_nav('contract')
        app.clear_content()
        app.current_page = 'contract'
        page = ContractManagementPage(app.content_frame, initial_project_id=project_id)
        page.contract_tree.tree.selection_set(str(contract_id))
        capture(root, '02_contract_entry.png')
        analysis_ui = page.open_document_cashflow()
        assert analysis_ui is not None
        capture(analysis_ui.dialog, '03_documents.png')
        button(analysis_ui.dialog, '生成分析草稿').invoke()
        settle(root)
        assert analysis_ui.analysis_id
        capture(analysis_ui.dialog, '04_nodes.png')
        analysis_ui.tabs.select(2)
        capture(analysis_ui.dialog, '05_schedule.png')
        analysis_ui.schedule_mode.set('用户设定情景估算')
        analysis_ui.render_analysis()
        assert analysis_ui.schedule.tree.get_children()
        analysis_ui.schedule_mode.set('按资料日期估算')
        analysis_ui.render_analysis()
        export_html(analysis_ui.analysis_id, output / 'cashflow_report.html')

        def count(table):
            with closing(sqlite3.connect(output / 'synthetic.db')) as conn:
                return conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]

        # Import preview cancellation writes nothing; duplicate import reuses a version.
        before = count('cashflow_documents')
        editor, _, _ = analysis_ui.document_editor(data=read_document(materials))
        button(editor, '取消').invoke()
        assert count('cashflow_documents') == before
        editor, _, variables = analysis_ui.document_editor(data=read_document(materials))
        variables['kind'].set('采购付款安排')
        button(editor, '保存资料版本').invoke()
        assert count('cashflow_documents') == before

        # Selecting a conflicting candidate is rejected, with no formal plan written.
        service.save_document({'name': '合成冲突报价', 'kind': '采购付款安排', 'fragments': [
            {'locator': '第1条 / 第1页', 'text': '付款 钢材定金；金额：190000元；日期：2026-11-05；条件：下单确认；对方：合成钢材供应商'}]}, **scope)
        assert service.get_analysis(analysis_ui.analysis_id)['stale_reason']
        button(analysis_ui.dialog, '生成分析草稿').invoke()
        analysis_ui.tabs.select(1)
        capture(analysis_ui.dialog, '06_conflict.png')
        review, table, acknowledged, reason = analysis_ui.confirm()
        payload = service.get_analysis(analysis_ui.analysis_id)['payload']
        conflict = next(row['key'] for row in payload['rows'] if row['conflict'])
        table.tree.selection_set(conflict)
        acknowledged.set(True)
        reason.set('合成冲突验证')
        previous_errors = len(errors)
        button(review, '确认所选节点并生成计划').invoke()
        assert len(errors) == previous_errors + 1
        assert count('fund_plans') == 0
        errors.pop()
        button(review, '取消').invoke()

        # Cancel the draft through its real UI callback.
        with patch('tkinter.messagebox.askyesno', return_value=True):
            button(analysis_ui.dialog, '取消草稿').invoke()
        assert service.get_analysis(analysis_ui.analysis_id)['status'] == 'cancelled'
        assert count('fund_plans') == 0

        conflict_doc = next(doc for doc in service.list_documents(**scope) if doc['name'] == '合成冲突报价')
        service.retire_document(conflict_doc['id'], '合成核对：旧报价不用')
        button(analysis_ui.dialog, '生成分析草稿').invoke()
        review, table, acknowledged, reason = analysis_ui.confirm()
        assert not table.tree.selection()
        known_keys = [row['key'] for row in service.get_analysis(analysis_ui.analysis_id)['payload']['rows'] if not row['issues']]
        table.tree.selection_set(known_keys)
        acknowledged.set(True)
        reason.set('合成端到端验收：核对来源与前置条件')
        capture(review, '07_confirmation.png')
        button(review, '确认所选节点并生成计划').invoke()
        assert count('fund_plans') == len(known_keys)
        assert count('fund_transactions') == 0
        assert service.get_analysis(analysis_ui.analysis_id)['status'] == 'confirmed'
        capture(analysis_ui.dialog, '08_confirmed.png')

        # Verify compact layout and accessible final actions.
        analysis_ui.dialog.geometry('840x640')
        settle(root)
        action = button(analysis_ui.dialog, '核对并转入收付计划')
        assert action.winfo_ismapped() and action.winfo_x() >= 0
        assert action.winfo_x() + action.winfo_width() <= action.master.winfo_width() + 2
        capture(analysis_ui.dialog, '09_compact.png')
        for document in service.list_documents(**scope):
            service.retire_document(document['id'], '合成验收：验证全部移出后仍可读历史')
        analysis_ui.refresh()
        assert not analysis_ui.document_map
        history_view = analysis_ui.show_document()
        assert history_view is not None
        history_view.destroy()
        analysis_ui.dialog.destroy()

        app.clear_content()
        app.set_active_nav('project')
        project_page = ProjectManagementPage(app.content_frame)
        settle(root)
        project_page._current_project_tree().tree.selection_set(str(project_id))
        capture(root, '10_project_entry.png')
        project_ui = project_page.open_document_cashflow()
        assert project_ui.scope == {'contract_id': None, 'project_id': project_id}
        project_ui.dialog.destroy()
        assert errors == [], errors

        with closing(sqlite3.connect(output / 'synthetic.db')) as conn:
            integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
            foreign_keys = conn.execute('PRAGMA foreign_key_check').fetchall()
        result = {'synthetic_only': True, 'migration': 630, 'captures': captures,
                  'plans': count('fund_plans'), 'actual_transactions': count('fund_transactions'),
                  'integrity': integrity, 'foreign_key_violations': foreign_keys,
                  'ui_checks': ['import cancellation', 'duplicate import', 'conflict rejection',
                                'draft cancellation', 'explicit selection and confirmation',
                                'stale analysis', 'compact layout', 'contract and project entry',
                                'reduced motion'], 'errors': errors}
        (output / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False))
        root.destroy()


if __name__ == '__main__':
    main()
