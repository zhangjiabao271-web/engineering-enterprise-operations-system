def add_ai_message_feedback(conn):
    """Store answer feedback separately from business facts."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ai_message_feedback (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               message_id INTEGER NOT NULL UNIQUE,
               rating TEXT NOT NULL CHECK(rating IN ('useful', 'not_useful')),
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL,
               FOREIGN KEY(message_id) REFERENCES ai_messages(id)
                   ON DELETE CASCADE
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_ai_message_feedback_rating
           ON ai_message_feedback(rating, updated_at DESC)"""
    )


MIGRATIONS = [
    (450, "AI经营助手回答反馈闭环", add_ai_message_feedback),
]
