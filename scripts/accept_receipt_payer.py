"""Exercise payer suggestions in native dialogs on an isolated database."""
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
assert os.environ.get('SUPPLY_CHAIN_DB_PATH'),'Use an isolated database'
import ttkbootstrap as ttk
from pages.finance_page import ReceivablePage
from services import finance_service, contract_service
from ui.theme import configure_design_system


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


root=ttk.Window(themename='flatly')
configure_design_system(root)
root.geometry('1200x800')
errors=[]
root.report_callback_exception=lambda *args:errors.append(str(args))
try:
    parent=ttk.Frame(root)
    parent.pack(fill='both',expand=True)
    page=ReceivablePage(parent)
    page.open_receipt_dialog()
    root.update()
    dialog=next(w for w in descendants(root) if isinstance(w,ttk.Toplevel))
    label=next(w for w in descendants(dialog) if isinstance(w,ttk.Label) and w.cget('text')=='付款方')
    grid=label.grid_info()
    payer=label.master.grid_slaves(row=grid['row'],column=grid['column']+1)[0]
    allocation=next(w for w in descendants(dialog) if isinstance(w,ttk.Combobox) and w['values'] and ' → ' in str(w['values'][0]))
    contracts={r['contract_no']:r for r in contract_service.list_contracts()}
    def expected():
        contract=contracts[allocation.get().split(' · ')[0]]
        return finance_service.default_receipt_payer(None,contract['id'])
    assert payer.get()==expected() and payer.get()
    allocation.set(allocation['values'][-1])
    root.update()
    assert payer.get()==expected()
    payer.delete(0,'end')
    payer.insert(0,'测试第三方代付')
    allocation.set(allocation['values'][0])
    root.update()
    assert payer.get()=='测试第三方代付'
    dialog.destroy()
    existing=finance_service.list_receipts()[0]
    page.open_receipt_dialog(existing['id'])
    root.update()
    dialog=next(w for w in descendants(root) if isinstance(w,ttk.Toplevel))
    label=next(w for w in descendants(dialog) if isinstance(w,ttk.Label) and w.cget('text')=='付款方')
    grid=label.grid_info()
    payer=label.master.grid_slaves(row=grid['row'],column=grid['column']+1)[0]
    assert payer.get()==existing['payer_name_snapshot']
    dialog.destroy()
    page.historical_page.receipt_dialog()
    root.update()
    dialog=next(w for w in descendants(root) if isinstance(w,ttk.Toplevel))
    assert any(isinstance(w,ttk.Entry) and w.get()==page.historical_page.selected()['customer_name'] for w in descendants(dialog))
    assert not errors,errors
    print('PASS default contract customer, source switch, manual override, existing snapshot, historical customer')
finally:
    root.destroy()
