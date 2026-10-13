"""User checklist and follow-up state; never manufacture financial facts."""


def migrate(conn):
    for statement in (
        '''CREATE TABLE daily_self_checks (
            day TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('worklogs','purchases')),
            status TEXT NOT NULL CHECK(status IN ('pending','done','none','later')),
            basis TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
            PRIMARY KEY(day,kind))''',
        '''CREATE TABLE operating_attention_states (
            issue_key TEXT PRIMARY KEY, project_id INTEGER REFERENCES projects(id),
            title TEXT NOT NULL, basis TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('processing','ignored','snoozed')),
            until_day TEXT, updated_at TEXT NOT NULL)''',
        '''CREATE TABLE operating_check_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, identity TEXT NOT NULL,
            action TEXT NOT NULL, created_at TEXT NOT NULL)''',
        '''CREATE TABLE assistant_entry_drafts (
            token TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE, state TEXT NOT NULL
              CHECK(state IN ('draft','applied','discarded')),
            records_json TEXT NOT NULL, fingerprint TEXT NOT NULL, review_json TEXT NOT NULL,
            result_json TEXT, created_at TEXT NOT NULL, expires_at TEXT NOT NULL)''',
    ):
        conn.execute(statement)


MIGRATIONS = [(640, '每日自检、主动经营跟进与助手确认草稿', migrate)]
