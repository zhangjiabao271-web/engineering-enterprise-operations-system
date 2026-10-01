"""Translate expected SQLite business constraints at service write boundaries.

Only IntegrityError is eligible. Operational/database failures keep their original
type so callers can report a generic failure and preserve diagnostics.
"""

import sqlite3
from functools import wraps


_BUSINESS_TRIGGER_MESSAGES = {
    "该月已有工资资金流水，请先作废错误付款，再修改工天金额或月份",
    "该月已有工资付款，不能删除工天",
}


def translate_constraints(*, duplicate, related, invalid):
    """Convert known constraint races to ValueError with operation-specific text."""
    def decorate(action):
        @wraps(action)
        def wrapped(*args, **kwargs):
            try:
                return action(*args, **kwargs)
            except sqlite3.IntegrityError as error:
                detail = str(error)
                if detail in _BUSINESS_TRIGGER_MESSAGES:
                    message = detail
                elif detail.startswith("UNIQUE constraint failed"):
                    message = duplicate
                elif detail == "FOREIGN KEY constraint failed":
                    message = related
                elif detail.startswith(("CHECK constraint failed", "NOT NULL constraint failed")):
                    message = invalid
                else:
                    raise
                raise ValueError(message) from error
        return wrapped
    return decorate
