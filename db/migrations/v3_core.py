from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _public_id():
    return str(uuid4())


def _minor_units(value):
    return int((Decimal(str(value or 0)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _execute_script(conn, script):
    """Execute schema statements without sqlite3.executescript's implicit commit."""
    for statement in script.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)


def create_v3_core(conn):
    """Create the V3 master-data and project foundation without touching legacy tables."""
    _execute_script(conn, """
        CREATE TABLE IF NOT EXISTS organizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            organization_code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            base_currency_code TEXT NOT NULL DEFAULT 'CNY',
            is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
            created_at TEXT NOT NULL,
            created_by INTEGER,
            updated_at TEXT NOT NULL,
            updated_by INTEGER
        );

        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            organization_id INTEGER NOT NULL,
            employee_code TEXT,
            name TEXT NOT NULL,
            phone TEXT,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'inactive')),
            created_at TEXT NOT NULL,
            created_by INTEGER,
            updated_at TEXT NOT NULL,
            updated_by INTEGER,
            FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
            UNIQUE (organization_id, employee_code)
        );

        CREATE TABLE IF NOT EXISTS business_partners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            organization_id INTEGER NOT NULL,
            partner_code TEXT NOT NULL,
            legal_name TEXT NOT NULL,
            short_name TEXT,
            unified_credit_code TEXT,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'inactive', 'pending')),
            legacy_supplier_id INTEGER UNIQUE,
            notes TEXT,
            created_at TEXT NOT NULL,
            created_by INTEGER,
            updated_at TEXT NOT NULL,
            updated_by INTEGER,
            FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
            UNIQUE (organization_id, partner_code)
        );

        CREATE TABLE IF NOT EXISTS partner_roles (
            partner_id INTEGER NOT NULL,
            role_code TEXT NOT NULL CHECK(role_code IN ('customer', 'supplier', 'subcontractor', 'other')),
            created_at TEXT NOT NULL,
            PRIMARY KEY (partner_id, role_code),
            FOREIGN KEY (partner_id) REFERENCES business_partners(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS partner_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            partner_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            department TEXT,
            title TEXT,
            phone TEXT,
            email TEXT,
            wechat TEXT,
            is_primary INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0, 1)),
            is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (partner_id) REFERENCES business_partners(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS units_of_measure (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            unit_code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            decimal_places INTEGER NOT NULL DEFAULT 3 CHECK(decimal_places BETWEEN 0 AND 6),
            is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1))
        );

        CREATE TABLE IF NOT EXISTS material_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            organization_id INTEGER NOT NULL,
            category_code TEXT NOT NULL,
            name TEXT NOT NULL,
            parent_id INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
            FOREIGN KEY (parent_id) REFERENCES material_categories(id) ON DELETE RESTRICT,
            UNIQUE (organization_id, category_code)
        );

        CREATE TABLE IF NOT EXISTS materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            organization_id INTEGER NOT NULL,
            material_code TEXT NOT NULL,
            name TEXT NOT NULL,
            specification TEXT NOT NULL DEFAULT '',
            unit_id INTEGER,
            category_id INTEGER,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'inactive', 'pending')),
            created_at TEXT NOT NULL,
            created_by INTEGER,
            updated_at TEXT NOT NULL,
            updated_by INTEGER,
            FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
            FOREIGN KEY (unit_id) REFERENCES units_of_measure(id) ON DELETE RESTRICT,
            FOREIGN KEY (category_id) REFERENCES material_categories(id) ON DELETE RESTRICT,
            UNIQUE (organization_id, material_code)
        );

        CREATE TABLE IF NOT EXISTS supplier_offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            organization_id INTEGER NOT NULL,
            supplier_partner_id INTEGER NOT NULL,
            material_id INTEGER NOT NULL,
            legacy_product_id INTEGER UNIQUE,
            price_minor INTEGER NOT NULL DEFAULT 0 CHECK(price_minor >= 0),
            currency_code TEXT NOT NULL DEFAULT 'CNY',
            tax_rate_bps INTEGER NOT NULL DEFAULT 0 CHECK(tax_rate_bps BETWEEN 0 AND 10000),
            valid_from TEXT,
            valid_to TEXT,
            minimum_quantity TEXT,
            lead_time_days INTEGER CHECK(lead_time_days IS NULL OR lead_time_days >= 0),
            notes TEXT,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'inactive')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
            FOREIGN KEY (supplier_partner_id) REFERENCES business_partners(id) ON DELETE RESTRICT,
            FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS project_sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            project_id INTEGER NOT NULL,
            site_code TEXT NOT NULL,
            name TEXT NOT NULL,
            address TEXT,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
            legacy_construction_site_id INTEGER UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
            UNIQUE (project_id, site_code)
        );

        CREATE TABLE IF NOT EXISTS wbs_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            project_id INTEGER NOT NULL,
            parent_id INTEGER,
            wbs_code TEXT NOT NULL,
            name TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'closed')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
            FOREIGN KEY (parent_id) REFERENCES wbs_nodes(id) ON DELETE RESTRICT,
            UNIQUE (project_id, wbs_code)
        );

        CREATE TABLE IF NOT EXISTS cost_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            organization_id INTEGER NOT NULL,
            parent_id INTEGER,
            cost_code TEXT NOT NULL,
            name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
            FOREIGN KEY (parent_id) REFERENCES cost_codes(id) ON DELETE RESTRICT,
            UNIQUE (organization_id, cost_code)
        );

        CREATE INDEX IF NOT EXISTS idx_employees_org ON employees(organization_id);
        CREATE INDEX IF NOT EXISTS idx_partners_org_name ON business_partners(organization_id, legal_name);
        CREATE INDEX IF NOT EXISTS idx_partner_roles_role ON partner_roles(role_code);
        CREATE INDEX IF NOT EXISTS idx_partner_contacts_partner ON partner_contacts(partner_id);
        CREATE INDEX IF NOT EXISTS idx_materials_org_name ON materials(organization_id, name, specification);
        CREATE INDEX IF NOT EXISTS idx_supplier_offers_supplier ON supplier_offers(supplier_partner_id);
        CREATE INDEX IF NOT EXISTS idx_supplier_offers_material ON supplier_offers(material_id);
        CREATE INDEX IF NOT EXISTS idx_project_sites_project ON project_sites(project_id);
        CREATE INDEX IF NOT EXISTS idx_wbs_project ON wbs_nodes(project_id);
        CREATE INDEX IF NOT EXISTS idx_cost_codes_org ON cost_codes(organization_id);
    """)

    project_columns = {row[1] for row in conn.execute("PRAGMA table_info(projects)")}
    additions = [
        ("public_id", "TEXT"),
        ("organization_id", "INTEGER REFERENCES organizations(id)"),
        ("customer_partner_id", "INTEGER REFERENCES business_partners(id)"),
        ("manager_employee_id", "INTEGER REFERENCES employees(id)"),
        ("planned_start_date", "TEXT"),
        ("planned_end_date", "TEXT"),
    ]
    for name, definition in additions:
        if name not in project_columns:
            conn.execute(f"ALTER TABLE projects ADD COLUMN {name} {definition}")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_public_id ON projects(public_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_org ON projects(organization_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_customer ON projects(customer_partner_id)")


def migrate_v3_master_data(conn):
    """Map legacy suppliers, products, projects and sites into V3 master data."""
    now = _now()
    org = conn.execute(
        "SELECT id FROM organizations WHERE organization_code='DEFAULT'"
    ).fetchone()
    if not org:
        cursor = conn.execute(
            """INSERT INTO organizations
               (public_id, organization_code, name, base_currency_code, created_at, updated_at)
               VALUES (?, 'DEFAULT', '默认经营主体', 'CNY', ?, ?)""",
            (_public_id(), now, now),
        )
        organization_id = cursor.lastrowid
    else:
        organization_id = org["id"]

    for row in conn.execute("SELECT * FROM suppliers ORDER BY id").fetchall():
        partner = conn.execute(
            "SELECT id FROM business_partners WHERE legacy_supplier_id=?", (row["id"],)
        ).fetchone()
        if not partner:
            cursor = conn.execute(
                """INSERT INTO business_partners
                   (public_id, organization_id, partner_code, legal_name, short_name,
                    status, legacy_supplier_id, notes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)""",
                (_public_id(), organization_id, f"SUP-{row['id']:05d}", row["name"],
                 row["name"], row["id"], row["notes"], row["created_at"] or now, now),
            )
            partner_id = cursor.lastrowid
        else:
            partner_id = partner["id"]
        conn.execute(
            "INSERT OR IGNORE INTO partner_roles(partner_id, role_code, created_at) VALUES (?, 'supplier', ?)",
            (partner_id, now),
        )
        if row["contact"]:
            exists = conn.execute(
                "SELECT 1 FROM partner_contacts WHERE partner_id=? AND name=?",
                (partner_id, row["contact"]),
            ).fetchone()
            if not exists:
                conn.execute(
                    """INSERT INTO partner_contacts
                       (public_id, partner_id, name, is_primary, created_at, updated_at)
                       VALUES (?, ?, ?, 1, ?, ?)""",
                    (_public_id(), partner_id, row["contact"], now, now),
                )

    customer_names = conn.execute(
        "SELECT DISTINCT trim(customer_name) FROM projects WHERE trim(COALESCE(customer_name, '')) <> ''"
    ).fetchall()
    for index, (customer_name,) in enumerate(customer_names, 1):
        partner = conn.execute(
            "SELECT id FROM business_partners WHERE organization_id=? AND legal_name=?",
            (organization_id, customer_name),
        ).fetchone()
        if not partner:
            cursor = conn.execute(
                """INSERT INTO business_partners
                   (public_id, organization_id, partner_code, legal_name, short_name,
                    status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (_public_id(), organization_id, f"CUS-{index:05d}", customer_name, customer_name, now, now),
            )
            partner_id = cursor.lastrowid
        else:
            partner_id = partner["id"]
        conn.execute(
            "INSERT OR IGNORE INTO partner_roles(partner_id, role_code, created_at) VALUES (?, 'customer', ?)",
            (partner_id, now),
        )

    manager_names = conn.execute(
        "SELECT DISTINCT trim(manager) FROM projects WHERE trim(COALESCE(manager, '')) <> ''"
    ).fetchall()
    for index, (manager_name,) in enumerate(manager_names, 1):
        employee = conn.execute(
            "SELECT id FROM employees WHERE organization_id=? AND name=?",
            (organization_id, manager_name),
        ).fetchone()
        if not employee:
            conn.execute(
                """INSERT INTO employees
                   (public_id, organization_id, employee_code, name, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (_public_id(), organization_id, f"EMP-{index:05d}", manager_name, now, now),
            )

    for project in conn.execute("SELECT * FROM projects ORDER BY id").fetchall():
        customer = None
        if project["customer_name"]:
            customer = conn.execute(
                "SELECT id FROM business_partners WHERE organization_id=? AND legal_name=?",
                (organization_id, project["customer_name"].strip()),
            ).fetchone()
        manager = None
        if project["manager"]:
            manager = conn.execute(
                "SELECT id FROM employees WHERE organization_id=? AND name=?",
                (organization_id, project["manager"].strip()),
            ).fetchone()
        conn.execute(
            """UPDATE projects SET public_id=COALESCE(public_id, ?), organization_id=?,
               customer_partner_id=?, manager_employee_id=? WHERE id=?""",
            (_public_id(), organization_id, customer["id"] if customer else None,
             manager["id"] if manager else None, project["id"]),
        )
        conn.execute(
            """INSERT OR IGNORE INTO wbs_nodes
               (public_id, project_id, wbs_code, name, created_at, updated_at)
               VALUES (?, ?, 'ROOT', '项目总项', ?, ?)""",
            (_public_id(), project["id"], now, now),
        )

    for site in conn.execute("SELECT * FROM construction_sites ORDER BY id").fetchall():
        conn.execute(
            """INSERT OR IGNORE INTO project_sites
               (public_id, project_id, site_code, name, address, is_active,
                legacy_construction_site_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_public_id(), site["project_id"], f"SITE-{site['id']:04d}", site["site_name"],
             site["address"], site["is_active"], site["id"], site["created_at"], now),
        )

    unit_names = {row[0].strip() for row in conn.execute(
        "SELECT DISTINCT unit FROM products WHERE trim(COALESCE(unit, '')) <> ''"
    ).fetchall()}
    for index, unit_name in enumerate(sorted(unit_names), 1):
        conn.execute(
            "INSERT OR IGNORE INTO units_of_measure(unit_code, name) VALUES (?, ?)",
            (f"LEGACY-{index:03d}", unit_name),
        )

    material_keys = {}
    product_rows = conn.execute("SELECT * FROM products ORDER BY id").fetchall()
    for product in product_rows:
        key = (product["name"].strip(), (product["specification"] or "").strip(), (product["unit"] or "").strip())
        if key not in material_keys:
            material_keys[key] = len(material_keys) + 1
            unit = conn.execute("SELECT id FROM units_of_measure WHERE name=?", (key[2],)).fetchone() if key[2] else None
            conn.execute(
                """INSERT INTO materials
                   (public_id, organization_id, material_code, name, specification,
                    unit_id, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
                (_public_id(), organization_id, f"MAT-{material_keys[key]:05d}", key[0], key[1],
                 unit["id"] if unit else None, now, now),
            )
        material = conn.execute(
            """SELECT id FROM materials WHERE organization_id=? AND material_code=?""",
            (organization_id, f"MAT-{material_keys[key]:05d}"),
        ).fetchone()
        supplier = conn.execute(
            "SELECT id FROM business_partners WHERE legacy_supplier_id=?", (product["supplier_id"],)
        ).fetchone()
        conn.execute(
            """INSERT OR IGNORE INTO supplier_offers
               (public_id, organization_id, supplier_partner_id, material_id,
                legacy_product_id, price_minor, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_public_id(), organization_id, supplier["id"], material["id"], product["id"],
             _minor_units(product["price"]), product["notes"], product["updated_at"] or now, now),
        )

    cost_code_rows = [
        ("MAT", "材料费"), ("LAB", "人工费"), ("MCH", "机械费"),
        ("TRN", "运输费"), ("SUB", "分包费"), ("MGT", "管理费"), ("OTH", "其他费用"),
    ]
    for code, name in cost_code_rows:
        conn.execute(
            """INSERT OR IGNORE INTO cost_codes
               (public_id, organization_id, cost_code, name, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (_public_id(), organization_id, code, name, now, now),
        )


def switch_procurement_to_v3_relations(conn):
    """Add authoritative V3 supplier/material relations to procurement records."""
    now = _now()
    _execute_script(conn, """
        CREATE TABLE IF NOT EXISTS supplier_profiles (
            partner_id INTEGER PRIMARY KEY,
            supplier_category TEXT,
            price_level TEXT,
            delivery_rating TEXT,
            quality_rating TEXT,
            export_capability TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (partner_id) REFERENCES business_partners(id) ON DELETE CASCADE
        );
    """)
    for row in conn.execute("SELECT * FROM suppliers ORDER BY id").fetchall():
        partner = conn.execute(
            "SELECT id FROM business_partners WHERE legacy_supplier_id=?", (row["id"],)
        ).fetchone()
        conn.execute(
            """INSERT OR REPLACE INTO supplier_profiles
               (partner_id, supplier_category, price_level, delivery_rating,
                quality_rating, export_capability, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (partner["id"], row["category"], row["price_level"], row["delivery"],
             row["quality"], row["export"], now),
        )

    order_columns = {row[1] for row in conn.execute("PRAGMA table_info(purchase_orders)")}
    order_additions = [
        ("public_id", "TEXT"),
        ("organization_id", "INTEGER REFERENCES organizations(id)"),
        ("supplier_partner_id", "INTEGER REFERENCES business_partners(id)"),
    ]
    for name, definition in order_additions:
        if name not in order_columns:
            conn.execute(f"ALTER TABLE purchase_orders ADD COLUMN {name} {definition}")

    item_columns = {row[1] for row in conn.execute("PRAGMA table_info(purchase_order_items)")}
    item_additions = [
        ("public_id", "TEXT"),
        ("material_id", "INTEGER REFERENCES materials(id)"),
        ("supplier_offer_id", "INTEGER REFERENCES supplier_offers(id)"),
    ]
    for name, definition in item_additions:
        if name not in item_columns:
            conn.execute(f"ALTER TABLE purchase_order_items ADD COLUMN {name} {definition}")

    organization_id = conn.execute(
        "SELECT id FROM organizations WHERE organization_code='DEFAULT'"
    ).fetchone()["id"]
    for order in conn.execute("SELECT id, supplier_id, public_id FROM purchase_orders").fetchall():
        partner = None
        if order["supplier_id"]:
            partner = conn.execute(
                "SELECT id FROM business_partners WHERE legacy_supplier_id=?", (order["supplier_id"],)
            ).fetchone()
        conn.execute(
            """UPDATE purchase_orders SET public_id=COALESCE(public_id, ?),
               organization_id=?, supplier_partner_id=COALESCE(supplier_partner_id, ?)
               WHERE id=?""",
            (_public_id(), organization_id, partner["id"] if partner else None, order["id"]),
        )
    for item in conn.execute("SELECT id, product_id, public_id FROM purchase_order_items").fetchall():
        offer = None
        if item["product_id"]:
            offer = conn.execute(
                "SELECT id, material_id FROM supplier_offers WHERE legacy_product_id=?",
                (item["product_id"],),
            ).fetchone()
        conn.execute(
            """UPDATE purchase_order_items SET public_id=COALESCE(public_id, ?),
               supplier_offer_id=COALESCE(supplier_offer_id, ?),
               material_id=COALESCE(material_id, ?) WHERE id=?""",
            (_public_id(), offer["id"] if offer else None,
             offer["material_id"] if offer else None, item["id"]),
        )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_purchase_orders_public_id ON purchase_orders(public_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_purchase_orders_partner ON purchase_orders(supplier_partner_id)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_purchase_items_public_id ON purchase_order_items(public_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_purchase_items_material ON purchase_order_items(material_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_purchase_items_offer ON purchase_order_items(supplier_offer_id)")


MIGRATIONS = [
    (100, "V3 组织、客商、材料、项目与成本科目基础结构", create_v3_core),
    (110, "V3 历史主数据与项目数据迁移", migrate_v3_master_data),
    (120, "V3 供应商档案与采购客商材料关联", switch_procurement_to_v3_relations),
]
