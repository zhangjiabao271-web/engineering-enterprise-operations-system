"""Add a lightweight, auditable collection follow-up workbench."""


def add_collection_followups(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS collection_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            organization_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL UNIQUE,
            due_date TEXT,
            promised_date TEXT,
            next_followup_date TEXT,
            owner_name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN (
                    'pending', 'following', 'promised', 'paused', 'closed'
                )),
            overdue_reason TEXT NOT NULL DEFAULT '',
            next_action TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(organization_id) REFERENCES organizations(id)
                ON DELETE RESTRICT,
            FOREIGN KEY(project_id) REFERENCES projects(id)
                ON DELETE RESTRICT
        );

        CREATE INDEX IF NOT EXISTS idx_collection_cases_schedule
        ON collection_cases(status, next_followup_date, promised_date);

        CREATE INDEX IF NOT EXISTS idx_collection_cases_organization
        ON collection_cases(organization_id, status, updated_at DESC);

        CREATE TABLE IF NOT EXISTS collection_followup_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            case_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            followup_date TEXT NOT NULL,
            content TEXT NOT NULL,
            promised_date TEXT,
            next_followup_date TEXT,
            next_action TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(case_id) REFERENCES collection_cases(id)
                ON DELETE RESTRICT,
            FOREIGN KEY(project_id) REFERENCES projects(id)
                ON DELETE RESTRICT
        );

        CREATE INDEX IF NOT EXISTS idx_collection_logs_case
        ON collection_followup_logs(case_id, followup_date DESC, id DESC);

        CREATE INDEX IF NOT EXISTS idx_collection_logs_project
        ON collection_followup_logs(project_id, followup_date DESC, id DESC);
        """
    )


MIGRATIONS = [
    (460, "回款作战台与跟进历史", add_collection_followups),
]
