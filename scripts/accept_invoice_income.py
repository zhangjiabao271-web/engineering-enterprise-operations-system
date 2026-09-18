"""Native dialog acceptance; requires an isolated database supplied by caller."""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
assert os.environ.get('SUPPLY_CHAIN_DB_PATH'), 'Use an isolated database'
assert Path(os.environ['SUPPLY_CHAIN_DB_PATH']).resolve() != Path('supplier_data.db').resolve()
import ttkbootstrap as ttk
from PIL import ImageGrab
from pages.contract_page import ContractManagementPage
from pages.finance_page import ReceivablePage
from ui.theme import configure_design_system


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


root = ttk.Window(themename='flatly')
configure_design_system(root)
root.geometry('1200x800')
errors = []
root.report_callback_exception = lambda *args: errors.append(str(args))
folder = Path('qa/invoice_income')
folder.mkdir(parents=True, exist_ok=True)
try:
    for kind, cls in [('contract', ContractManagementPage), ('invoice', ReceivablePage)]:
        parent = ttk.Frame(root)
        parent.pack(fill='both', expand=True)
        page = cls(parent)
        if kind == 'contract':
            page.open_contract_dialog(4)
        else:
            page.open_invoice_dialog()
        root.update()
        dialog = next(w for w in descendants(root) if isinstance(w, ttk.Toplevel))
        if kind == 'contract':
            assert any(isinstance(w, ttk.Combobox) and w.get() == '随开票自动确认收入'
                       for w in descendants(dialog))
        else:
            combo = next(w for w in descendants(dialog) if isinstance(w, ttk.Combobox)
                         and any('HT-20260804-384D68' in str(v) for v in w['values']))
            combo.set(next(v for v in combo['values'] if 'HT-20260804-384D68' in str(v)))
            combo.event_generate('<<ComboboxSelected>>')
            root.update()
            assert any(isinstance(w, ttk.Label) and '无需另填收入确认' in str(w.cget('text'))
                       for w in descendants(dialog))
        dialog.attributes('-topmost', True)
        dialog.lift()
        dialog.focus_force()
        root.update()
        time.sleep(0.3)
        root.update()
        ImageGrab.grab(bbox=(dialog.winfo_rootx(), dialog.winfo_rooty(),
                            dialog.winfo_rootx() + dialog.winfo_width(),
                            dialog.winfo_rooty() + dialog.winfo_height())).save(folder / f'{kind}.png')
        assert not errors, errors
        dialog.destroy()
        parent.destroy()
    print('PASS native contract and invoice dialogs; screenshots in', folder)
finally:
    root.destroy()
