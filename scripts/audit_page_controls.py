# 核查工具：真实启动完整应用，逐页导航、逐个 Notebook 标签选中后，
# 断言所有下拉框/按钮/表格真实可见（winfo_ismapped），并对指定页面截图存证。
# 用法: .venv/Scripts/python.exe scripts/audit_page_controls.py <数据库> [--shots 输出目录]
import argparse
import ctypes
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("database", type=Path)
parser.add_argument("--shots", type=Path, default=None, help="截图输出目录")
args = parser.parse_args()

temp_dir = Path(tempfile.mkdtemp(prefix="audit_"))
db_copy = temp_dir / "supplier_data.db"
shutil.copy2(args.database.resolve(), db_copy)
os.environ["SUPPLY_CHAIN_DB_PATH"] = str(db_copy)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

import ttkbootstrap as ttk

from main import SupplierManagerApp

Combobox = ttk.Combobox

CHECK_TYPES = (Combobox, ttk.Button, ttk.Treeview)

violations = []


def check_widget(widget, where):
    if isinstance(widget, CHECK_TYPES):
        try:
            mapped = widget.winfo_ismapped()
        except Exception:
            mapped = -1
        if mapped != 1:
            violations.append(
                f"[{where}] {widget.winfo_class()} {widget} 未映射"
            )


def walk(widget, where):
    """深度遍历；遇 Notebook 逐标签选中后再深入该标签子树。"""
    for child in widget.winfo_children():
        if isinstance(child, ttk.Notebook):
            for tab_id in child.tabs():
                child.select(tab_id)
                child.update_idletasks()
                tab_widget = child.nametowidget(tab_id)
                walk(tab_widget, where)
                check_widget(tab_widget, where)
        else:
            check_widget(child, where)
            walk(child, where)


def shot(root, path, crop_top=None):
    root.update_idletasks()
    root.update()
    time.sleep(0.4)
    root.lift()
    root.attributes("-topmost", True)
    root.update()
    time.sleep(0.2)
    from PIL import ImageGrab

    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    if crop_top:
        img = img.crop((0, 0, w, min(crop_top, h)))
    img.save(path)
    print("captured:", path)


root = ttk.Window(themename="flatly")
root.geometry("1200x800+60+30")
app = SupplierManagerApp(root)
try:
    root.update_idletasks()
    root.update()
    assert len(app.page_commands) == 17
    page_names = {
        "home": "经营驾驶舱", "governance": "数据治理中心",
        "workspace": "项目工作空间", "profit": "项目经营核算",
        "contract": "合同与结算", "finance": "开票与回款", "cost": "成本",
        "project": "项目台账", "purchase": "采购中心", "workday": "人工与工天",
        "supplier": "供应商", "customer": "客户", "product": "材料与报价", "compare": "报价对比",
        "construction": "施工与验收", "import_export": "数据导入导出", "ai": "AI 经营助手",
    }
    # 第一遍：默认视图截图（walk 会切换标签，先截保持初始状态）
    if args.shots:
        out = Path(args.shots)
        out.mkdir(parents=True, exist_ok=True)
        for i, key in enumerate(app.page_commands, 1):
            app.navigate_to(key)
            root.update_idletasks()
            root.update()
            name = page_names.get(key, key)
            shot(root, out / f"核查_{i:02d}_{name}_整页_20260808.png")
    # 第二遍：逐页逐标签选中后断言控件映射
    for key in app.page_commands:
        app.navigate_to(key)
        root.update_idletasks()
        root.update()
        walk(app.content_frame, key)

    # profit 页专项：下拉框必须 pack 进 FilterBar、真实可见且有实际宽度。
    # 注意：控件的 master 是页面根（content_frame），不能靠 winfo_children 判断，
    # 权威依据是 pack_info()["in"]。
    app.navigate_to("profit")
    root.update_idletasks()
    root.update()
    combo = None
    filterbar = None
    for w in app.content_frame.winfo_children():
        if isinstance(w, Combobox):
            combo = w
        if "filterbar" in str(w).lower():
            filterbar = w
    assert combo is not None and filterbar is not None, (
        f"profit 页缺少下拉框或 FilterBar: "
        f"{[str(w) for w in app.content_frame.winfo_children()]}"
    )
    assert combo.winfo_ismapped() == 1, "profit 页下拉框未映射"
    container = combo.pack_info().get("in")
    assert str(container) == str(filterbar), (
        f"下拉框容器不是 FilterBar: in={container}, filterbar={filterbar}"
    )
    assert combo.winfo_width() > 150, f"下拉框宽度异常: {combo.winfo_width()}"
    bar_x = filterbar.winfo_rootx()
    bar_right = bar_x + filterbar.winfo_width()
    assert bar_x <= combo.winfo_rootx() < bar_right, (
        f"下拉框屏幕位置不在 FilterBar 内: combo x={combo.winfo_rootx()}, "
        f"bar={bar_x}..{bar_right}"
    )
    print(
        f"profit 页下拉框正常: 容器={container}, "
        f"宽={combo.winfo_width()}px, 屏幕 x={combo.winfo_rootx()}"
    )
finally:
    root.destroy()
    shutil.rmtree(temp_dir, ignore_errors=True)

if violations:
    print(f"\n发现 {len(violations)} 处控件未映射:")
    for v in violations:
        print(" -", v)
    sys.exit(1)
print("\n全 17 页控件映射审计通过：所有下拉框/按钮/表格均可见。")
