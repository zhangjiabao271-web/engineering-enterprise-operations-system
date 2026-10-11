"""Native WebView2 window; business services remain in the local Python process."""

import argparse
import ctypes
import hashlib
import json
import logging
import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class PresentationPreferences:
    """The native bridge exposes only two visual preferences, never ledger access."""
    def __init__(self, path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._values = {'reduced': False, 'solid': False}
        try:
            if self._path.stat().st_size <= 4096:
                values = json.loads(self._path.read_text(encoding='utf-8'))
                if isinstance(values, dict):
                    self._values.update({key: values[key] for key in self._values if type(values.get(key)) is bool})
        except (OSError, ValueError):
            pass

    def get_ui_preferences(self):
        with self._lock:
            return dict(self._values)

    def save_ui_preferences(self, reduced, solid):
        if type(reduced) is not bool or type(solid) is not bool:
            raise ValueError('视觉设置仅接受开关值')
        with self._lock:
            values = {'reduced': reduced, 'solid': solid}
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix('.tmp')
            temporary.write_text(json.dumps(values), encoding='utf-8')
            temporary.replace(self._path)
            self._values = values
            return dict(values)


@contextmanager
def single_window(database):
    """Do not open the same WebView profile in two independent processes."""
    if os.name != 'nt':
        yield
        return
    identity = hashlib.sha256(str(database).casefold().encode()).hexdigest()[:24]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateMutexW(None, False, 'Local\\EngineeringWorkbench-' + identity)
    if not handle:
        raise OSError('无法创建本地窗口锁')
    already_open = ctypes.get_last_error() == 183
    try:
        if already_open:
            raise ValueError('此账本的网页桌面版已经打开，请切回现有窗口。')
        yield
    finally:
        kernel.CloseHandle(handle)


def run_window(webview, *, read_only, demo, profile):
    from web_workbench.runtime import local_server
    webview.settings['ALLOW_DOWNLOADS'] = True
    # Reports/attachment links remain explicit user-initiated browser/download actions.
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = True
    with local_server(read_only=read_only, demo=demo) as server:
        title = '工程经营工作台'
        if demo:
            title += ' · 演示'
        elif read_only:
            title += ' · 只读'
        width, height = window_size()
        webview.create_window(title, server.origin, width=width, height=height,
                              min_size=(min(960, width), min(680, height)), background_color='#F5F5F7',
                              maximized=True, text_select=True,
                              js_api=PresentationPreferences(profile / 'ui-preferences.json'))
        webview.start(gui='edgechromium', debug=False, private_mode=False,
                      storage_path=str(profile), icon=str(ROOT / 'app.ico'))


def window_size(work_area=None):
    """Fit the first window above the taskbar, including on smaller displays."""
    if work_area is None and os.name == 'nt':
        from ctypes.wintypes import RECT
        rectangle = RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rectangle), 0):
            work_area = (rectangle.right - rectangle.left, rectangle.bottom - rectangle.top)
    if work_area is None:
        return 1280, 800
    return min(1380, max(320, work_area[0] - 32)), min(920, max(480, work_area[1] - 32))


def main(argv=None):
    parser = argparse.ArgumentParser(description='工程经营工作台 · 独立本地软件窗口')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--db', type=Path, help='指定现有账本；默认正式库只读')
    group.add_argument('--demo', type=Path, help='全新合成演示目录，绝不使用正式库')
    parser.add_argument('--allow-write', action='store_true', help='明确启用写入；启动前备份账本')
    args = parser.parse_args(argv)
    database = (args.demo / 'supplier_data.db' if args.demo else args.db or ROOT / 'supplier_data.db').resolve()
    local = ROOT / '.local' / 'desktop'
    try:
        local.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=local / 'desktop.log', level=logging.INFO,
                            format='%(asctime)s %(levelname)s %(message)s', encoding='utf-8')
        try:
            import webview
        except ImportError as error:
            raise ValueError('桌面窗口依赖未安装，请按桌面版说明安装 requirements-desktop.txt') from error
        with single_window(database):
            from web_workbench.runtime import prepare_database
            selected_db = None if args.demo else args.db or ROOT / 'supplier_data.db'
            prepare_database(db=selected_db, demo=args.demo, allow_write=args.allow_write)
            identity = hashlib.sha256(str(database).casefold().encode()).hexdigest()[:16]
            profile = local / ('profile-' + identity)
            run_window(webview, read_only=not (args.demo or args.allow_write),
                       demo=bool(args.demo), profile=profile)
    except Exception as error:
        logging.exception('本地窗口未能运行')
        message = (str(error) if isinstance(error, ValueError) else
                   '桌面窗口启动未完成。请检查 Microsoft Edge WebView2 运行时及 .local/desktop/desktop.log。账本未自动迁移。')
        if os.name == 'nt':
            ctypes.windll.user32.MessageBoxW(None, message, '工程经营工作台', 0x10)
        elif sys.stderr:
            print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
