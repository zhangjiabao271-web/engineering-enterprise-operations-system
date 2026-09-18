def upgrade(conn):
    conn.execute('''CREATE TABLE cash_collection_closures (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id),
        original_income_minor INTEGER NOT NULL,
        received_minor INTEGER NOT NULL,
        reduction_minor INTEGER NOT NULL CHECK(reduction_minor>0),
        reason TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
        before_json TEXT NOT NULL, after_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','revoked')),
        created_at TEXT NOT NULL, revoked_at TEXT)''')
    conn.execute("CREATE UNIQUE INDEX one_active_cash_closure ON cash_collection_closures(project_id) WHERE status='active'")
    for action in ('INSERT', 'UPDATE', 'DELETE'):
        reference = 'NEW' if action == 'INSERT' else 'OLD'
        target_guard = ''
        if action == 'UPDATE':
            target_guard = " OR EXISTS (SELECT 1 FROM cash_collection_closures WHERE project_id=NEW.project_id AND status='active')"
        conn.execute(f'''CREATE TRIGGER guard_cash_closure_{action.lower()}
            BEFORE {action} ON settlements
            WHEN EXISTS (SELECT 1 FROM cash_collection_closures
                         WHERE project_id={reference}.project_id AND status='active'){target_guard}
            BEGIN SELECT RAISE(ABORT,'项目已有收款结清调整，请先撤销结清再修改收入'); END''')


MIGRATIONS = [(570, '零星工程收款结清调整及撤销历史', upgrade)]
