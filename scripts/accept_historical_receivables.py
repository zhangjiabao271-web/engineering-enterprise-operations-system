"""Run native UI checks against the rehearsed database only."""
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
assert os.environ.get('SUPPLY_CHAIN_DB_PATH'),'Must use an isolated database'
import ttkbootstrap as ttk
from pages.historical_receivable_page import HistoricalReceivablePage
from ui.theme import configure_design_system


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


root=ttk.Window(themename='flatly')
configure_design_system(root)
root.geometry('1180x780+30+30')
errors=[]
root.report_callback_exception=lambda *args: errors.append(str(args))
try:
    parent=ttk.Frame(root,padding=15)
    parent.pack(fill='both',expand=True)
    page=HistoricalReceivablePage(parent)
    root.update()
    assert page.projects.get_children()
    assert '¥150,000.00' in page.projects.item(page.projects.get_children()[0],'values')
    assert '待确认' in page.projects.item(page.projects.get_children()[0],'values')
    page.project_dialog()
    root.update()
    dialog=next(w for w in descendants(root) if isinstance(w,ttk.Toplevel))
    assert any(isinstance(w,ttk.Combobox) and len(w['values'])>0 for w in descendants(dialog))
    dialog.destroy()
    page.receipt_dialog()
    root.update()
    dialog=next(w for w in descendants(root) if isinstance(w,ttk.Toplevel))
    assert any(isinstance(w,ttk.Button) and w.cget('text')=='保存回款' for w in descendants(dialog))
    dialog.destroy()
    assert not errors,errors
    print('PASS historical project table, pending balance, receipt table, calendar receipt dialog, customer selection')
finally:
    root.destroy()
