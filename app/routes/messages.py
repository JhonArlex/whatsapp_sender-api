"""Routes para historial de mensajes."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import get_current_user
from app.db import execute, query

router = APIRouter(prefix="/api/v1/messages", tags=["messages"])


@router.get("")
def list_messages(
    job_id: str = Query("", description="Filtrar por job"),
    status_filter: str = Query("", description="Filtrar por estado: sent/failed"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    params: list = [str(user["id"])]
    sql = "SELECT id, job_id, remote_jid, push_name, instance_name, msg_type, content, status, error_detail, evolution_message_id, sent_at FROM message_history WHERE user_id = %s"

    if job_id:
        sql += " AND job_id = %s"
        params.append(job_id)
    if status_filter:
        sql += " AND status = %s"
        params.append(status_filter)

    offset = (page - 1) * limit
    count_sql = sql.replace("SELECT id, job_id, remote_jid", "SELECT COUNT(*) as total")
    total_rows = query(count_sql, tuple(params))
    total = total_rows[0]["total"] if total_rows else 0

    sql += " ORDER BY sent_at DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])

    rows = query(sql, tuple(params))
    return {
        "messages": [
            {
                "id": str(r["id"]),
                "job_id": str(r["job_id"]) if r.get("job_id") else None,
                "remote_jid": r["remote_jid"],
                "push_name": r["push_name"],
                "instance_name": r["instance_name"],
                "msg_type": r["msg_type"],
                "content": r["content"],
                "status": r["status"],
                "error_detail": r["error_detail"],
                "sent_at": r["sent_at"].isoformat() if r.get("sent_at") else None,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "limit": limit,
    }


@router.post("/{message_id}/resend")
def resend_message(message_id: str, user: dict = Depends(get_current_user)):
    rows = query(
        "SELECT mh.*, jg.instance_name, jg.instance_token, jg.evolution_base_url, jg.remote_jid, jg.push_name "
        "FROM message_history mh "
        "LEFT JOIN job_groups jg ON jg.id = mh.job_group_id "
        "WHERE mh.id = %s AND mh.user_id = %s",
        (message_id, str(user["id"])),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Mensaje no encontrado")

    r = rows[0]
    from app.clients.evolution import EvolutionClient
    import asyncio

    client = EvolutionClient(r.get("evolution_base_url", ""))
    instance_name = r.get("instance_name", "")
    instance_token = r.get("instance_token", "")
    remote_jid = r.get("remote_jid", "")
    content = r.get("content", "")

    try:
        loop = asyncio.new_event_loop()
        resp = loop.run_until_complete(
            client.send_text(instance_name, instance_token, remote_jid, content)
        )
        loop.close()
        status_code = resp.get("status", 200) if isinstance(resp, dict) else 200
        ok = status_code in (200, 201)

        execute(
            "UPDATE message_history SET status = %s, error_detail = %s, sent_at = NOW() WHERE id = %s",
            ("sent" if ok else "failed", None if ok else str(resp)[:500], message_id),
        )
        return {"ok": ok, "detail": None if ok else str(resp)[:500]}
    except Exception as e:
        execute(
            "UPDATE message_history SET status = 'failed', error_detail = %s WHERE id = %s",
            (str(e), message_id),
        )
        raise HTTPException(status_code=500, detail=str(e))
