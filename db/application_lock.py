"""One interactive writer per ledger, shared by the old and new entry points."""

import ctypes
import hashlib
import os
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def ledger_writer(database):
    if os.name != 'nt':
        yield
        return
    identity = hashlib.sha256(str(Path(database).resolve()).casefold().encode()).hexdigest()[:24]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateMutexW(None, False, 'Local\\EngineeringLedgerWriter-' + identity)
    if not handle:
        raise OSError('无法建立账本录入保护')
    try:
        if ctypes.get_last_error() == 183:
            raise ValueError('此账本已有录入窗口。请先保存并退出旧版或另一录入窗口，再打开本窗口。')
        yield
    finally:
        kernel.CloseHandle(handle)
