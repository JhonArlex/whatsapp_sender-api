"""Servicio de plantillas de mensajes."""

from __future__ import annotations

from datetime import datetime, timezone

from app.db import execute, query


def list_templates(user_id: str) -> list[dict]:
    """Lista las plantillas del usuario ordenadas por más reciente."""
    rows = query(
        "SELECT id, name, msg_type, content, media_url, media_type, created_at, updated_at "
        "FROM message_templates WHERE user_id = %s ORDER BY updated_at DESC",
        (user_id,),
    )
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "msg_type": r["msg_type"] or "text",
            "content": r["content"],
            "media_url": r.get("media_url") or "",
            "media_type": r.get("media_type") or "",
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            "updated_at": r["updated_at"].isoformat() if r.get("updated_at") else None,
        }
        for r in rows
    ]


def create_template(
    user_id: str,
    name: str,
    content: str,
    msg_type: str = "text",
    media_url: str = "",
    media_type: str = "",
) -> dict:
    execute(
        "INSERT INTO message_templates (user_id, name, msg_type, content, media_url, media_type) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (user_id, name, msg_type, content, media_url, media_type),
    )
    rows = query(
        "SELECT id, name, msg_type, content, media_url, media_type, created_at, updated_at "
        "FROM message_templates WHERE user_id = %s AND name = %s",
        (user_id, name),
    )
    r = rows[0]
    return {
        "id": str(r["id"]),
        "name": r["name"],
        "msg_type": r["msg_type"] or "text",
        "content": r["content"],
        "media_url": r.get("media_url") or "",
        "media_type": r.get("media_type") or "",
        "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        "updated_at": r["updated_at"].isoformat() if r.get("updated_at") else None,
    }


def update_template(
    template_id: str,
    user_id: str,
    name: str | None = None,
    content: str | None = None,
    msg_type: str | None = None,
    media_url: str | None = None,
    media_type: str | None = None,
) -> dict | None:
    """Actualiza una plantilla. Retorna la actualizada o None si no existe."""
    existing = query(
        "SELECT id FROM message_templates WHERE id = %s AND user_id = %s",
        (template_id, user_id),
    )
    if not existing:
        return None

    updates = []
    params = []
    if name is not None:
        updates.append("name = %s")
        params.append(name)
    if content is not None:
        updates.append("content = %s")
        params.append(content)
    if msg_type is not None:
        updates.append("msg_type = %s")
        params.append(msg_type)
    if media_url is not None:
        updates.append("media_url = %s")
        params.append(media_url)
    if media_type is not None:
        updates.append("media_type = %s")
        params.append(media_type)

    if updates:
        updates.append("updated_at = %s")
        params.append(datetime.now(timezone.utc))
        params.append(template_id)
        execute(
            f"UPDATE message_templates SET {', '.join(updates)} WHERE id = %s",
            tuple(params),
        )

    rows = query(
        "SELECT id, name, msg_type, content, media_url, media_type, created_at, updated_at "
        "FROM message_templates WHERE id = %s",
        (template_id,),
    )
    if not rows:
        return None
    r = rows[0]
    return {
        "id": str(r["id"]),
        "name": r["name"],
        "msg_type": r["msg_type"] or "text",
        "content": r["content"],
        "media_url": r.get("media_url") or "",
        "media_type": r.get("media_type") or "",
        "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        "updated_at": r["updated_at"].isoformat() if r.get("updated_at") else None,
    }


def delete_template(template_id: str, user_id: str) -> bool:
    execute(
        "DELETE FROM message_templates WHERE id = %s AND user_id = %s",
        (template_id, user_id),
    )
    return True
