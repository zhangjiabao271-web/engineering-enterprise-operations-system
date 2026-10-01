"""Measure actual Tk callback cadence on an isolated database copy."""
import argparse
import os
from pathlib import Path
import statistics
import sys
import tempfile
from time import perf_counter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('database', type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    with tempfile.TemporaryDirectory(prefix='page_motion_') as folder:
        database = Path(folder)/'test.db'
        os.environ['SUPPLY_CHAIN_DB_PATH'] = str(database)
        os.environ['SUPPLY_CHAIN_ATTACHMENTS_PATH'] = str(Path(folder)/'attachments')
        from db.backup import backup_database
        backup_database(args.database, database)
        import ttkbootstrap as ttk
        from main import SupplierManagerApp
        from ui.page_transition import PageTransition, motion_is_enabled

        ticks = []
        phases = {}
        original = PageTransition._step
        def measured(transition):
            ticks.append(perf_counter())
            return original(transition)
        PageTransition._step = measured
        originals = {}
        for name in ('_cover_current', '_start', '_begin'):
            originals[name] = getattr(PageTransition, name)
            def timed(transition, _name=name):
                if _name == '_begin':
                    phases['reveal_ms'] = (perf_counter() - phases['requested']) * 1000
                started = perf_counter()
                result = originals[_name](transition)
                phases[_name] = (perf_counter() - started) * 1000
                return result
            setattr(PageTransition, name, timed)
        root = ttk.Window(themename='flatly')
        root.title('切页性能验收（隔离副本）')
        errors = []
        root.report_callback_exception = lambda *error: errors.append(error)
        app = SupplierManagerApp(root)
        app.page_transition.set_enabled(True)
        animation_expected = motion_is_enabled()

        def settle():
            done = ttk.BooleanVar(value=False)
            deadline = perf_counter() + 10
            def check():
                if app.page_transition._timer is None or perf_counter() >= deadline:
                    done.set(True)
                else:
                    root.after(16, check)
            root.after(32, check)
            root.wait_variable(done)
            assert app.page_transition._timer is None

        results = []
        try:
            root.update()
            settle()
            for key in ('supplier', 'purchase', 'finance', 'home', 'workday') * 2:
                ticks.clear()
                start = perf_counter()
                phases.clear()
                phases['requested'] = start
                app.navigate_to(key)
                build_ms = (perf_counter()-start)*1000
                settle()
                gaps = [(b-a)*1000 for a,b in zip(ticks,ticks[1:])]
                mean = statistics.mean(gaps) if gaps else 0
                peak = max(gaps) if gaps else 0
                print(
                    f'{key}: build={build_ms:.1f}ms '
                    f'cover={phases.get("_cover_current", 0):.1f}ms '
                    f'layout={phases.get("_start", 0):.1f}ms '
                    f'reveal={phases.get("reveal_ms", 0):.1f}ms '
                    f'frames={len(ticks)} mean={mean:.1f}ms max={peak:.1f}ms',
                    flush=True,
                )
                results.append((key, len(ticks), mean, peak))
                assert app.page_transition._overlay is None
                assert app.page_transition._image is None
            assert not errors, errors
            if animation_expected:
                assert all(frames >= 6 and mean <= 40 and peak <= 100 for _,frames,mean,peak in results), results
            else:
                assert all(frames == 0 for _,frames,_,_ in results), results
        finally:
            PageTransition._step = original
            for name, method in originals.items():
                setattr(PageTransition, name, method)
            root.destroy()
        if animation_expected:
            print('Motion cadence passed; callback timing is not a display-refresh/FPS measurement.')
        else:
            print('Reduced motion respected; animated frame measurement skipped.')


if __name__ == '__main__':
    main()
