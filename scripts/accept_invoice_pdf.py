"""Exercise the sample PDF in an isolated native invoice dialog, without saving it."""
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
assert os.environ.get('SUPPLY_CHAIN_DB_PATH')
assert Path(os.environ['SUPPLY_CHAIN_DB_PATH']).resolve() != Path('supplier_data.db').resolve()
import ttkbootstrap as ttk
from PIL import ImageGrab
from pages.finance_page import ReceivablePage
from ui.theme import configure_design_system
from services.invoice_pdf_service import recognize


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


sample = sys.argv[1]
result = recognize(sample)
assert (result['amount'], result['net_amount'], result['tax_amount'], result['tax_rate']) == ('2600.00','2574.26','25.74','1')
root = ttk.Window(themename='flatly')
configure_design_system(root)
root.geometry('1200x800')
errors = []
root.report_callback_exception = lambda *args: errors.append(str(args))
try:
    parent = ttk.Frame(root)
    parent.pack(fill='both', expand=True)
    page = ReceivablePage(parent)
    page.open_invoice_dialog()
    root.update()
    dialog = next(w for w in descendants(root) if isinstance(w, ttk.Toplevel))
    button = next(w for w in descendants(dialog) if isinstance(w, ttk.Button)
                  and w.cget('text') == '选择 PDF 自动识别')
    with patch('pages.finance_page.filedialog.askopenfilename', return_value=sample), patch('pages.finance_page.messagebox.askyesno', return_value=True):
        button.invoke()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            root.update()
            if any(isinstance(w, ttk.Entry) and w.get() == '2574.26' for w in descendants(dialog)):
                break
            time.sleep(0.05)
    values = [w.get() for w in descendants(dialog) if isinstance(w, ttk.Entry)]
    for expected in ('2600.00', '2574.26', '25.74', '99990000000000000089'):
        assert expected in values, expected
    assert not errors, errors
    dialog.attributes('-topmost', True)
    dialog.lift()
    dialog.focus_force()
    root.update()
    time.sleep(0.3)
    folder = Path('qa/invoice_pdf')
    folder.mkdir(parents=True, exist_ok=True)
    ImageGrab.grab(bbox=(dialog.winfo_rootx(), dialog.winfo_rooty(),
                        dialog.winfo_rootx()+dialog.winfo_width(),
                        dialog.winfo_rooty()+dialog.winfo_height())).save(folder/'dialog.png')
    print('PASS local sample parsing and native auto-fill; sample not booked')
finally:
    root.destroy()
