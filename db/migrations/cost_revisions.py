def create_cost_revisions(conn):
    conn.execute("""CREATE TABLE cost_entry_revisions (
        id INTEGER PRIMARY KEY,
        cost_entry_id INTEGER NOT NULL REFERENCES cost_entries(id),
        previous_values_json TEXT NOT NULL,
        updated_values_json TEXT NOT NULL,
        changed_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX idx_cost_revisions_entry ON cost_entry_revisions(cost_entry_id, id)")


MIGRATIONS = [(470, "成本信息修改历史与金额纠错", create_cost_revisions)]
