"""Disposable, synthetic conversation for visual design acceptance."""
import ctypes
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    ctypes.windll.kernel32.SetErrorMode(2)
    with tempfile.TemporaryDirectory(prefix='ai_design_preview_') as temporary:
        os.environ['SUPPLY_CHAIN_DB_PATH'] = str(Path(temporary) / 'preview.db')
        os.environ['SUPPLY_CHAIN_ATTACHMENTS_PATH'] = str(Path(temporary) / 'attachments')
        from db.schema import init_db
        import ttkbootstrap as ttk
        from pages.ai_page import AIAssistantPage
        from ui.theme import configure_design_system
        from ui.desktop_style import install_desktop_style
        from ui.scaling import scale_px
        init_db()
        root = ttk.Window(themename='flatly')
        root.title('经营助手 · 设计验收（演示数据）')
        root.geometry('1400x900+40+40')
        configure_design_system(root)
        install_desktop_style(root)
        nav = ttk.Frame(root, style='Sidebar.TFrame', width=scale_px(root, 180), padding=18)
        nav.pack(side='left', fill='y')
        nav.pack_propagate(False)
        ttk.Label(nav, text='工程经营', style='Brand.TLabel').pack(anchor='w', pady=(0, 30))
        for title in ('经营驾驶舱', '项目经营核算', '合同与结算', '开票与回款', '成本', '采购管理', '人工与工天'):
            ttk.Label(nav, text=title, style='SidebarStatus.TLabel').pack(anchor='w', pady=12)
        ttk.Button(nav, text='经营助手', style='NavActive.TButton').pack(fill='x', pady=30)
        host = ttk.Frame(root, padding=24)
        host.pack(fill='both', expand=True)
        with patch('pages.ai_page.safe_init_loaders'):
            page = AIAssistantPage(host)
        page.model_status_var.set('演示数据 · 不连接真实业务')
        page.scope_var.set('全公司')
        page.turn_status_var.set('准备就绪')
        source = {'module': '确认收入', 'label': '查看确认收入明细', 'page_key': 'finance',
                  'view_type': 'operating', 'scope_label': '演示数据', 'details': [], 'kpis': []}
        page.render_messages([
            {'role': 'user', 'content': '今年业务做得最多的是哪个客户？请按已经确认的收入金额比较，不要用合同金额或回款代替。'},
            {'role': 'assistant', 'message_type': 'answer', 'content':
             '今年已确认收入最多的客户是 **示例客户甲**，金额为 **¥1,762,000.00**。\n\n'
             '占今年已确认收入的 **33.66%**。\n\n'
             '范围：2026-01-01 至 2026-10-02\n口径：按收入确认日期统计，不用合同金额、开票或回款代替收入。',
             'metadata': {'answer_mode': 'local', 'question': '演示问题', 'sources': [source]}},
        ])
        root.after(120000, root.destroy)
        print('SYNTHETIC_PREVIEW_READY', flush=True)
        root.mainloop()


if __name__ == '__main__':
    main()
