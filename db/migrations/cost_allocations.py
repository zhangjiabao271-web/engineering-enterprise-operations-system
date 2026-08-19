from datetime import datetime
from uuid import uuid4


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def add_cost_allocation_lines(conn):
    """Separate a cost document from its project accounting allocations."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(cost_entries)")}
    if "vehicle_no" not in columns:
        conn.execute("ALTER TABLE cost_entries ADD COLUMN vehicle_no TEXT")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cost_allocation_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            cost_entry_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
            allocation_method TEXT NOT NULL
                CHECK(allocation_method IN ('direct', 'equal', 'manual')),
            allocation_version INTEGER NOT NULL CHECK(allocation_version > 0),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'void')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            voided_at TEXT,
            FOREIGN KEY (cost_entry_id) REFERENCES cost_entries(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (project_id) REFERENCES projects(id)
                ON DELETE RESTRICT,
            UNIQUE (cost_entry_id, allocation_version, project_id)
        );

        CREATE INDEX IF NOT EXISTS idx_cost_allocation_lines_cost
        ON cost_allocation_lines(cost_entry_id, status, allocation_version);

        CREATE INDEX IF NOT EXISTS idx_cost_allocation_lines_project
        ON cost_allocation_lines(project_id, status, cost_entry_id);
        """
    )

    # Existing direct project costs remain readable without allocation lines.
    # New and reallocated costs use versioned lines so their source document is
    # recorded once while project accounting remains independently traceable.
    now = _now()
    conn.execute(
        """UPDATE cost_entries
           SET allocation_status=CASE
                 WHEN project_id IS NULL THEN 'unassigned' ELSE 'assigned' END,
               updated_at=COALESCE(updated_at, ?)
           WHERE status='active'""",
        (now,),
    )


MIGRATIONS = [
    (240, "车辆燃油费与多项目成本分摊", add_cost_allocation_lines),
]
