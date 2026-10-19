"""Open and close the real WebView2 workbench on fresh synthetic data, without Tk."""
import argparse
import importlib.abc
import importlib.util
import json
import logging
import os
from pathlib import Path
import socket
import sys
import time
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    output = parser.parse_args().output.resolve()
    root = Path(__file__).resolve().parents[1]
    if output.exists() or not output.is_relative_to(root / 'qa'):
        raise ValueError('Use a fresh synthetic acceptance directory inside qa')
    if importlib.util.find_spec('ttkbootstrap') is not None:
        raise ValueError('Run this acceptance in a clean environment without ttkbootstrap')
    output.mkdir(parents=True)
    sys.path.insert(0, str(root))
    os.environ.update(SUPPLY_CHAIN_BACKUP_DIR=str(output / 'backups'))
    logging.disable(logging.CRITICAL)

    class NoOldUI(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split('.')[0] in {'pages', 'ui', 'tkinter', 'ttkbootstrap'}:
                raise ImportError('Retired dependency: ' + fullname)

    sys.meta_path.insert(0, NoOldUI())
    from web_workbench.runtime import prepare_database
    prepare_database(demo=output / 'synthetic')
    from web_workbench import desktop
    import webview

    report = {'clean_environment': True, 'checks': [], 'errors': []}
    original_create = webview.create_window
    windows = []

    def create(*args, **kwargs):
        window = original_create(*args, **kwargs)
        windows.append(window)
        return window

    def inspect():
        window = windows[0]
        try:
            deadline = time.monotonic() + 35
            ready = False
            while time.monotonic() < deadline:
                try:
                    ready = window.evaluate_js("typeof state !== 'undefined' && !!state.boot && document.querySelector('#main')?.getAttribute('aria-busy') === 'false'")
                except Exception:
                    ready = False
                if ready:
                    break
                time.sleep(.1)
            if not ready:
                raise AssertionError('Native workbench did not become ready')
            mode = window.evaluate_js('({demo:state.boot.demo,readonly:state.boot.read_only})')
            if mode != {'demo': True, 'readonly': False}:
                raise AssertionError('The window is not using the synthetic writable ledger')
            report['origin'] = window.get_current_url().split('/#')[0].rstrip('/')
            report['title'] = window.title
            report['checks'].append('real WebView2 startup on synthetic writable data')
            target = window.evaluate_js("Array.from(document.querySelectorAll('a[href]')).find(link => link.textContent.includes('数据导入导出'))?.getAttribute('href')")
            if not target:
                raise AssertionError('Data tools navigation is missing')
            window.evaluate_js('location.hash=' + json.dumps(target))
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if window.evaluate_js("document.querySelector('#main')?.textContent.includes('数据导入导出') && document.querySelector('#main')?.getAttribute('aria-busy') === 'false'"):
                    report['checks'].append('native navigation to data import/export')
                    break
                time.sleep(.1)
            else:
                raise AssertionError('Data tools did not open')
        except Exception as error:
            report['errors'].append(str(error))
        finally:
            window.destroy()

    webview.create_window = create
    original_start = webview.start
    webview.start = lambda **kwargs: original_start(inspect, **kwargs)
    desktop.run_window(webview, read_only=False, demo=True, profile=output / 'profile')
    if 'origin' in report:
        address = urlsplit(report['origin'])
        try:
            with socket.create_connection((address.hostname, address.port), timeout=.5):
                report['errors'].append('Native server port was not released')
        except OSError:
            report['checks'].append('normal close releases the local server port')
    if any(name.split('.')[0] in {'pages', 'ui', 'tkinter', 'ttkbootstrap'} for name in sys.modules):
        report['errors'].append('A retired UI module was loaded')
    (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)
    if report['errors']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
