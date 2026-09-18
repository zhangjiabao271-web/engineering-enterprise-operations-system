"""Native-window acceptance using an online database copy and synthetic visible data."""
import argparse
from datetime import date,timedelta
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
from uuid import uuid4


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--screenshots',action='store_true')
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(project_root))
    with tempfile.TemporaryDirectory(prefix='funds_ui_') as folder:
        os.environ['SUPPLY_CHAIN_DB_PATH'] = str(Path(folder)/'test.db')
        os.environ['SUPPLY_CHAIN_ATTACHMENTS_PATH'] = str(Path(folder)/'attachments')
        from db.backup import backup_database
        from db.migration_runner import run_migrations
        backup_database(project_root/'supplier_data.db',os.environ['SUPPLY_CHAIN_DB_PATH'])
        run_migrations()
        from services import funds_service as funds,project_service,cost_service,procurement_service,collection_service
        from pages.funds_page import FundsPage,open_source_payment
        from ui.theme import configure_design_system
        import ttkbootstrap as ttk

        today = date.today().isoformat()
        start = (date.today()-timedelta(days=40)).isoformat()
        project = project_service.create_project({'name':'资金验收钢平台工程','business_mode':'cash'})
        purchase = procurement_service.add_purchase_order(
            {'purchase_type':'零星采购','project_id':project,'merchant_name_snapshot':'示例工具材料批发门店',
             'purchase_date':today,'payment_status':'未付款'},
            {'material_name_snapshot':'示例施工工具','settlement_mode':'total','settlement_total_cents':199988,'price_basis':'inclusive'})
        cost = cost_service.create_cost({'project_id':project,'cost_date':today,'category':'管理费',
                'amount':'2400','counterparty_name':'示例代理记账单位','allocation_method':'direct'})
        root = ttk.Window(themename='flatly')
        root.tk.call('tk','scaling',96/72)
        configure_design_system(root)
        root.geometry('1200x800+30+30')
        root.title('资金管理 · 隔离验收')
        errors = []
        root.report_callback_exception = lambda *args:errors.append(str(args))
        output = project_root/'qa'/'funds_management_20260910'
        if args.screenshots:
            output.mkdir(parents=True,exist_ok=True)

        def capture(widget,name):
            root.update()
            if args.screenshots:
                from PIL import ImageGrab
                widget.attributes('-topmost',True)
                widget.lift()
                widget.focus_force()
                root.update()
                time.sleep(.2)
                ImageGrab.grab(bbox=(widget.winfo_rootx(),widget.winfo_rooty(),
                    widget.winfo_rootx()+widget.winfo_width(),widget.winfo_rooty()+widget.winfo_height())).save(output/name)

        def check_buttons(widget):
            root.update()
            for button in descendants(widget):
                if isinstance(button,ttk.Button) and button.winfo_ismapped():
                    assert button.winfo_width()>=button.winfo_reqwidth(),('clipped button',button.cget('text'))
                    assert button.winfo_rootx()+button.winfo_width()<=widget.winfo_rootx()+widget.winfo_width(),button.cget('text')

        def submit(form,text):
            root.update()
            button = next(w for w in descendants(form.footer) if isinstance(w,ttk.Button) and w.cget('text')==text)
            button.invoke()
            root.update()
            assert not form.window.winfo_exists(),form.error.get()

        original_sources = funds.list_sources
        def visible_sources(kind,**kwargs):
            return [r for r in original_sources(kind,**kwargs) if r['id']=={'purchase':purchase,'cost':cost}.get(kind,-1)]
        try:
            sidebar = ttk.Frame(root,width=190)
            sidebar.pack(side='left',fill='y')
            sidebar.pack_propagate(False)
            ttk.Label(sidebar,text='工程经营',style='Brand.TLabel').pack(pady=20)
            ttk.Label(sidebar,text='资金管理 · 验收').pack()
            content = ttk.Frame(root,padding=16)
            content.pack(fill='both',expand=True)
            with patch.object(funds,'list_sources',side_effect=visible_sources),patch.object(collection_service,'list_project_cases',return_value=[]):
                page = FundsPage(content)
                root.update()
                assert page.kpis['balance_minor'].get()=='尚未启用'
                capture(root,'01-empty.png')
                form = page.open_account()
                form.values['name'].set('示例公司银行账户')
                form.values['kind'].set('公司银行')
                form.values['date'].set(start)
                form.values['opening'].set('50000.00')
                check_buttons(form.window)
                capture(form.window,'02-account-form.png')
                submit(form,'保存')
                bank = funds.list_accounts()[0]['id']
                cash = funds.save_account({'name':'示例备用现金','kind':'cash','opening_date':start,'opening_amount':'2000'})
                page.refresh()
                page.source_filter.set('采购待付')
                page.refresh_sources()
                page.show_pending()
                page.source_table.tree.selection_set(f'{purchase}:')
                root.update()
                capture(root,'03-purchase-pending.png')
                form = page.pay_source()
                form.values['account'].set(form.controls['account']['values'][0])
                form.values['amount'].set('1000')
                form.values['prior'].set('0')
                form.values['payee'].set('示例工具材料批发门店')
                form.values['reason'].set('核对订单，本次首次付款')
                confirm = next(w for w in descendants(form.body) if isinstance(w,ttk.Checkbutton))
                confirm.invoke()
                form.window.geometry('560x550+50+50')
                check_buttons(form.window)
                capture(form.window,'04-partial-payment.png')
                form.body.yview_moveto(1)
                root.update()
                capture(form.window,'04-partial-payment-confirm.png')
                submit(form,'确认登记')
                row = visible_sources('purchase')[0]
                assert row['remaining_minor']==99988
                assert funds.get_overview()['expense_minor']==100000
                form = page.open_transfer()
                form.values['account'].set('示例公司银行账户')
                form.values['to'].set('示例备用现金')
                form.values['amount'].set('500')
                form.values['notes'].set('银行取现备用')
                submit(form,'登记真实收支')
                assert funds.get_overview()['balance_minor']==5100000
                assert funds.get_overview()['expense_minor']==100000
                page.source_table.tree.selection_set(f'{purchase}:')
                form = page.plan_from_source()
                form.values['title'].set('工具余款，月底确认后支付')
                form.values['due_date'].set('')
                submit(form,'保存')
                assert funds.list_plans()[0]['state']=='待安排'
                form = page.open_reconcile()
                form.values['account'].set('示例公司银行账户')
                form.values['actual'].set('48500')
                form.values['notes'].set('按示例银行日终余额核对')
                submit(form,'保存核对，不调平')
                assert funds.list_reconciliations()[0]['difference_minor']==0
                for key in page.tabs:
                    page.notebook.select(page.tabs[key])
                    root.update()
                    check_buttons(root)
                    capture(root,f'05-{key}.png')
                page.notebook.select(page.tabs['pending'])
                page.plan_table.master.master.select(page.plan_table.master)
                page.plan_table.tree.selection_set(str(funds.list_plans()[0]['id']))
                root.update()
                capture(root,'05-plans.png')
                page.source_table.master.master.select(page.source_table.master)
                root.geometry('1000x700+30+30')
                for key in page.tabs:
                    page.notebook.select(page.tabs[key])
                    root.update()
                    check_buttons(root)
                    capture(root,f'06-narrow-{key}.png')
                assert not errors,errors
                print('PASS: native account create, partial business payment, atomic transfer, undated plan, reconciliation; 4 tabs at 1200x800 and 1000x700; no callback errors.')
            # Instantiate the real shell and cross-page actions on the same isolated DB.
            for child in root.winfo_children():
                child.destroy()
            from main import SupplierManagerApp
            import main as app_module
            # The test window already owns initialized ttkbootstrap elements.
            with patch.object(app_module,'configure_design_system'):
                app = SupplierManagerApp(root)
            app.navigate_to('funds')
            root.update()
            assert app.current_page=='funds'
            assert app.nav_buttons['funds'].winfo_ismapped()
            for key in ('workspace','profit','cost','finance','purchase','workday'):
                app.navigate_to(key)
                root.update()
                assert app.current_page==key
            app.navigate_to('funds')
            root.update()
            if args.screenshots:
                capture(root,'07-real-shell.png')
            assert not errors,errors
            print('PASS: real application navigation, project workspace/profit, cost, receipts, purchase and workday pages construct after migration.')
        finally:
            root.destroy()


if __name__=='__main__':
    main()
