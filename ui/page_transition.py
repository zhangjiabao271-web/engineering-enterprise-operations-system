"""Short page entrance motion; all pages use the same cancellable lifecycle."""

import os
from time import monotonic
from tkinter import TclError

import ttkbootstrap as ttk

from ui.scaling import scale_px, scale_treeview_columns


def motion_is_enabled():
    if os.environ.get("ENGINEERING_REDUCED_MOTION") == "1":
        return False
    try:
        from ctypes import byref, windll, wintypes

        enabled = wintypes.BOOL(True)
        if windll.user32.SystemParametersInfoW(0x1042, 0, byref(enabled), 0):
            return bool(enabled.value)
    except (AttributeError, OSError):
        pass
    return True


def animate_dialog_open(dialog, owner):
    preference = getattr(owner, "_motion_enabled", None)
    if not (preference.get() if preference is not None else motion_is_enabled()):
        return
    started = monotonic()
    timer = None

    def cancel(event):
        nonlocal timer
        if event.widget is dialog and timer is not None:
            dialog.after_cancel(timer)
            timer = None

    def step():
        nonlocal timer
        timer = None
        progress = min(1.0, (monotonic() - started) / 0.16)
        dialog.attributes("-alpha", 1 - 0.16 * (1 - progress) ** 3)
        if progress < 1:
            timer = dialog.after(16, step)

    try:
        dialog.attributes("-alpha", 0.84)
    except TclError:
        return
    dialog.bind("<Destroy>", cancel, add="+")
    timer = dialog.after(16, step)


class PageTransition:
    DURATION = 0.20

    def __init__(self, host):
        self.host = host
        self.enabled = motion_is_enabled()
        self.current = None
        self.previous = None
        self._timer = None
        host.bind("<Destroy>", self._on_destroy, add="+")

    def new_page(self):
        self.finish()
        self.previous = self.current
        self.current = ttk.Frame(self.host, padding=scale_px(self.host, 24))
        self.current.place(x=0, y=0, relwidth=1, relheight=1)
        if self.previous is not None:
            self.current.lower(self.previous)
        self._timer = self.host.after_idle(self._start)
        return self.current

    def _start(self):
        self._timer = None
        scale_treeview_columns(self.current)
        self.current.lift()
        if not self.enabled or self.previous is None or not self.host.winfo_viewable():
            self.finish()
            return
        self._started = monotonic()
        self._step()

    def _step(self):
        self._timer = None
        progress = min(1.0, (monotonic() - self._started) / self.DURATION)
        offset = round(scale_px(self.host, 26) * (1 - progress) ** 3)
        self.current.place_configure(x=offset)
        if progress >= 1:
            self.finish()
        else:
            self._timer = self.host.after(16, self._step)

    def finish(self):
        if self._timer is not None:
            self.host.after_cancel(self._timer)
            self._timer = None
        if self.current is not None and self.current.winfo_exists():
            self.current.place_configure(x=0)
        if self.previous is not None:
            self.previous.destroy()
            self.previous = None

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        if not self.enabled:
            self.finish()

    def _on_destroy(self, event):
        if event.widget is self.host and self._timer is not None:
            self.host.after_cancel(self._timer)
            self._timer = None
