"""Window construction checks using an isolated migrated DB, not visual acceptance."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
path = Path(sys.argv[1]).resolve()
if path == ROOT / 'supplier_data.db' or path.name != 'rehearsal.db':
    raise RuntimeError('Use the explicitly isolated rehearsal.db only')
os.environ['SUPPLY_CHAIN_DB_PATH'] = str(path)
os.environ['TCL_LIBRARY'] = str(ROOT / '.venv' / 'tcl' / 'tcl8.6')
os.environ['TK_LIBRARY'] = str(ROOT / '.venv' / 'tcl' / 'tk8.6')

import ttkbootstrap as ttk
from pages.finance_page import ReceivablePage
from services.operating_entity_service import RECORDS, list_records
from ui.theme import configure_design_system


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


root = ttk.Window(themename='litera')
root.withdraw()
root.geometry('1200x760')
root.title('经营主体与进项票 - 隔离测试')
errors = []
root.report_callback_exception = lambda *args: errors.append(str(args[1]))
configure_design_system(root)
frame = ttk.Frame(root)
frame.pack(fill='both', expand=True)
page = ReceivablePage(frame)
root.deiconify()
root.update()
for kind in RECORDS:
    print(kind, len(list_records(kind)))
for index in (5, 6):
    page.notebook.select(index)
    root.update()
    print('tab', page.notebook.tab(index, 'text'), 'constructed')
if '--preview' in sys.argv:
    root.after(300000, root.destroy)
    root.mainloop()
    sys.exit(0)
page.input_invoice_page.edit()
root.update()
dialogs = [widget for widget in descendants(root) if isinstance(widget, ttk.Toplevel)]
if not dialogs:
    raise RuntimeError('Entry dialog missing')
dialog = dialogs[-1]
buttons = [w for w in descendants(dialog) if isinstance(w, ttk.Button) and w.cget('text') == '确认保存']
if not buttons:
    raise RuntimeError('Save action missing')
print('entry_dialog', dialog.winfo_width(), dialog.winfo_height(),
      'save_button', buttons[0].winfo_width(), buttons[0].winfo_height())
dialog.destroy()
root.destroy()
if errors:
    raise RuntimeError(str(errors))
print('UI_CONSTRUCTION_OK; no visual or user-interaction acceptance claimed')
