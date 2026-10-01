"""View reuse must reload facts and keep project-scoped navigation intact."""

import os
from pathlib import Path
import tempfile
from time import monotonic
import unittest
from unittest.mock import Mock, patch


class ViewReuseTests(unittest.TestCase):
    def test_reentry_reuses_widgets_and_runs_every_loader_in_order(self):
        from main import SupplierManagerApp

        app = SupplierManagerApp.__new__(SupplierManagerApp)
        frame, page = Mock(), Mock()
        app._page_views = {}
        app.content_frame = frame
        app.clear_content = Mock()
        app.set_active_nav = Mock()
        factory = Mock(return_value=page)
        app._show_reusable_page('finance', factory, ('refresh',))
        factory.assert_called_once_with(frame)
        page.refresh.assert_not_called()
        app._show_reusable_page('finance', factory, ('refresh',), project_id=42)
        factory.assert_called_once()
        app.clear_content.assert_called_with(frame)
        page.refresh.assert_called_once_with()
        self.assertEqual(page.initial_project_id, 42)
        self.assertEqual(app.current_page, 'finance')

    def test_only_registered_views_are_retained(self):
        from main import SupplierManagerApp

        cached, disposable = Mock(), Mock()
        app = SupplierManagerApp.__new__(SupplierManagerApp)
        app._page_views = {'supplier': (cached, Mock())}
        app._retire_content(cached)
        app._retire_content(disposable)
        cached.place_forget.assert_called_once()
        cached.destroy.assert_not_called()
        disposable.destroy.assert_called_once()

    def test_dashboard_refresh_moves_to_current_month(self):
        from pages.home_page import OperationsDashboardPage

        page = OperationsDashboardPage.__new__(OperationsDashboardPage)
        page.month = '2026-09'
        page.stat_label = Mock()
        page._refresh_data = Mock()
        with patch('pages.home_page.datetime') as clock:
            clock.now.return_value.strftime.return_value = '2026-10'
            page.refresh()
        self.assertEqual(page.month, '2026-10')
        page.stat_label.configure.assert_called_once_with(text='统计期 2026-10')
        page._refresh_data.assert_called_once()


@unittest.skipUnless(os.environ.get('SUPPLY_CHAIN_GUI_TESTS') == '1', 'Opt-in Tk tests')
class ViewReuseGuiTests(unittest.TestCase):
    def test_actual_views_reload_data_and_do_not_grow_on_navigation(self):
        import ctypes
        import ttkbootstrap as ttk
        from db import connection
        from db.backup import backup_database
        from main import SupplierManagerApp
        from services import master_data_service, project_service
        from ui.page_transition import animate_dialog_open

        ctypes.windll.kernel32.SetErrorMode(2)
        with tempfile.TemporaryDirectory(prefix='view_reuse_') as folder:
            target = Path(folder) / 'test.db'
            backup_database(connection.DB_PATH, target)
            with patch.object(connection, 'DB_PATH', target), patch.dict(
                os.environ, SUPPLY_CHAIN_ATTACHMENTS_PATH=str(Path(folder) / 'attachments')
            ):
                root = ttk.Window(themename='flatly')
                errors = []
                root.report_callback_exception = lambda *error: errors.append(error)
                try:
                    app = SupplierManagerApp(root)
                    app.page_transition.set_enabled(False)
                    root.update()
                    app.navigate_to('supplier')
                    root.update()
                    supplier_frame, suppliers = app._page_views['supplier']
                    new_id = master_data_service.create_supplier({'name': '界面刷新验收供应商'})
                    app.navigate_to('finance')
                    root.update()
                    finance_frame, finance = app._page_views['finance']
                    new_project = project_service.create_project({'name': '界面缓存验收项目'})
                    app.navigate_to('supplier')
                    root.update()
                    self.assertIs(app.content_frame, supplier_frame)
                    self.assertIn(new_id, [row['id'] for row in suppliers.filtered_rows])
                    app.navigate_to('finance', project_id=new_project)
                    root.update()
                    self.assertIs(app.content_frame, finance_frame)
                    self.assertEqual(finance.selected_project_id(), new_project)
                    app.navigate_to('finance', project_id=new_project)
                    root.update()
                    self.assertEqual(finance.selected_project_id(), new_project)
                    for key in ('purchase', 'workday', 'home', 'supplier', 'finance') * 2:
                        app.navigate_to(key)
                        root.update()
                        frames = app.content_host.winfo_children()
                        self.assertEqual(sum(bool(frame.winfo_ismapped()) for frame in frames), 1)
                        self.assertLessEqual(len(frames), 5)
                    self.assertEqual(len(app._page_views), 5)
                    app.navigate_to('purchase')
                    root.update()
                    purchase = app._page_views['purchase'][1]
                    purchase.project_tooltip.show_tip()
                    self.assertIsNotNone(purchase.project_tooltip.toplevel)
                    app.navigate_to('supplier')
                    root.update()
                    self.assertIsNone(purchase.project_tooltip.toplevel)
                    # Resize a reused view; no stale window snapshot may remain.
                    root.geometry('1250x810')
                    root.update()
                    self.assertIsNone(app.page_transition._overlay)
                    app.page_transition.set_enabled(True)
                    app.navigate_to('supplier')
                    root.update_idletasks()
                    root.geometry('1260x820')
                    root.update()
                    self.assertIsNone(app.page_transition._overlay)
                    # Rapid requests replace the pending transition, never queue it.
                    for key in ('purchase', 'workday', 'finance', 'supplier'):
                        app.navigate_to(key)
                    done = ttk.BooleanVar(value=False)
                    deadline = monotonic() + 5
                    def check_finished():
                        if app.page_transition._timer is None or monotonic() >= deadline:
                            done.set(True)
                        else:
                            root.after(16, check_finished)
                    root.after(16, check_finished)
                    root.wait_variable(done)
                    self.assertIsNone(app.page_transition._timer)
                    self.assertIsNone(app.page_transition._image)
                    self.assertIsNone(app.page_transition._overlay)
                    app.navigate_to('workday')
                    app.page_transition.set_enabled(False)
                    root.update()
                    self.assertIsNone(app.page_transition._timer)
                    dialog = ttk.Toplevel(root)
                    animate_dialog_open(dialog, root)
                    dialog.destroy()
                    # System/user preference changes also stop a dialog fade.
                    dialog = ttk.Toplevel(root)
                    animate_dialog_open(dialog, root)
                    app.motion_var.set(False)
                    done.set(False)
                    root.after(80, lambda: done.set(True))
                    root.wait_variable(done)
                    self.assertEqual(float(dialog.attributes('-alpha')), 1.0)
                    dialog.destroy()
                    self.assertFalse(errors, errors)
                finally:
                    root.destroy()


if __name__ == '__main__':
    unittest.main()
