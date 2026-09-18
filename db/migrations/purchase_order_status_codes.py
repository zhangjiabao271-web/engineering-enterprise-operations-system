"""purchase_orders.status 取值从中文（有效/作废）改为英文代码（active/void）。

purchase_orders 被 purchase_order_items（ON DELETE CASCADE）、
purchase_cost_allocation_lines（ON DELETE RESTRICT）等子表引用，而
migration_runner 在事务内强制外键约束：此时 DROP TABLE 的隐式 DELETE 会
触发子表级联删除或被 RESTRICT 直接阻断（实测 PRAGMA defer_foreign_keys
也无法在 COMMIT 时通过），经典的建新表→拷贝→DROP→RENAME 重建法对该表
不适用。因此本迁移先在旧模式下完成数据映射，再用 PRAGMA writable_schema
精准改写 sqlite_master 中 status 列的定义文本（仅 DEFAULT 与 CHECK，
列结构不变），全部索引、触发器和视图原样保留。
"""


STATUS_ANCHOR = "status TEXT NOT NULL DEFAULT '有效'"
STATUS_DEFINITION = (
    "status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','void'))"
)


def migrate_purchase_order_status_codes(conn):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='purchase_orders'"
    ).fetchone()
    if not row:
        return
    table_sql = row[0]
    normalized_sql = "".join(table_sql.lower().split())
    if "check(statusin('active','void'))" in normalized_sql:
        return  # 已是英文代码定义（例如新库直接按新结构建表）
    if STATUS_ANCHOR not in table_sql:
        raise RuntimeError("purchase_orders.status 定义与预期不符，迁移中止")

    # fund_guard_purchase_update 会把 status 变更视为业务修改并拦截，
    # 迁移期间临时卸下，数据映射完成后按原文重建。
    trigger_row = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type='trigger' AND name='fund_guard_purchase_update'"
    ).fetchone()
    trigger_sql = trigger_row[0] if trigger_row else None
    if trigger_sql:
        conn.execute("DROP TRIGGER fund_guard_purchase_update")
    conn.execute("UPDATE purchase_orders SET status='active' WHERE status='有效'")
    conn.execute("UPDATE purchase_orders SET status='void' WHERE status='作废'")
    remaining = conn.execute(
        "SELECT COUNT(*) FROM purchase_orders WHERE status NOT IN ('active', 'void')"
    ).fetchone()[0]
    if remaining:
        raise RuntimeError(
            f"purchase_orders.status 存在无法映射的取值，共 {remaining} 行"
        )
    if trigger_sql:
        conn.execute(trigger_sql)

    new_sql = table_sql.replace(STATUS_ANCHOR, STATUS_DEFINITION, 1)
    conn.execute("PRAGMA writable_schema=ON")
    try:
        conn.execute(
            "UPDATE sqlite_master SET sql=? WHERE type='table' AND name='purchase_orders'",
            (new_sql,),
        )
    finally:
        conn.execute("PRAGMA writable_schema=OFF")


MIGRATIONS = [
    (580, "采购单状态改为英文代码active/void", migrate_purchase_order_status_codes),
]
