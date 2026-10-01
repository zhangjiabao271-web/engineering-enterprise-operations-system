"""Short page entrance motion; all pages use the same cancellable lifecycle."""

import os
from math import ceil
from time import monotonic
from tkinter import TclError, Toplevel, Label

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


def _motion_allowed(owner, requested=True):
    if not requested or not motion_is_enabled():
        return False
    preference = getattr(owner, "_motion_enabled", None)
    return preference is None or bool(preference.get())


def _next_frame_delay(started, interval=1 / 120):
    """Target the next clock boundary; late callbacks never accumulate drift."""
    elapsed = max(0.0, monotonic() - started)
    remaining = interval - elapsed % interval
    return max(1, ceil(remaining * 1000))


def animate_dialog_open(dialog, owner):
    if not _motion_allowed(owner):
        return
    started = None
    timer = None

    def cancel(event):
        nonlocal timer
        if event.widget is dialog and timer is not None:
            dialog.after_cancel(timer)
            timer = None

    def step():
        nonlocal timer, started
        timer = None
        if not _motion_allowed(owner):
            dialog.attributes("-alpha", 1)
            return
        if started is None:
            # style_dialog runs before form construction; do not consume the
            # entrance duration while its widgets are still being created.
            started = monotonic()
        progress = min(1.0, (monotonic() - started) / 0.16)
        dialog.attributes("-alpha", 1 - 0.16 * (1 - progress) ** 3)
        if progress < 1:
            timer = dialog.after(_next_frame_delay(started), step)

    try:
        dialog.attributes("-alpha", 0.84)
    except TclError:
        return
    dialog.bind("<Destroy>", cancel, add="+")
    timer = dialog.after(8, step)


class PageTransition:
    DURATION = 0.16
    READY_TIMEOUT = 1.5

    def __init__(self, host, retire_page=None):
        self.host = host
        self.enabled = motion_is_enabled()
        self.current = None
        self.previous = None
        self._retire_page = retire_page or (lambda page: page.destroy())
        self._timer = None
        self._overlay = None
        self._image = None
        self._owner = host.winfo_toplevel()
        self._owner_bindings = {
            event: self._owner.bind(event, self._on_owner_change, add='+')
            for event in ('<Configure>', '<Unmap>')
        }
        host.bind("<Destroy>", self._on_destroy, add="+")
        host.bind("<Configure>", self._on_resize, add="+")

    def _cover_current(self):
        if (not _motion_allowed(self._owner, self.enabled) or self.current is None
                or not self.host.winfo_viewable() or os.name != 'nt'):
            return
        try:
            from PIL import ImageGrab, ImageTk

            # Capture only our own content window, never the desktop or other apps.
            snapshot = ImageGrab.grab(window=self.current.winfo_id())
            overlay = Toplevel(self.host)
            self._overlay = overlay
            overlay.withdraw()
            overlay.overrideredirect(True)
            overlay.transient(self.host.winfo_toplevel())
            overlay.attributes('-disabled', True)
            # Keep the new page composited while it lays out beneath this cover.
            overlay.attributes('-alpha', 0.99)
            overlay.geometry(
                f'{snapshot.width}x{snapshot.height}'
                f'+{self.host.winfo_rootx()}+{self.host.winfo_rooty()}'
            )
            self._image = ImageTk.PhotoImage(snapshot, master=overlay)
            Label(overlay, image=self._image, borderwidth=0, highlightthickness=0).pack()
            overlay.deiconify()
            overlay.lift()
            overlay.update_idletasks()
        except (OSError, TclError, TypeError):
            self._clear_overlay()

    def _clear_overlay(self):
        if self._overlay is not None:
            self._overlay.destroy()
            self._overlay = None
        self._image = None

    def new_page(self, page=None):
        interrupted = self._timer is not None
        self.finish()
        if page is self.current and page is not None:
            return page
        if not interrupted:
            self._cover_current()
        self.previous = self.current
        self.current = page if page is not None else ttk.Frame(
            self.host, padding=scale_px(self.host, 24)
        )
        self.current.place(x=0, y=0, relwidth=1, relheight=1)
        if self.previous is not None:
            self.current.lower(self.previous)
        self._timer = self.host.after_idle(self._start)
        return self.current

    def _start(self):
        self._timer = None
        if not getattr(self.current, '_transition_layout_scaled', False):
            scale_treeview_columns(self.current)
            self.current._transition_layout_scaled = True
        self.current.lift()
        if self.previous is not None:
            self._retire_page(self.previous)
            self.previous = None
        # Complete pending geometry/paint before compositing the snapshot away.
        # Reused pages avoid both rebuilding widgets and destroying the old view.
        self.host.update_idletasks()
        if (not _motion_allowed(self._owner, self.enabled)
                or self._overlay is None or not self.host.winfo_viewable()):
            self.finish()
            return
        self._ready_at = self._last_probe = monotonic()
        self._quiet_frames = 0
        self._timer = self.host.after(8, self._wait_ready)

    def _wait_ready(self):
        self._timer = None
        current = monotonic()
        self._quiet_frames = self._quiet_frames + 1 if current - self._last_probe < 0.04 else 0
        self._last_probe = current
        if current - self._ready_at >= self.READY_TIMEOUT:
            self.finish()
        elif self._quiet_frames >= 2:
            self._begin()
        else:
            self._timer = self.host.after(8, self._wait_ready)

    def _begin(self):
        self._timer = None
        self._started = monotonic()
        self._step()

    def _step(self):
        self._timer = None
        progress = min(1.0, (monotonic() - self._started) / self.DURATION)
        if self._overlay is None:
            self.finish()
            return
        if not _motion_allowed(self._owner, self.enabled):
            self.finish()
            return
        self._overlay.attributes('-alpha', 0.99 * (1 - progress) ** 3)
        if progress >= 1:
            self.finish()
        else:
            self._timer = self.host.after(_next_frame_delay(self._started), self._step)

    def finish(self):
        if self._timer is not None:
            self.host.after_cancel(self._timer)
            self._timer = None
        if self.current is not None and self.current.winfo_exists():
            self.current.place_configure(x=0)
        if self.previous is not None:
            self._retire_page(self.previous)
            self.previous = None
        self._clear_overlay()

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        if not self.enabled:
            self.finish()

    def _on_destroy(self, event):
        if event.widget is self.host:
            if self._timer is not None:
                self.host.after_cancel(self._timer)
                self._timer = None
            self._overlay = None
            self._image = None
            for sequence, binding in self._owner_bindings.items():
                self._owner.unbind(sequence, binding)

    def _on_resize(self, event):
        if event.widget is self.host and self._overlay is not None:
            self.finish()

    def _on_owner_change(self, event):
        if event.widget is self._owner and self._overlay is not None:
            self.finish()
