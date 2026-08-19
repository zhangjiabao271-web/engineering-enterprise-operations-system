from services._common import now as _now
import json
from datetime import datetime
from uuid import uuid4

from db.connection import get_connection


def _load_json(value):
    try:
        data = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _dump_json(value):
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _conversation_dict(row):
    if not row:
        return None
    result = dict(row)
    result["context"] = _load_json(result.pop("context_json", "{}"))
    return result


def _message_dict(row):
    result = dict(row)
    result["metadata"] = _load_json(result.pop("metadata_json", "{}"))
    return result


def create_conversation(
    title="新对话",
    project_id=None,
    context=None,
    db_path=None,
):
    now = _now()
    clean_title = str(title or "新对话").strip() or "新对话"
    context = dict(context or {})
    if project_id:
        context["project_id"] = int(project_id)
    else:
        context.pop("project_id", None)
    conn = get_connection(db_path)
    try:
        cursor = conn.execute(
            """INSERT INTO ai_conversations (
                   public_id, title, project_id, context_json,
                   status, created_at, updated_at
               ) VALUES (?, ?, ?, ?, 'active', ?, ?)""",
            (
                str(uuid4()),
                clean_title,
                int(project_id) if project_id else None,
                _dump_json(context),
                now,
                now,
            ),
        )
        conn.commit()
        return get_conversation(cursor.lastrowid, db_path=db_path)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_conversations(include_archived=False, limit=100, db_path=None):
    conn = get_connection(db_path)
    try:
        sql = """SELECT ac.*, p.name AS project_name,
                        (SELECT COUNT(*) FROM ai_messages am
                         WHERE am.conversation_id=ac.id) AS message_count
                 FROM ai_conversations ac
                 LEFT JOIN projects p ON p.id=ac.project_id"""
        params = []
        if not include_archived:
            sql += " WHERE ac.status='active'"
        sql += " ORDER BY ac.updated_at DESC, ac.id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        return [_conversation_dict(row) for row in conn.execute(sql, params)]
    finally:
        conn.close()


def get_conversation(conversation_id, db_path=None):
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            """SELECT ac.*, p.name AS project_name,
                      (SELECT COUNT(*) FROM ai_messages am
                       WHERE am.conversation_id=ac.id) AS message_count
               FROM ai_conversations ac
               LEFT JOIN projects p ON p.id=ac.project_id
               WHERE ac.id=?""",
            (int(conversation_id),),
        ).fetchone()
        if not row:
            raise ValueError("AI 对话不存在")
        return _conversation_dict(row)
    finally:
        conn.close()


def list_messages(conversation_id, limit=None, db_path=None):
    conn = get_connection(db_path)
    try:
        if limit:
            rows = conn.execute(
                """SELECT * FROM (
                       SELECT * FROM ai_messages
                       WHERE conversation_id=? ORDER BY id DESC LIMIT ?
                   ) ORDER BY id""",
                (int(conversation_id), max(1, int(limit))),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM ai_messages
                   WHERE conversation_id=? ORDER BY id""",
                (int(conversation_id),),
            ).fetchall()
        return [_message_dict(row) for row in rows]
    finally:
        conn.close()


def add_message(
    conversation_id,
    role,
    content,
    message_type="text",
    metadata=None,
    db_path=None,
):
    if role not in ("user", "assistant"):
        raise ValueError("AI 消息角色不正确")
    if message_type not in ("text", "answer", "confirmation", "error", "notice"):
        raise ValueError("AI 消息类型不正确")
    text = str(content or "").strip()
    if not text:
        raise ValueError("AI 消息内容不能为空")
    now = _now()
    conn = get_connection(db_path)
    try:
        cursor = conn.execute(
            """INSERT INTO ai_messages (
                   public_id, conversation_id, role, message_type,
                   content, metadata_json, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid4()),
                int(conversation_id),
                role,
                message_type,
                text,
                _dump_json(metadata),
                now,
            ),
        )
        conn.execute(
            "UPDATE ai_conversations SET updated_at=? WHERE id=?",
            (now, int(conversation_id)),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM ai_messages WHERE id=?", (cursor.lastrowid,)
        ).fetchone()
        return _message_dict(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_title(conversation_id, title, db_path=None):
    clean_title = str(title or "").strip()
    if not clean_title:
        return get_conversation(conversation_id, db_path=db_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE ai_conversations SET title=?, updated_at=? WHERE id=?",
            (clean_title[:40], _now(), int(conversation_id)),
        )
        conn.commit()
    finally:
        conn.close()
    return get_conversation(conversation_id, db_path=db_path)


def update_context(conversation_id, updates, project_id=None, db_path=None):
    conversation = get_conversation(conversation_id, db_path=db_path)
    context = dict(conversation.get("context") or {})
    updates = dict(updates or {})
    has_project_update = "project_id" in updates
    inferred_project_id = updates.get("project_id")
    for key, value in updates.items():
        if value is None:
            context.pop(key, None)
        else:
            context[key] = value
    if project_id is not None:
        project_value = int(project_id) if project_id else None
    elif has_project_update:
        project_value = int(inferred_project_id) if inferred_project_id else None
    else:
        project_value = conversation.get("project_id")
    if project_value:
        context["project_id"] = project_value
    else:
        context.pop("project_id", None)

    conn = get_connection(db_path)
    try:
        conn.execute(
            """UPDATE ai_conversations
               SET project_id=?, context_json=?, updated_at=? WHERE id=?""",
            (
                project_value,
                _dump_json(context),
                _now(),
                int(conversation_id),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_conversation(conversation_id, db_path=db_path)


def replace_context(conversation_id, context=None, project_id=None, db_path=None):
    clean_context = dict(context or {})
    project_value = int(project_id) if project_id else None
    if project_value:
        clean_context["project_id"] = project_value
    else:
        clean_context.pop("project_id", None)
    conn = get_connection(db_path)
    try:
        conn.execute(
            """UPDATE ai_conversations
               SET project_id=?, context_json=?, updated_at=? WHERE id=?""",
            (
                project_value,
                _dump_json(clean_context),
                _now(),
                int(conversation_id),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_conversation(conversation_id, db_path=db_path)


def archive_conversation(conversation_id, db_path=None):
    conn = get_connection(db_path)
    try:
        conn.execute(
            """UPDATE ai_conversations
               SET status='archived', updated_at=? WHERE id=?""",
            (_now(), int(conversation_id)),
        )
        conn.commit()
    finally:
        conn.close()
