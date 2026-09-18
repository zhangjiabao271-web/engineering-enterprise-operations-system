"""初始建库：历史基础表（suppliers/products/purchases/workers/work_logs）、
V2 经营基础结构与施工模块结构，随后交给编号迁移继续演进。

由 database.py 兼容层迁入：该层其余委托函数已无调用方，随本次收缩删除。
"""

import logging
from datetime import datetime

from db import get_connection, run_migrations

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT,
            contact TEXT,
            price_level TEXT,
            delivery TEXT,
            quality TEXT,
            export TEXT,
            notes TEXT,
            created_at TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            specification TEXT,
            unit TEXT,
            price REAL,
            notes TEXT,
            updated_at TEXT,
            FOREIGN KEY (supplier_id) REFERENCES suppliers(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity REAL,
            unit_price REAL,
            total_price REAL,
            construction_site TEXT,
            purchase_date TEXT,
            notes TEXT,
            created_at TEXT,
            FOREIGN KEY (supplier_id) REFERENCES suppliers(id) ON DELETE CASCADE,
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS workers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            trade TEXT,
            phone TEXT,
            daily_rate REAL DEFAULT 0,
            status TEXT DEFAULT '在职',
            notes TEXT,
            created_at TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS work_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            worker_id INTEGER NOT NULL,
            work_date TEXT NOT NULL,
            construction_site TEXT NOT NULL,
            work_type TEXT,
            work_days REAL DEFAULT 1,
            is_overtime INTEGER NOT NULL DEFAULT 0
                CHECK(is_overtime IN (0, 1)),
            daily_rate REAL DEFAULT 0,
            amount REAL DEFAULT 0,
            notes TEXT,
            created_at TEXT,
            FOREIGN KEY (worker_id) REFERENCES workers(id) ON DELETE RESTRICT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_work_logs_date ON work_logs(work_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_work_logs_worker ON work_logs(worker_id)")
    _init_business_schema(conn)
    conn.commit()
    conn.close()
    run_migrations()


def _init_business_schema(conn):
    """V2 经营系统基础结构及幂等迁移。旧表保留，仅作为历史兼容。"""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            customer_name TEXT,
            address TEXT,
            manager TEXT,
            status TEXT NOT NULL DEFAULT '进行中',
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS purchase_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_no TEXT NOT NULL UNIQUE,
            purchase_type TEXT NOT NULL CHECK(purchase_type IN ('正式采购', '零星采购')),
            project_id INTEGER,
            supplier_id INTEGER,
            merchant_name_snapshot TEXT NOT NULL,
            purchase_date TEXT NOT NULL,
            payment_method TEXT NOT NULL DEFAULT '未记录',
            payment_status TEXT NOT NULL DEFAULT '未确认',
            invoice_status TEXT NOT NULL DEFAULT '未确认',
            purchaser TEXT,
            total_amount_cents INTEGER NOT NULL DEFAULT 0 CHECK(total_amount_cents >= 0),
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','void')),
            notes TEXT,
            legacy_purchase_id INTEGER UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
            FOREIGN KEY (supplier_id) REFERENCES suppliers(id) ON DELETE RESTRICT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS purchase_order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_order_id INTEGER NOT NULL,
            product_id INTEGER,
            material_name_snapshot TEXT NOT NULL,
            specification_snapshot TEXT,
            unit_snapshot TEXT,
            cost_category TEXT NOT NULL DEFAULT '材料费',
            quantity REAL NOT NULL DEFAULT 1 CHECK(quantity > 0),
            unit_price_cents INTEGER NOT NULL DEFAULT 0 CHECK(unit_price_cents >= 0),
            line_amount_cents INTEGER NOT NULL DEFAULT 0 CHECK(line_amount_cents >= 0),
            purpose TEXT,
            notes TEXT,
            FOREIGN KEY (purchase_order_id) REFERENCES purchase_orders(id) ON DELETE CASCADE,
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE SET NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_purchase_orders_date ON purchase_orders(purchase_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_purchase_orders_project ON purchase_orders(project_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_purchase_orders_type ON purchase_orders(purchase_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_purchase_items_order ON purchase_order_items(purchase_order_id)")
    _init_construction_schema(conn)

    # 修复历史孤立产品，不静默删除数据。
    orphan = cursor.execute("""
        SELECT COUNT(*) FROM products p
        LEFT JOIN suppliers s ON p.supplier_id=s.id
        WHERE s.id IS NULL
    """).fetchone()[0]
    if orphan:
        missing_ids = [row[0] for row in cursor.execute("""
            SELECT DISTINCT p.supplier_id FROM products p
            LEFT JOIN suppliers s ON p.supplier_id=s.id WHERE s.id IS NULL
        """).fetchall()]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for supplier_id in missing_ids:
            cursor.execute("""
                INSERT OR IGNORE INTO suppliers
                    (id, name, category, contact, price_level, delivery, quality, export, notes, created_at)
                VALUES (?, ?, '待归类', '', '未知', '未知', '未知', '否', ?, ?)
            """, (supplier_id, f"历史待认领供应商 #{supplier_id}", "系统迁移生成，请重新关联后再停用", now))

    applied = cursor.execute("SELECT 1 FROM schema_migrations WHERE version=1").fetchone()
    if not applied:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        site_rows = cursor.execute("""
            SELECT construction_site AS site FROM purchases WHERE trim(COALESCE(construction_site, '')) <> ''
            UNION
            SELECT construction_site AS site FROM work_logs WHERE trim(COALESCE(construction_site, '')) <> ''
            ORDER BY site
        """).fetchall()
        for index, row in enumerate(site_rows, 1):
            cursor.execute("""
                INSERT OR IGNORE INTO projects
                    (project_code, name, status, notes, created_at, updated_at)
                VALUES (?, ?, '进行中', '由历史工地名称自动迁移', ?, ?)
            """, (f"LEGACY-{index:04d}", row["site"], now, now))

        old_rows = cursor.execute("""
            SELECT pur.*, s.name AS supplier_name, p.name AS product_name,
                   p.specification, p.unit
            FROM purchases pur
            JOIN suppliers s ON pur.supplier_id=s.id
            JOIN products p ON pur.product_id=p.id
            ORDER BY pur.id
        """).fetchall()
        for row in old_rows:
            project = cursor.execute(
                "SELECT id FROM projects WHERE name=?",
                (row["construction_site"],)
            ).fetchone() if row["construction_site"] else None
            total_cents = round(float(row["total_price"] or 0) * 100)
            unit_cents = round(float(row["unit_price"] or 0) * 100)
            cursor.execute("""
                INSERT OR IGNORE INTO purchase_orders (
                    order_no, purchase_type, project_id, supplier_id,
                    merchant_name_snapshot, purchase_date, payment_method,
                    payment_status, invoice_status, total_amount_cents,
                    notes, legacy_purchase_id, created_at, updated_at
                ) VALUES (?, '正式采购', ?, ?, ?, ?, '未记录', '未确认',
                          '未确认', ?, ?, ?, ?, ?)
            """, (
                f"LEGACY-{row['id']:06d}", project["id"] if project else None,
                row["supplier_id"], row["supplier_name"], row["purchase_date"],
                total_cents, row["notes"], row["id"], row["created_at"] or now, now
            ))
            order = cursor.execute(
                "SELECT id FROM purchase_orders WHERE legacy_purchase_id=?", (row["id"],)
            ).fetchone()
            has_item = cursor.execute(
                "SELECT 1 FROM purchase_order_items WHERE purchase_order_id=?", (order["id"],)
            ).fetchone()
            if not has_item:
                cursor.execute("""
                    INSERT INTO purchase_order_items (
                        purchase_order_id, product_id, material_name_snapshot,
                        specification_snapshot, unit_snapshot, cost_category,
                        quantity, unit_price_cents, line_amount_cents, notes
                    ) VALUES (?, ?, ?, ?, ?, '材料费', ?, ?, ?, ?)
                """, (
                    order["id"], row["product_id"], row["product_name"],
                    row["specification"], row["unit"], float(row["quantity"] or 0) or 1,
                    unit_cents, total_cents, row["notes"]
                ))
        cursor.execute("""
            INSERT INTO schema_migrations(version, description, applied_at)
            VALUES (1, '项目基础与统一采购单迁移', ?)
        """, (now,))


def _init_construction_schema(conn):
    """施工工程量、现场照片和验收记录。"""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS construction_sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            site_name TEXT NOT NULL UNIQUE,
            address TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS construction_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER NOT NULL,
            record_date TEXT NOT NULL,
            work_area TEXT,
            work_item TEXT NOT NULL,
            quantity REAL NOT NULL CHECK(quantity >= 0),
            unit TEXT NOT NULL,
            team_name TEXT,
            description TEXT,
            inspection_status TEXT NOT NULL DEFAULT '待验收'
                CHECK(inspection_status IN ('待验收', '已验收', '需整改')),
            inspector TEXT,
            inspection_date TEXT,
            inspection_notes TEXT,
            record_status TEXT NOT NULL DEFAULT '有效'
                CHECK(record_status IN ('有效', '作废')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (site_id) REFERENCES construction_sites(id) ON DELETE RESTRICT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS construction_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER NOT NULL,
            photo_type TEXT NOT NULL DEFAULT '施工现场',
            file_path TEXT NOT NULL,
            original_name TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (record_id) REFERENCES construction_records(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_construction_records_date ON construction_records(record_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_construction_records_site ON construction_records(site_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_construction_records_status ON construction_records(inspection_status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_construction_photos_record ON construction_photos(record_id)")

    record_columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(construction_records)")
    }
    record_additions = [
        ("start_date", "TEXT"),
        ("end_date", "TEXT"),
        ("work_amount_cents", "INTEGER NOT NULL DEFAULT 0"),
        ("work_details", "TEXT"),
    ]
    for name, definition in record_additions:
        if name not in record_columns:
            cursor.execute(
                f"ALTER TABLE construction_records ADD COLUMN {name} {definition}"
            )
    cursor.execute("""
        UPDATE construction_records
        SET start_date=COALESCE(start_date, record_date),
            end_date=COALESCE(end_date, record_date)
        WHERE start_date IS NULL OR end_date IS NULL
    """)
    cursor.execute("""
        UPDATE construction_records
        SET work_details=TRIM(
            COALESCE(work_item, '')
            || CASE
                WHEN quantity IS NOT NULL AND COALESCE(unit, '')<>''
                THEN CHAR(10) || '工程量：' || quantity || ' ' || unit
                ELSE ''
            END
            || CASE
                WHEN TRIM(COALESCE(description, ''))<>''
                THEN CHAR(10) || description
                ELSE ''
            END
        )
        WHERE TRIM(COALESCE(work_details, ''))=''
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_construction_records_period "
        "ON construction_records(start_date, end_date)"
    )

    applied = cursor.execute("SELECT 1 FROM schema_migrations WHERE version=2").fetchone()
    if applied:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 历史版本曾在此为演示预置特定客户项目，已移除：
    # 全新部署不再自动创建任何项目，仅记录版本标记以保持迁移序号连续。
    cursor.execute("""
        INSERT INTO schema_migrations(version, description, applied_at)
        VALUES (2, '施工工程量、照片与验收模块', ?)
    """, (now,))
