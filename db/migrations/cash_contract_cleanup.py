"""Compatibility marker for the withdrawn cash-contract cleanup rule."""


def preserve_cash_conversion_contracts(conn):
    """Keep version 420 ordered without changing contract archive status."""


MIGRATIONS = [
    (
        420,
        "保留现金工程转换后的合同档案",
        preserve_cash_conversion_contracts,
    ),
]
