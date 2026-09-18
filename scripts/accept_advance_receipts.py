import os
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
assert os.environ.get('SUPPLY_CHAIN_DB_PATH')
assert Path(os.environ['SUPPLY_CHAIN_DB_PATH']).resolve() != Path('supplier_data.db').resolve()
import ttkbootstrap as ttk
from PIL import ImageGrab
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
try:
    parent = ttk.Frame(root)
    parent.pack(fill='both', expand=True)
    page = ReceivablePage(parent)
    page.open_receipt_dialog()
    root.update()
    dialog = next(w for w in descendants(root) if isinstance(w, ttk.Toplevel))
    source = next(w for w in descendants(dialog) if isinstance(w, ttk.Combobox)
                  and '零星现金工程' in w['values'])
    source.set('零星现金工程')
    source.event_generate('<<ComboboxSelected>>')
    root.update()
    labels = [w for w in descendants(dialog) if isinstance(w, ttk.Label)]
    assert not any(w.winfo_ismapped() and str(w.cget('text')).startswith('完工') for w in labels)
    assert any(w.winfo_ismapped() and '预收款' in str(w.cget('text')) for w in labels)
    dialog.attributes('-topmost', True)
    dialog.lift()
    dialog.focus_force()
    root.update()
    time.sleep(0.3)
    folder = Path('qa/advance_receipts')
    folder.mkdir(parents=True, exist_ok=True)
    ImageGrab.grab(bbox=(dialog.winfo_rootx(), dialog.winfo_rooty(),
                        dialog.winfo_rootx()+dialog.winfo_width(),
                        dialog.winfo_rooty()+dialog.winfo_height())).save(folder/'receipt.png')
    assert not errors, errors
    print('PASS native receipt form without completion fields')
finally:
    root.destroy()
