import logging
from datetime import datetime

from db import get_connection, run_migrations
from services import master_data_service as master_service
from services import procurement_service
from services import project_service
from services import construction_service
from services import labor_service

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
            status TEXT NOT NULL DEFAULT '有效',
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
    site_specs = [
        ("澄湖药业", ["澄湖药业", "澄湖"]),
        ("屹峰药业", ["屹峰药业", "屹峰"]),
        ("朗润", ["朗润药业", "朗润"]),
    ]
    for index, (site_name, project_names) in enumerate(site_specs, 1):
        project = None
        for name in project_names:
            project = cursor.execute(
                "SELECT id FROM projects WHERE name=? ORDER BY id LIMIT 1", (name,)
            ).fetchone()
            if project:
                break
        if not project:
            code = f"SITE-{index:03d}"
            cursor.execute("""
                INSERT INTO projects (
                    project_code, name, status, notes, created_at, updated_at
                ) VALUES (?, ?, '进行中', '施工记录模块初始化创建', ?, ?)
            """, (code, site_name, now, now))
            project_id = cursor.lastrowid
        else:
            project_id = project["id"]
        cursor.execute("""
            INSERT OR IGNORE INTO construction_sites (
                project_id, site_name, is_active, notes, created_at
            ) VALUES (?, ?, 1, '默认厂区', ?)
        """, (project_id, site_name, now))
    cursor.execute("""
        INSERT INTO schema_migrations(version, description, applied_at)
        VALUES (2, '施工工程量、照片与验收模块', ?)
    """, (now,))


def add_supplier(data):
    return master_service.create_supplier(data)


def update_supplier(supplier_id, data):
    return master_service.update_supplier(supplier_id, data)


def delete_suppliers(supplier_ids):
    return master_service.deactivate_suppliers([int(value) for value in supplier_ids])


def delete_products(product_ids):
    return master_service.deactivate_supplier_offers([int(value) for value in product_ids])


def get_suppliers(keyword="", category=""):
    return master_service.list_suppliers(keyword=keyword, category=category)


def get_supplier_by_id(supplier_id):
    return master_service.get_supplier(supplier_id)


def add_product(data):
    return master_service.create_supplier_offer(data)


def update_product(product_id, data):
    return master_service.update_supplier_offer(product_id, data)



def get_products(supplier_id=None, keyword=""):
    return master_service.list_supplier_offers(supplier_id=supplier_id, keyword=keyword)


def get_product_by_id(product_id):
    return master_service.get_supplier_offer(product_id)


def get_products_for_compare(name_keyword, spec_keyword=""):
    """获取用于报价对比的产品"""
    rows = master_service.list_supplier_offers(keyword=name_keyword)
    if spec_keyword:
        rows = [row for row in rows if spec_keyword.lower() in (row.get("specification") or "").lower()]
    return sorted(
        rows,
        key=lambda row: (
            row.get("tax_inclusive_price_minor") or 0,
            row.get("price_minor") or 0,
        ),
    )


def get_products_with_suppliers(keyword=""):
    return master_service.list_supplier_offers(keyword=keyword)


def get_price_history(product_keywords=None, limit=20):
    """按产品关键词统计历史采购价（均价、最低、最高、最近）"""
    conn = get_connection()
    cursor = conn.cursor()
    product_keywords = product_keywords or []
    conditions = []
    params = []
    for kw in product_keywords:
        conditions.append("(poi.material_name_snapshot LIKE ? OR poi.specification_snapshot LIKE ?)")
        params.extend([f"%{kw}%", f"%{kw}%"])
    where_clause = " OR ".join(conditions) if conditions else "1=1"
    sql = f"""
        SELECT poi.material_name_snapshot as product_name,
               poi.specification_snapshot as specification,
               poi.unit_snapshot as unit,
               COUNT(po.id) as purchase_count,
               ROUND(AVG(poi.unit_price_cents) / 100.0, 2) as avg_price,
               ROUND(MIN(poi.unit_price_cents) / 100.0, 2) as min_price,
               ROUND(MAX(poi.unit_price_cents) / 100.0, 2) as max_price,
               MAX(po.purchase_date) as latest_date
        FROM purchase_orders po
        JOIN purchase_order_items poi ON poi.purchase_order_id = po.id
        WHERE po.status='有效' AND ({where_clause})
        GROUP BY poi.material_name_snapshot, poi.specification_snapshot, poi.unit_snapshot
        ORDER BY latest_date DESC
        LIMIT ?
    """
    params.append(limit)
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_similar_projects(keywords=None, limit=15):
    """按关键词查找类似项目的采购记录"""
    conn = get_connection()
    cursor = conn.cursor()
    keywords = keywords or []
    conditions = []
    params = []
    for kw in keywords:
        conditions.append("(pr.name LIKE ? OR po.notes LIKE ? OR poi.material_name_snapshot LIKE ? OR poi.specification_snapshot LIKE ?)")
        params.extend([f"%{kw}%", f"%{kw}%", f"%{kw}%", f"%{kw}%"])
    where_clause = " OR ".join(conditions) if conditions else "1=1"
    sql = f"""
        SELECT po.purchase_date, COALESCE(pr.name, '待归集') as construction_site,
               po.merchant_name_snapshot as supplier_name,
               poi.material_name_snapshot as product_name,
               poi.specification_snapshot as specification,
               poi.unit_snapshot as unit, poi.quantity,
               poi.unit_price_cents / 100.0 as unit_price,
               poi.line_amount_cents / 100.0 as total_price,
               po.notes
        FROM purchase_orders po
        JOIN purchase_order_items poi ON poi.purchase_order_id=po.id
        LEFT JOIN projects pr ON po.project_id=pr.id
        WHERE po.status='有效' AND ({where_clause})
        ORDER BY po.purchase_date DESC
        LIMIT ?
    """
    params.append(limit)
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_recommended_suppliers(category="", limit=10):
    """按产品范围推荐供应商，质量优、价格低、交期快优先"""
    quality_rank = {"优": 1, "良": 2, "中": 3, "差": 4}
    price_rank = {"低": 1, "中低": 2, "中": 3, "中高": 4, "高": 5}
    delivery_rank = {"快": 1, "较快": 2, "一般": 3, "较慢": 4, "慢": 5}
    rows = master_service.list_suppliers(category=category)
    return sorted(rows, key=lambda row: (
        quality_rank.get(row.get("quality"), 9),
        price_rank.get(row.get("price_level"), 9),
        delivery_rank.get(row.get("delivery"), 9),
    ))[:limit]


# ==================== 工人与工天记录 ====================

def add_worker(data):
    return labor_service.add_worker(data)


def update_worker(worker_id, data):
    return labor_service.update_worker(worker_id, data)


def delete_workers(worker_ids):
    return labor_service.delete_workers(worker_ids)


def get_workers(keyword="", active_only=False):
    return labor_service.get_workers(keyword, active_only)


def get_worker_by_id(worker_id):
    return labor_service.get_worker_by_id(worker_id)


def add_work_log(data):
    return labor_service.add_work_log(data)


def add_work_logs_batch(entries):
    return labor_service.add_work_logs_batch(entries)


def update_work_log(log_id, data):
    return labor_service.update_work_log(log_id, data)


def delete_work_logs(log_ids):
    return labor_service.delete_work_logs(log_ids)


def set_work_logs_overtime(log_ids, is_overtime):
    return labor_service.set_work_logs_overtime(log_ids, is_overtime)


def get_work_log_by_id(log_id):
    return labor_service.get_work_log_by_id(log_id)


def suggest_project_for_site(site_text):
    return labor_service.suggest_project_for_site(site_text)


def get_work_logs(month="", keyword=""):
    return labor_service.get_work_logs(month, keyword)


def get_work_months():
    return labor_service.get_work_months()


def get_work_dashboard(month):
    return labor_service.get_work_dashboard(month)
# ==================== V2 项目与统一采购 ====================

def get_projects(active_only=False):
    return project_service.list_projects(active_only=active_only)


def add_project(data):
    return project_service.create_project(data)


def _next_purchase_no(conn, purchase_type, purchase_date):
    prefix = "LS" if purchase_type == "零星采购" else "CG"
    date_part = purchase_date.replace("-", "")
    base = f"{prefix}-{date_part}-"
    row = conn.execute(
        "SELECT order_no FROM purchase_orders WHERE order_no LIKE ? ORDER BY order_no DESC LIMIT 1",
        (base + "%",)
    ).fetchone()
    sequence = int(row["order_no"].split("-")[-1]) + 1 if row else 1
    return f"{base}{sequence:03d}"


def add_purchase_order(header, item):
    return procurement_service.add_purchase_order(header, item)


def get_purchase_orders(month="", purchase_type="", project_id=None, keyword="", unassigned_only=False):
    return procurement_service.list_purchase_orders(month, purchase_type, project_id, keyword, unassigned_only)


def assign_purchase_project(order_ids, project_id):
    if not order_ids:
        return
    conn = get_connection()
    placeholders = ",".join("?" * len(order_ids))
    conn.execute(
        f"UPDATE purchase_orders SET project_id=?, updated_at=? WHERE id IN ({placeholders})",
        (project_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), *order_ids)
    )
    conn.commit()
    conn.close()


def update_purchase_order_status(order_ids, payment_method, payment_status, invoice_status):
    if not order_ids:
        return
    conn = get_connection()
    placeholders = ",".join("?" * len(order_ids))
    conn.execute(f"""
        UPDATE purchase_orders
        SET payment_method=?, payment_status=?, invoice_status=?, updated_at=?
        WHERE id IN ({placeholders}) AND status='有效'
    """, (
        payment_method, payment_status, invoice_status,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"), *order_ids
    ))
    conn.commit()
    conn.close()


def void_purchase_orders(order_ids):
    if not order_ids:
        return
    conn = get_connection()
    placeholders = ",".join("?" * len(order_ids))
    conn.execute(
        f"UPDATE purchase_orders SET status='作废', updated_at=? WHERE id IN ({placeholders})",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), *order_ids)
    )
    conn.commit()
    conn.close()


def get_purchase_months():
    conn = get_connection()
    rows = conn.execute("""
        SELECT DISTINCT substr(purchase_date, 1, 7) AS month
        FROM purchase_orders WHERE status='有效'
        ORDER BY month DESC
    """).fetchall()
    conn.close()
    return [row["month"] for row in rows if row["month"]]


def get_purchase_dashboard(month, project_id=None):
    conn = get_connection()
    where = "po.status='有效' AND substr(po.purchase_date, 1, 7)=?"
    params = [month]
    if project_id:
        where += " AND po.project_id=?"
        params.append(project_id)
    summary = conn.execute(f"""
        SELECT
            COALESCE(SUM(po.total_amount_cents), 0) AS total_cents,
            COALESCE(SUM(CASE WHEN po.purchase_type='正式采购' THEN po.total_amount_cents ELSE 0 END), 0) AS formal_cents,
            COALESCE(SUM(CASE WHEN po.purchase_type='零星采购' THEN po.total_amount_cents ELSE 0 END), 0) AS petty_cents,
            COUNT(DISTINCT po.merchant_name_snapshot) AS merchant_count,
            COALESCE(SUM(CASE WHEN po.project_id IS NULL THEN po.total_amount_cents ELSE 0 END), 0) AS unassigned_cents,
            COALESCE(SUM(CASE WHEN po.invoice_status IN ('无发票', '未确认') THEN po.total_amount_cents ELSE 0 END), 0) AS no_invoice_cents,
            COALESCE(SUM(CASE WHEN po.payment_method='员工垫付' AND po.payment_status<>'已付款' THEN po.total_amount_cents ELSE 0 END), 0) AS reimbursement_cents,
            COUNT(*) AS order_count
        FROM purchase_orders po WHERE {where}
    """, params).fetchone()
    by_project = conn.execute(f"""
        SELECT COALESCE(pr.name, '待归集') AS label,
               SUM(po.total_amount_cents) AS amount_cents,
               COUNT(*) AS order_count
        FROM purchase_orders po LEFT JOIN projects pr ON po.project_id=pr.id
        WHERE {where}
        GROUP BY COALESCE(pr.name, '待归集')
        ORDER BY amount_cents DESC LIMIT 8
    """, params).fetchall()
    by_merchant = conn.execute(f"""
        SELECT po.merchant_name_snapshot AS label,
               SUM(po.total_amount_cents) AS amount_cents,
               COUNT(*) AS order_count
        FROM purchase_orders po WHERE {where}
        GROUP BY po.merchant_name_snapshot
        ORDER BY amount_cents DESC LIMIT 8
    """, params).fetchall()
    year, mon = map(int, month.split("-"))
    prev_month = f"{year - 1}-12" if mon == 1 else f"{year}-{mon - 1:02d}"
    prev_params = [prev_month] + ([project_id] if project_id else [])
    previous_cents = conn.execute(
        f"SELECT COALESCE(SUM(po.total_amount_cents), 0) FROM purchase_orders po WHERE {where}",
        prev_params
    ).fetchone()[0]
    conn.close()
    result = dict(summary)
    result["previous_cents"] = previous_cents
    return {
        "summary": result,
        "by_project": [dict(row) for row in by_project],
        "by_merchant": [dict(row) for row in by_merchant],
    }


# ==================== 施工工程量与验收 ====================

def get_construction_sites(active_only=True):
    return construction_service.get_construction_sites(active_only)


def get_construction_work_areas(project_id=None):
    return construction_service.get_construction_work_areas(project_id)


def get_purchase_order(order_id):
    return procurement_service.get_purchase_order(order_id)


def update_purchase_order(order_id, header, item):
    """修改采购业务内容，保留采购单号、历史迁移标识和审计关系。"""
    return procurement_service.update_purchase_order(order_id, header, item)


def add_construction_record(data):
    return construction_service.add_construction_record(data)


def update_construction_record(record_id, data):
    return construction_service.update_construction_record(record_id, data)


def get_construction_record(record_id):
    return construction_service.get_construction_record(record_id)


def get_construction_records(month="", project_id=None, inspection_status="", keyword=""):
    return construction_service.get_construction_records(month, project_id, inspection_status, keyword)


def update_construction_inspection(record_id, data):
    return construction_service.update_construction_inspection(record_id, data)


def void_construction_records(record_ids):
    return construction_service.void_construction_records(record_ids)


def add_construction_photo(record_id, file_path, original_name, photo_type="施工现场", notes=""):
    return construction_service.add_construction_photo(record_id, file_path, original_name, photo_type, notes)


def get_construction_photos(record_id):
    return construction_service.get_construction_photos(record_id)


def delete_construction_photo(photo_id):
    return construction_service.delete_construction_photo(photo_id)


def get_construction_dashboard(month, project_id=None):
    return construction_service.get_construction_dashboard(month, project_id)
