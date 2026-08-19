"""Service-layer shared helpers.

These small utilities were duplicated across every service module.  Keeping
them here gives one place to adjust organization resolution, timestamp format
and money conversion rules.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

DEFAULT_ORG_CODE = "DEFAULT"


def now():
    """ISO-8601 local timestamp with seconds precision (storage format)."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def organization_id(conn):
    """Resolve the default operating organization id from the connection."""
    row = conn.execute(
        "SELECT id FROM organizations WHERE organization_code=?",
        (DEFAULT_ORG_CODE,),
    ).fetchone()
    if not row:
        raise RuntimeError("默认经营主体不存在")
    return row["id"]


def minor(value, *, allow_zero=False):
    """Convert a decimal amount to integer minor units (round half up).

    Default is the strict contract/cost/finance behavior: values <= 0 are
    rejected.  Set allow_zero=True for lenient call sites (e.g. labor rates)
    where zero is a valid amount.
    """
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValueError("金额必须是有效数字")
    if not allow_zero and amount <= 0:
        raise ValueError("金额必须大于 0")
    if amount < 0:
        raise ValueError("金额不能为负数")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
