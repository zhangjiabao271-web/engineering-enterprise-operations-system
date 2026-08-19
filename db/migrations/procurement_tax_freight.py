def add_procurement_tax_and_freight(conn):
    """Add supplier tax defaults and immutable purchase pricing snapshots."""
    profile_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(supplier_profiles)")
    }
    if "default_tax_rate_bps" not in profile_columns:
        conn.execute(
            """ALTER TABLE supplier_profiles
               ADD COLUMN default_tax_rate_bps INTEGER NOT NULL DEFAULT 0
               CHECK(default_tax_rate_bps BETWEEN 0 AND 10000)"""
        )

    order_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(purchase_orders)")
    }
    if "freight_amount_cents" not in order_columns:
        conn.execute(
            """ALTER TABLE purchase_orders
               ADD COLUMN freight_amount_cents INTEGER NOT NULL DEFAULT 0
               CHECK(freight_amount_cents >= 0)"""
        )

    item_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(purchase_order_items)")
    }
    additions = [
        (
            "material_unit_price_cents",
            "INTEGER NOT NULL DEFAULT 0 CHECK(material_unit_price_cents >= 0)",
        ),
        (
            "tax_rate_bps",
            "INTEGER NOT NULL DEFAULT 0 CHECK(tax_rate_bps BETWEEN 0 AND 10000)",
        ),
        (
            "material_amount_cents",
            "INTEGER NOT NULL DEFAULT 0 CHECK(material_amount_cents >= 0)",
        ),
        (
            "tax_amount_cents",
            "INTEGER NOT NULL DEFAULT 0 CHECK(tax_amount_cents >= 0)",
        ),
        (
            "tax_inclusive_unit_price_cents",
            "INTEGER NOT NULL DEFAULT 0 CHECK(tax_inclusive_unit_price_cents >= 0)",
        ),
    ]
    added_item_columns = set()
    for name, definition in additions:
        if name not in item_columns:
            conn.execute(
                f"ALTER TABLE purchase_order_items ADD COLUMN {name} {definition}"
            )
            added_item_columns.add(name)

    # Historical purchases are accounting facts. Preserve their original amount
    # without retroactively applying a supplier's current tax setting.
    if added_item_columns:
        conn.execute(
            """UPDATE purchase_order_items
               SET material_unit_price_cents=unit_price_cents,
                   tax_rate_bps=0,
                   material_amount_cents=line_amount_cents,
                   tax_amount_cents=0,
                   tax_inclusive_unit_price_cents=unit_price_cents"""
        )

    # The user confirmed Meifeng Steel normally carries 10% tax. Existing
    # active offers are master data rather than historical purchase snapshots,
    # so they can safely adopt the supplier default.
    conn.execute(
        """UPDATE supplier_profiles
           SET default_tax_rate_bps=1000
           WHERE partner_id IN (
               SELECT id FROM business_partners WHERE legal_name='砺锋钢铁'
           )"""
    )
    conn.execute(
        """UPDATE supplier_offers
           SET tax_rate_bps=1000
           WHERE tax_rate_bps=0
             AND supplier_partner_id IN (
                 SELECT id FROM business_partners WHERE legal_name='砺锋钢铁'
             )"""
    )


MIGRATIONS = [
    (140, "V3 供应商税率、采购税额与项目运费快照", add_procurement_tax_and_freight),
]
