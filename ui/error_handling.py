"""Present unexpected callback failures without exposing raw database errors."""

import logging
from tkinter import messagebox


def show_unexpected_error(title, *, parent=None):
    logging.exception("%s: unexpected UI action failure", title)
    messagebox.showerror(
        title,
        "操作状态未能确认。请先刷新核对，勿重复提交；如问题持续，请联系维护人员。",
        parent=parent,
    )
