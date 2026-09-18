"""Windows DPAPI-backed storage for the AI credential.

The encrypted blob is tied to the current Windows user.  No key material is
logged or included in raised errors.
"""

import ctypes
import os
from ctypes import wintypes
from pathlib import Path


class SecretStoreError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _blob(data):
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
    ), buffer


def protect_bytes(value):
    if os.name != "nt":
        raise SecretStoreError("当前系统不支持 Windows 安全存储")
    source, source_buffer = _blob(bytes(value))
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        "工程企业经营系统 AI 凭据",
        None,
        None,
        None,
        0,
        ctypes.byref(output),
    ):
        raise SecretStoreError("Windows 安全存储加密失败")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
        del source_buffer


def unprotect_bytes(value):
    if os.name != "nt":
        raise SecretStoreError("当前系统不支持 Windows 安全存储")
    source, source_buffer = _blob(bytes(value))
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(output),
    ):
        raise SecretStoreError("Windows 安全存储解密失败")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
        del source_buffer


def save_secret(secret, path, *, protect=protect_bytes):
    secret = str(secret or "").strip()
    target = Path(path)
    if not secret:
        delete_secret(target)
        return
    try:
        encrypted = protect(secret.encode("utf-8"))
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(encrypted)
        os.replace(temporary, target)
    except SecretStoreError:
        raise
    except OSError as error:
        raise SecretStoreError("无法保存 Windows 安全存储中的 AI 凭据") from error


def get_secret(path, *, unprotect=unprotect_bytes):
    target = Path(path)
    if not target.exists():
        return ""
    try:
        return unprotect(target.read_bytes()).decode("utf-8").strip()
    except (OSError, UnicodeError, SecretStoreError) as error:
        raise SecretStoreError("无法读取 Windows 安全存储中的 AI 凭据") from error


def delete_secret(path):
    target = Path(path)
    if target.exists():
        target.unlink()
