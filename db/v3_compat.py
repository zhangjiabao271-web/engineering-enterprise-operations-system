"""Temporary compatibility bridge while legacy UI pages are being replaced."""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _public_id():
    return str(uuid4())


def _organization_id(conn):
    row = conn.execute(
        "SELECT id FROM organizations WHERE organization_code='DEFAULT'"
    ).fetchone()
    if not row:
        raise RuntimeError("V3 default organization is missing; run migrations first")
    return row["id"]


def sync_supplier(conn, supplier_id, data):
    now = _now()
    organization_id = _organization_id(conn)
    partner = conn.execute(
        "SELECT id FROM business_partners WHERE legacy_supplier_id=?", (supplier_id,)
    ).fetchone()
    if partner:
        partner_id = partner["id"]
        conn.execute(
            """UPDATE business_partners
               SET legal_name=?, short_name=?, notes=?, status='active', updated_at=?
               WHERE id=?""",
            (data["name"], data["name"], data.get("notes", ""), now, partner_id),
        )
    else:
        cursor = conn.execute(
            """INSERT INTO business_partners
               (public_id, organization_id, partner_code, legal_name, short_name,
                status, legacy_supplier_id, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)""",
            (_public_id(), organization_id, f"SUP-{supplier_id:05d}", data["name"],
             data["name"], supplier_id, data.get("notes", ""), now, now),
        )
        partner_id = cursor.lastrowid
    conn.execute(
        "INSERT OR IGNORE INTO partner_roles(partner_id, role_code, created_at) VALUES (?, 'supplier', ?)",
        (partner_id, now),
    )
    contact = (data.get("contact") or "").strip()
    if contact:
        primary = conn.execute(
            "SELECT id FROM partner_contacts WHERE partner_id=? AND is_primary=1 ORDER BY id LIMIT 1",
            (partner_id,),
        ).fetchone()
        if primary:
            conn.execute(
                "UPDATE partner_contacts SET name=?, is_active=1, updated_at=? WHERE id=?",
                (contact, now, primary["id"]),
            )
        else:
            conn.execute(
                """INSERT INTO partner_contacts
                   (public_id, partner_id, name, is_primary, created_at, updated_at)
                   VALUES (?, ?, ?, 1, ?, ?)""",
                (_public_id(), partner_id, contact, now, now),
            )
    return partner_id


def sync_product(conn, product_id, data):
    now = _now()
    organization_id = _organization_id(conn)
    supplier = conn.execute("SELECT * FROM suppliers WHERE id=?", (data["supplier_id"],)).fetchone()
    if not supplier:
        raise ValueError("Supplier does not exist")
    partner_id = sync_supplier(conn, supplier["id"], dict(supplier))

    unit_name = (data.get("unit") or "").strip()
    unit_id = None
    if unit_name:
        unit = conn.execute("SELECT id FROM units_of_measure WHERE name=?", (unit_name,)).fetchone()
        if unit:
            unit_id = unit["id"]
        else:
            cursor = conn.execute(
                "INSERT INTO units_of_measure(unit_code, name) VALUES (?, ?)",
                (f"U-{uuid4().hex[:10].upper()}", unit_name),
            )
            unit_id = cursor.lastrowid

    name = data["name"].strip()
    specification = (data.get("specification") or "").strip()
    material = conn.execute(
        """SELECT id FROM materials
           WHERE organization_id=? AND name=? AND specification=?
             AND ((unit_id IS NULL AND ? IS NULL) OR unit_id=?)
           ORDER BY id LIMIT 1""",
        (organization_id, name, specification, unit_id, unit_id),
    ).fetchone()
    if material:
        material_id = material["id"]
    else:
        cursor = conn.execute(
            """INSERT INTO materials
               (public_id, organization_id, material_code, name, specification,
                unit_id, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (_public_id(), organization_id, f"MAT-AUTO-{uuid4().hex[:10].upper()}", name,
             specification, unit_id, now, now),
        )
        material_id = cursor.lastrowid

    price_minor = int(
        (Decimal(str(data.get("price") or 0)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    offer = conn.execute(
        "SELECT id FROM supplier_offers WHERE legacy_product_id=?", (product_id,)
    ).fetchone()
    if offer:
        conn.execute(
            """UPDATE supplier_offers SET supplier_partner_id=?, material_id=?,
               price_minor=?, notes=?, status='active', updated_at=? WHERE id=?""",
            (partner_id, material_id, price_minor, data.get("notes", ""), now, offer["id"]),
        )
    else:
        conn.execute(
            """INSERT INTO supplier_offers
               (public_id, organization_id, supplier_partner_id, material_id,
                legacy_product_id, price_minor, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_public_id(), organization_id, partner_id, material_id, product_id,
             price_minor, data.get("notes", ""), now, now),
        )


def project_relations(conn, customer_name, manager_name):
    now = _now()
    organization_id = _organization_id(conn)
    customer_id = None
    customer_name = (customer_name or "").strip()
    if customer_name:
        customer = conn.execute(
            "SELECT id FROM business_partners WHERE organization_id=? AND legal_name=?",
            (organization_id, customer_name),
        ).fetchone()
        if customer:
            customer_id = customer["id"]
        else:
            cursor = conn.execute(
                """INSERT INTO business_partners
                   (public_id, organization_id, partner_code, legal_name, short_name,
                    status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (_public_id(), organization_id, f"CUS-AUTO-{uuid4().hex[:8].upper()}",
                 customer_name, customer_name, now, now),
            )
            customer_id = cursor.lastrowid
        conn.execute(
            "INSERT OR IGNORE INTO partner_roles(partner_id, role_code, created_at) VALUES (?, 'customer', ?)",
            (customer_id, now),
        )

    manager_id = None
    manager_name = (manager_name or "").strip()
    if manager_name:
        manager = conn.execute(
            "SELECT id FROM employees WHERE organization_id=? AND name=?",
            (organization_id, manager_name),
        ).fetchone()
        if manager:
            manager_id = manager["id"]
        else:
            cursor = conn.execute(
                """INSERT INTO employees
                   (public_id, organization_id, employee_code, name, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (_public_id(), organization_id, f"EMP-AUTO-{uuid4().hex[:8].upper()}",
                 manager_name, now, now),
            )
            manager_id = cursor.lastrowid
    return organization_id, customer_id, manager_id


def create_root_wbs(conn, project_id):
    now = _now()
    conn.execute(
        """INSERT OR IGNORE INTO wbs_nodes
           (public_id, project_id, wbs_code, name, created_at, updated_at)
           VALUES (?, ?, 'ROOT', '项目总项', ?, ?)""",
        (_public_id(), project_id, now, now),
    )
