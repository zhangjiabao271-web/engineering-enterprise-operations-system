def add_ai_conversations(conn):
    """Persist read-only AI conversations separately from business facts."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ai_conversations (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               public_id TEXT NOT NULL UNIQUE,
               title TEXT NOT NULL DEFAULT '新对话',
               project_id INTEGER,
               context_json TEXT NOT NULL DEFAULT '{}',
               status TEXT NOT NULL DEFAULT 'active'
                   CHECK(status IN ('active', 'archived')),
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL,
               FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE RESTRICT
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_ai_conversations_recent
           ON ai_conversations(status, updated_at DESC, id DESC)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ai_messages (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               public_id TEXT NOT NULL UNIQUE,
               conversation_id INTEGER NOT NULL,
               role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
               message_type TEXT NOT NULL DEFAULT 'text'
                   CHECK(message_type IN (
                       'text', 'answer', 'confirmation', 'error', 'notice'
                   )),
               content TEXT NOT NULL,
               metadata_json TEXT NOT NULL DEFAULT '{}',
               created_at TEXT NOT NULL,
               FOREIGN KEY(conversation_id) REFERENCES ai_conversations(id)
                   ON DELETE CASCADE
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_ai_messages_conversation
           ON ai_messages(conversation_id, id)"""
    )


MIGRATIONS = [
    (230, "AI经营助手连续会话、消息与可见上下文", add_ai_conversations),
]
