"""Exercise the actual contract dialog against an isolated online snapshot."""
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def main():
    from db.backup import backup_database
    from db import connection
    with tempfile.TemporaryDirectory(prefix='no_invoice_contract_') as folder:
        target = backup_database(connection.DB_PATH, Path(folder) / 'test.db')
        connection.DB_PATH = target
        import ttkbootstrap as ttk
        from pages.contract_page import ContractManagementPage
        from services import project_service, contract_service
        from ui.theme import configure_design_system

        root = ttk.Window(themename='cosmo')
        try:
            root.geometry('1200x800')
            configure_design_system(root)
            page = ContractManagementPage(root)
            root.update()
            page.open_allocation_dialog()
            root.update()
            dialog = next(w for w in root.winfo_children() if isinstance(w, ttk.Toplevel))
            combos = [w for w in descendants(dialog) if isinstance(w, ttk.Combobox)]
            project_combo = next(w for w in combos if any('前丁赤圣庵改造戏棚' in v for v in w['values']))
            project_label = next(v for v in project_combo['values'] if '前丁赤圣庵改造戏棚' in v)
            assert '无需开票' in project_label
            contract_combo = next(w for w in combos if any('HT-20260911-E5FD53' in v for v in w['values']))
            contract_combo.set(next(v for v in contract_combo['values'] if 'HT-20260911-E5FD53' in v))
            contract_combo.event_generate('<<ComboboxSelected>>')
            project_combo.set(project_label)
            amount = next(w for w in descendants(dialog) if w.winfo_class() == 'TEntry')
            amount.delete(0, 'end')
            amount.insert(0, '86000')
            root.update()
            button = next(w for w in descendants(dialog) if isinstance(w, ttk.Button) and w['text'] == '确认关联/分配')
            assert button.winfo_ismapped()
            with patch('pages.contract_page.messagebox.showwarning') as warning:
                button.invoke()
                assert not warning.called, str(warning.call_args)
            root.update()
            assert not dialog.winfo_exists(), 'Association did not save'
            project = project_service.get_project(32)
            assert project['business_mode'] == 'contract'
            assert project['invoice_policy'] == 'not_required'
            links = [r for r in contract_service.list_allocations() if r['project_id'] == 32]
            assert len(links) == 1 and links[0]['allocated_amount_minor'] == 8600000
            print('Native dialog: target selectable, save successful, 86000 contract linked without invoice requirement.')
            print('Production data untouched; visual pixel inspection not performed.')
        finally:
            root.destroy()


if __name__ == '__main__':
    main()
