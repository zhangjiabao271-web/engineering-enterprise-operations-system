"""Verify supplier categories on an isolated database; optionally deploy migration."""
import argparse
import sys
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


def children(widget):
    for child in widget.winfo_children():
        yield child
        yield from children(child)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root_dir = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root_dir))
    from db import connection
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from db.backup import backup_database
    from db.migration_runner import run_migrations
    from services import master_data_service as master
    source = connection.DB_PATH
    with closing(connection.get_connection()) as conn:
        columns = {r[0]: [c[1] for c in conn.execute(f'PRAGMA table_info("{r[0]}")')] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name<>'schema_migrations'").fetchall()}
        original = {name: [tuple(r) for r in conn.execute('SELECT ' + ','.join(f'"{c}"' for c in cols) + f' FROM "{name}"')] for name, cols in columns.items()}
    with tempfile.TemporaryDirectory(prefix="supplier_kind_") as folder:
        snapshot = backup_database(source, Path(folder) / "test.db")
        run_migrations(snapshot)
        connection.DB_PATH = snapshot
        try:
            ids = [master.create_supplier({"name": f"类型验收{i}", "supplier_kind": kind}) for i, kind in enumerate(master.SUPPLIER_KIND_LABELS)]
            with_error = master.get_business_partner(ids[0])
            try:
                master.update_business_partner(ids[0], {**with_error, "supplier_kind": "invalid"})
                raise AssertionError("Invalid kind accepted")
            except ValueError:
                pass
            assert master.get_supplier(ids[0])["supplier_kind"] == "unclassified"
            import ttkbootstrap as ttk
            from pages.supplier_page import SupplierPage
            from ui.theme import configure_design_system
            root = ttk.Window(themename="flatly")
            root.geometry("1366x768")
            configure_design_system(root)
            errors = []
            root.report_callback_exception = lambda *a: errors.append(str(a))
            try:
                page = SupplierPage(root)
                for code, label in master.SUPPLIER_KIND_LABELS.items():
                    page.load_data()
                    page.kind_tabs.select(page.kind_frames[code])
                    root.update()
                    assert page.table.tree.get_children()
                    assert all(master.get_supplier(int(i))["supplier_kind"] == code for i in page.table.tree.get_children())
                    assert "kind" not in page.table.tree["columns"]
                page.reset_filters()
                page.open_partner_dialog(ids[0])
                root.update()
                dialog = next(w for w in children(root) if isinstance(w, ttk.Toplevel))
                combo = next(w for w in children(dialog) if isinstance(w, ttk.Combobox) and tuple(w["values"]) == tuple(master.SUPPLIER_KIND_LABELS.values()))
                combo.set("生产厂家")
                save = next(w for w in children(dialog) if isinstance(w, ttk.Button) and w.cget("text") == "保存供应商档案")
                with patch("pages.supplier_page.messagebox.showwarning") as warning:
                    save.invoke()
                    root.update()
                    assert not warning.called, warning.call_args
                assert master.get_supplier(ids[0])["supplier_kind"] == "manufacturer"
                assert page.current_kind() == "manufacturer"
                assert page.table.tree.exists(str(ids[0]))
                page.search_var.set("类型验收0")
                page.load_data()
                root.update()
                assert page.table.tree.get_children() == (str(ids[0]),)
                assert str(page.kind_tabs.tab(page.kind_frames["unclassified"], "state")) == "hidden"
                page.kind_tabs.select(page.kind_frames["distributor"])
                root.update()
                assert not page.table.tree.get_children()
                page.reset_filters()
                root.update()
                assert page.current_kind() == "distributor"
                assert str(ids[2]) in page.table.tree.get_children()
                assert not errors, errors
            finally:
                root.destroy()
        finally:
            connection.DB_PATH = source
    print("PASS: three categories, filters/reset, edit/save/list refresh, invalid category rejection")
    if args.apply:
        target = root_dir / "backups" / f"before_supplier_kind_{datetime.now():%Y%m%d_%H%M%S_%f}.db"
        backup_database(source, target)
        result = run_migrations(source)
        assert result["applied"] == [490], result
        with closing(connection.get_connection()) as conn:
            for name, cols in columns.items():
                after = [tuple(r) for r in conn.execute('SELECT ' + ','.join(f'"{c}"' for c in cols) + f' FROM "{name}"')]
                assert after == original[name], name
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert not conn.execute("PRAGMA foreign_key_check").fetchall()
            assert not conn.execute("SELECT 1 FROM supplier_profiles WHERE supplier_kind<>'unclassified'").fetchone()
        print("PASS: schema 490; all original business columns unchanged; backup:", target)


if __name__ == "__main__":
    main()
