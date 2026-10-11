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
_FUND_TRIGGER_MESSAGES = {
    '已有资金流水关联，请先在资金管理中作废错误流水，再修改原单据':
        '该记录已关联有效资金流水，不能单独修改金额、日期等资金依据或作废。'
        '请先在资金与计划核对关联流水；只有录错的流水才能作废，真实到账和付款须保留。原记录未改动。',
    '已关联资金历史，不能删除原单据':
        '该记录保留了资金流水历史，不能删除原单据，请按业务更正流程处理。',
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
                elif detail in _FUND_TRIGGER_MESSAGES:
                    message = _FUND_TRIGGER_MESSAGES[detail]
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
