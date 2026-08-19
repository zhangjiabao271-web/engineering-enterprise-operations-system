"""Shared calendar-backed date field for desktop forms."""

from datetime import date, datetime

import ttkbootstrap as ttk
from ttkbootstrap.constants import LEFT, X
from ttkbootstrap.dialogs import Querybox


DATE_FORMAT = "%Y-%m-%d"


class DatePicker(ttk.Frame):
    """Readonly date field that is changed through a calendar popup."""

    def __init__(
        self,
        parent,
        *,
        textvariable=None,
        allow_empty=False,
        width=12,
        popup_title="选择日期",
        **kwargs,
    ):
        super().__init__(parent, **kwargs)
        self.variable = (
            textvariable if textvariable is not None else ttk.StringVar(master=self)
        )
        self.allow_empty = allow_empty
        self.popup_title = popup_title
        self._enabled = True

        entry_options = {
            "textvariable": self.variable,
            "state": "readonly",
        }
        entry_options["width"] = width
        self.entry = ttk.Entry(self, **entry_options)
        self.entry.pack(side=LEFT, fill=X, ipady=4)
        self.entry.bind("<Button-1>", self._open_from_event)
        self.entry.bind("<Return>", self._open_from_event)
        self.entry.bind("<space>", self._open_from_event)

        self.select_button = ttk.Button(
            self,
            text="选择",
            bootstyle="secondary-outline",
            command=self.open_calendar,
            width=4,
        )
        self.select_button.pack(side=LEFT, padx=(8, 0))

        self.clear_button = None
        if allow_empty:
            self.clear_button = ttk.Button(
                self,
                text="清空",
                bootstyle="secondary-outline",
                command=self.clear,
                width=4,
            )
            self.clear_button.pack(side=LEFT, padx=(8, 0))

    def _open_from_event(self, _event=None):
        if not self._enabled:
            return "break"
        self.open_calendar()
        return "break"

    def open_calendar(self):
        if not self._enabled:
            return
        current = self.variable.get().strip()
        try:
            start_date = datetime.strptime(current, DATE_FORMAT).date()
        except ValueError:
            start_date = date.today()
        selected = Querybox.get_date(
            parent=self,
            title=self.popup_title,
            firstweekday=0,
            startdate=start_date,
            bootstyle="secondary",
        )
        if selected is None:
            return
        self.variable.set(selected.strftime(DATE_FORMAT))
        self.event_generate("<<DateSelected>>")

    def clear(self):
        if not self.allow_empty or not self._enabled:
            return
        self.variable.set("")
        self.event_generate("<<DateSelected>>")

    def get(self):
        return self.variable.get()

    def focus_set(self):
        return self.entry.focus_set()

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        state = "normal" if enabled else "disabled"
        self.entry.configure(state="readonly" if enabled else "disabled")
        self.select_button.configure(state=state)
        if self.clear_button is not None:
            self.clear_button.configure(state=state)
