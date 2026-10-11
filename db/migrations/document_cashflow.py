"""Versioned local evidence and analysis drafts, separate from business facts."""


def migrate(conn):
    statements = [
        """CREATE TABLE cashflow_documents (
            id INTEGER PRIMARY KEY, contract_id INTEGER REFERENCES contracts(id),
            project_id INTEGER REFERENCES projects(id), name TEXT NOT NULL,
            kind TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0),
            sha256 TEXT NOT NULL, fragments_json TEXT NOT NULL,
            source_path TEXT NOT NULL DEFAULT '', source_file_sha256 TEXT NOT NULL DEFAULT '',
            attachment_id INTEGER REFERENCES business_attachments(id),
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
            change_reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
            CHECK(contract_id IS NOT NULL OR project_id IS NOT NULL)
        )""",
        """CREATE UNIQUE INDEX cashflow_document_version ON cashflow_documents(
            COALESCE(contract_id,0),COALESCE(project_id,0),name,kind,version)""",
        """CREATE UNIQUE INDEX cashflow_document_current ON cashflow_documents(
            COALESCE(contract_id,0),COALESCE(project_id,0),name,kind) WHERE active=1""",
        """CREATE TABLE cashflow_analyses (
            id INTEGER PRIMARY KEY, contract_id INTEGER REFERENCES contracts(id),
            project_id INTEGER REFERENCES projects(id), fingerprint TEXT NOT NULL,
            engine_version TEXT NOT NULL, payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','confirmed','cancelled')),
            created_at TEXT NOT NULL, confirmed_at TEXT, confirmation_reason TEXT NOT NULL DEFAULT '',
            CHECK(contract_id IS NOT NULL OR project_id IS NOT NULL)
        )""",
        """CREATE TABLE cashflow_plan_links (
            analysis_id INTEGER NOT NULL REFERENCES cashflow_analyses(id),
            row_key TEXT NOT NULL, plan_id INTEGER NOT NULL UNIQUE REFERENCES fund_plans(id),
            PRIMARY KEY(analysis_id,row_key)
        )""",
    ]
    for statement in statements:
        conn.execute(statement)


MIGRATIONS = [(630, '合同项目资料版本、现金流草稿与确认追溯', migrate)]
