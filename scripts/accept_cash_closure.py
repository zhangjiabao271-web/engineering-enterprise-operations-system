import os
import sys
import time
from pathlib import Path
from uuid import uuid4
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
assert os.environ.get('SUPPLY_CHAIN_DB_PATH')
assert Path(os.environ['SUPPLY_CHAIN_DB_PATH']).resolve() != Path('supplier_data.db').resolve()
import ttkbootstrap as ttk
from PIL import ImageGrab
from services import project_service, contract_service, finance_service
from pages.finance_page import ReceivablePage
from ui.theme import configure_design_system


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


project = project_service.create_project({'name': '抹零验收-' + uuid4().hex[:6],
    'business_mode': 'cash', 'invoice_policy': 'not_required', 'cash_agreed_amount': '98683',
    'status': '进行中'})
contract_service.create_settlement({'project_id': project, 'amount': '98683',
                                    'settlement_date': '2026-09-17'})
finance_service.create_receipt({'project_id': project, 'amount': '98600',
                                'receipt_date': '2026-09-17'})
root = ttk.Window(themename='flatly')
configure_design_system(root)
root.geometry('1200x800')
errors = []
root.report_callback_exception = lambda *args: errors.append(str(args))
try:
    parent = ttk.Frame(root)
    parent.pack(fill='both', expand=True)
    page = ReceivablePage(parent)
    root.update()
    page.collection_trees['pending'].tree.selection_set(str(project))
    page.open_cash_closure('pending')
    root.update()
    dialog = next(w for w in descendants(root) if isinstance(w, ttk.Toplevel))
    assert any(isinstance(w, ttk.Label) and '83.00' in str(w.cget('text')) for w in descendants(dialog))
    dialog.attributes('-topmost', True)
    dialog.lift()
    dialog.focus_force()
    root.update()
    time.sleep(0.3)
    folder = Path('qa/cash_closure')
    folder.mkdir(parents=True, exist_ok=True)
    ImageGrab.grab(bbox=(dialog.winfo_rootx(), dialog.winfo_rooty(),
                        dialog.winfo_rootx()+dialog.winfo_width(),
                        dialog.winfo_rooty()+dialog.winfo_height())).save(folder/'confirm.png')
    check = next(w for w in descendants(dialog) if isinstance(w, ttk.Checkbutton))
    check.invoke()
    save = next(w for w in descendants(dialog) if isinstance(w, ttk.Button) and w.cget('text') == '确认结清')
    save.invoke()
    root.update()
    assert str(project) in page.collection_trees['settled'].tree.get_children()
    assert not errors, errors
    print('PASS native confirmation, checkbox, save and settled queue')
finally:
    root.destroy()
