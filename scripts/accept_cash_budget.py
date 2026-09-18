"""Native budget-page checks on a disposable database, never the formal ledger."""
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def children(widget):
    for child in widget.winfo_children():
        yield child
        yield from children(child)


def main():
    from db import connection
    from db.backup import backup_database
    from db.migration_runner import run_migrations
    with tempfile.TemporaryDirectory(prefix='cash_budget_ui_') as folder:
        copy = backup_database(connection.DB_PATH, Path(folder) / 'test.db')
        run_migrations(copy)
        connection.DB_PATH = copy
        from services import cash_budget_service as budget
        from pages.funds_page import FundsPage
        from ui.theme import configure_design_system
        import ttkbootstrap as ttk
        with closing(sqlite3.connect(copy)) as conn:
            for table in ('cash_budget_rules', 'cash_budget_events', 'cash_budget_snapshots'):
                conn.execute(f'DELETE FROM {table}')
            conn.commit()
        root = ttk.Window(themename='cosmo')
        try:
            root.geometry('1200x800')
            configure_design_system(root)
            with patch.object(budget, 'sources', return_value=[]):
                page = FundsPage(root)
                root.update()
                assert page.notebook.index(page.notebook.select()) == 0
                assert page.budget_page.data['snapshot'] is None
                budget.save_snapshot({'balance_date': '2026-09-11', 'amount': '100000', 'reserve': '20000'})
                budget.save_rule({'title': '示例日常预算', 'category': '场地与日常运营 / 办公通信',
                                 'amount': '12000', 'cadence': 'daily', 'next_date': '2026-09-12'})
                page.budget_page.refresh()
                root.update()
                assert len(page.budget_page.timeline.tree.get_children()) == 180
                for key in page.budget_page.tabs:
                    page.budget_page.book.select(page.budget_page.tabs[key])
                    root.update()
                page.budget_page.book.select(page.budget_page.tabs['overview'])
                page.budget_page.open_snapshot()
                root.update()
                dialog = next(w for w in root.winfo_children() if isinstance(w, ttk.Toplevel))
                assert any(isinstance(w, ttk.Button) and w.winfo_ismapped() and w['text'] == '保存' for w in children(dialog))
                dialog.destroy()
                root.update()
                if '--screenshots' in sys.argv:
                    from PIL import ImageGrab
                    output = Path(__file__).resolve().parents[1] / 'qa' / 'cash_budget_20260911'
                    output.mkdir(parents=True, exist_ok=True)
                    root.attributes('-topmost', True)
                    root.update()
                    root.after(300, lambda: None)
                    box = (root.winfo_rootx(), root.winfo_rooty(), root.winfo_rootx() + root.winfo_width(), root.winfo_rooty() + root.winfo_height())
                    ImageGrab.grab(bbox=box).save(output / 'overview.png')
                    print('Screenshot:', output / 'overview.png')
                root.geometry('1000x700')
                root.update()
                assert page.budget_page.timeline.tree.winfo_ismapped()
                print('Native UI: first tab, no-ledger onboarding, 180-day timeline, four tabs, editable balance dialog, narrow window passed.')
        finally:
            root.destroy()


if __name__ == '__main__':
    main()
