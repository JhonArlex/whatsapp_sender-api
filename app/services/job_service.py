"""Servicio de jobs: creación, worker asíncrono, cancelación."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from app.clients.evolution import EvolutionClient
from app.config import settings
from app.db import execute, query

# Almacén en memoria para jobs activos y sus eventos de cancelación
_active_jobs: dict[str, threading.Event] = {}
_jobs_lock = threading.Lock()

# WebSocket connections por job
_ws_connections: dict[str, list] = {}
_ws_lock = threading.Lock()


def register_ws(job_id: str, ws):
    with _ws_lock:
        if job_id not in _ws_connections:
            _ws_connections[job_id] = []
        _ws_connections[job_id].append(ws)


def unregister_ws(job_id: str, ws):
    with _ws_lock:
        if job_id in _ws_connections:
            _ws_connections[job_id] = [w for w in _ws_connections[job_id] if w != ws]


async def _broadcast(job_id: str, event: dict):
    """Envía evento a todos los WebSocket conectados a este job."""
    with _ws_lock:
        connections = list(_ws_connections.get(job_id, []))
    for ws in connections:
        try:
            await ws.send_json(event)
        except Exception:
            pass


def create_job(user_id: str, name: str, groups: list[dict], messages: list[dict]) -> dict:
    """Crea un job en BD y arranca el worker."""
    job_id = str(uuid.uuid4())

    execute(
        "INSERT INTO jobs (id, user_id, name, status, total_groups) VALUES (%s, %s, %s, 'pending', %s)",
        (job_id, user_id, name, len(groups)),
    )

    for g in groups:
        execute(
            "INSERT INTO job_groups (job_id, remote_jid, push_name, instance_name, instance_token, evolution_base_url) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (job_id, g["remote_jid"], g.get("push_name", ""), g.get("instance_name", ""),
             g.get("instance_token", ""), g.get("evolution_base_url", "")),
        )

    for i, msg in enumerate(messages):
        execute(
            "INSERT INTO job_messages (job_id, msg_type, content, media_base64, media_mimetype, file_name, sort_order) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (job_id, msg.get("msg_type", "text"), msg.get("content", ""),
             msg.get("media_base64", ""), msg.get("media_mimetype", ""),
             msg.get("file_name", ""), i),
        )

    cancel_event = threading.Event()
    with _jobs_lock:
        _active_jobs[job_id] = cancel_event

    t = threading.Thread(target=_run_job_worker, args=(job_id, user_id, cancel_event), daemon=True)
    t.start()

    return {"id": job_id, "status": "pending", "total_groups": len(groups)}


def cancel_job(job_id: str, user_id: str) -> bool:
    """Cancela un job en ejecución."""
    with _jobs_lock:
        event = _active_jobs.get(job_id)
        if event:
            event.set()
            execute(
                "UPDATE jobs SET status = 'cancelled', finished_at = NOW() WHERE id = %s AND user_id = %s",
                (job_id, user_id),
            )
            return True
    return False


def _run_job_worker(job_id: str, user_id: str, cancel_event: threading.Event):
    """Worker que ejecuta un job en segundo plano."""
    try:
        execute("UPDATE jobs SET status = 'running', started_at = NOW() WHERE id = %s", (job_id,))

        groups = query(
            "SELECT id, remote_jid, push_name, instance_name, instance_token, evolution_base_url "
            "FROM job_groups WHERE job_id = %s ORDER BY id",
            (job_id,),
        )
        messages = query(
            "SELECT msg_type, content, media_base64, media_mimetype, file_name FROM job_messages WHERE job_id = %s ORDER BY sort_order",
            (job_id,),
        )

        total = len(groups)
        processed = 0
        success = 0
        fails = 0

        for g in groups:
            if cancel_event.is_set():
                execute(
                    "UPDATE jobs SET status = 'cancelled', processed_groups = %s, success_count = %s, fail_count = %s, finished_at = NOW() WHERE id = %s",
                    (processed, success, fails, job_id),
                )
                return

            remote_jid = g["remote_jid"]
            push_name = g["push_name"]
            instance_name = g["instance_name"]
            instance_token = g["instance_token"]
            evo_base_url = g["evolution_base_url"]
            group_id = str(g["id"])

            execute(
                "UPDATE job_groups SET status = 'sending' WHERE id = %s",
                (group_id,),
            )

            # Enviar mensajes
            ok = True
            error_detail = ""
            for msg in messages:
                if cancel_event.is_set():
                    return

                client = EvolutionClient(evo_base_url, origin=settings.evolution_request_origin)
                loop = asyncio.new_event_loop()
                try:
                    if msg["msg_type"] == "text":
                        resp = loop.run_until_complete(
                            client.send_text(instance_name, instance_token, remote_jid, msg["content"])
                        )
                    elif msg["msg_type"] in ("image", "video", "document"):
                        resp = loop.run_until_complete(
                            client.send_media(
                                instance_name, instance_token, remote_jid,
                                caption=msg["content"],
                                media_base64=msg["media_base64"],
                                mimetype=msg["media_mimetype"],
                                filename=msg["file_name"],
                            )
                        )
                    else:
                        resp = loop.run_until_complete(
                            client.send_text(instance_name, instance_token, remote_jid, msg["content"])
                        )

                    # Verificar respuesta
                    status_code = resp.get("status", 200)
                    if status_code not in (200, 201):
                        ok = False
                        error_detail = f"HTTP {status_code}: {resp.get('message', str(resp)[:200])}"
                except Exception as e:
                    ok = False
                    error_detail = str(e)[:500]
                finally:
                    loop.close()

                if not ok:
                    break

                # Delay entre mensajes del mismo grupo
                time.sleep(2)

            # Actualizar estado del grupo
            group_status = "ok" if ok else "error"
            execute(
                "UPDATE job_groups SET status = %s, detail = %s, sent_at = NOW() WHERE id = %s",
                (group_status, error_detail or None, group_id),
            )

            # Guardar en historial
            execute(
                "INSERT INTO message_history (job_id, job_group_id, user_id, remote_jid, push_name, instance_name, "
                "msg_type, content, status, error_detail, sent_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())",
                (job_id, group_id, user_id, remote_jid, push_name, instance_name,
                 "mixed", json.dumps([m["content"] for m in messages] if messages else ""),
                 group_status, error_detail or None),
            )

            processed += 1
            if ok:
                success += 1
            else:
                fails += 1

            # Actualizar progreso
            execute(
                "UPDATE jobs SET processed_groups = %s, success_count = %s, fail_count = %s WHERE id = %s",
                (processed, success, fails, job_id),
            )

            # Broadcast via WebSocket
            loop2 = asyncio.new_event_loop()
            try:
                loop2.run_until_complete(_broadcast(job_id, {
                    "type": "progress",
                    "data": {
                        "processed": processed,
                        "total": total,
                        "success": success,
                        "fails": fails,
                        "current_group": push_name,
                        "remote_jid": remote_jid,
                    },
                }))
                loop2.run_until_complete(_broadcast(job_id, {
                    "type": "group_update",
                    "data": {
                        "remote_jid": remote_jid,
                        "push_name": push_name,
                        "status": group_status,
                        "detail": error_detail if not ok else None,
                    },
                }))
            finally:
                loop2.close()

            # Delay entre grupos
            time.sleep(3)

        # Finalizar
        final_status = "completed" if fails == 0 else "completed_with_errors"
        execute(
            "UPDATE jobs SET status = %s, processed_groups = %s, success_count = %s, fail_count = %s, finished_at = NOW() WHERE id = %s",
            (final_status, processed, success, fails, job_id),
        )

        loop3 = asyncio.new_event_loop()
        try:
            loop3.run_until_complete(_broadcast(job_id, {
                "type": "completed",
                "data": {"status": final_status, "success": success, "fails": fails, "total": total},
            }))
        finally:
            loop3.close()

    except Exception as e:
        execute(
            "UPDATE jobs SET status = 'error', error_message = %s, finished_at = NOW() WHERE id = %s",
            (str(e)[:1000], job_id),
        )
    finally:
        with _jobs_lock:
            _active_jobs.pop(job_id, None)


def get_job(job_id: str, user_id: str) -> dict | None:
    """Devuelve detalle de un job."""
    rows = query(
        "SELECT id, name, status, total_groups, processed_groups, success_count, fail_count, "
        "error_message, started_at, finished_at, created_at FROM jobs WHERE id = %s AND user_id = %s",
        (job_id, user_id),
    )
    if not rows:
        return None
    r = rows[0]

    groups = query(
        "SELECT id, remote_jid, push_name, instance_name, status, detail, sent_at "
        "FROM job_groups WHERE job_id = %s ORDER BY id",
        (job_id,),
    )
    messages = query(
        "SELECT msg_type, content, file_name FROM job_messages WHERE job_id = %s ORDER BY sort_order",
        (job_id,),
    )

    return {
        "id": str(r["id"]),
        "name": r["name"],
        "status": r["status"],
        "total_groups": r["total_groups"],
        "processed_groups": r["processed_groups"],
        "success_count": r["success_count"],
        "fail_count": r["fail_count"],
        "error_message": r["error_message"],
        "started_at": r["started_at"].isoformat() if r.get("started_at") else None,
        "finished_at": r["finished_at"].isoformat() if r.get("finished_at") else None,
        "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        "groups": [
            {
                "id": str(g["id"]),
                "remote_jid": g["remote_jid"],
                "push_name": g["push_name"],
                "instance_name": g["instance_name"],
                "status": g["status"],
                "detail": g["detail"],
                "sent_at": g["sent_at"].isoformat() if g.get("sent_at") else None,
            }
            for g in groups
        ],
        "messages": [
            {
                "msg_type": m["msg_type"],
                "content": m["content"],
                "file_name": m["file_name"],
            }
            for m in messages
        ],
    }


def list_jobs(user_id: str, status_filter: str = "", page: int = 1, limit: int = 20) -> dict:
    """Lista jobs del usuario con paginación."""
    params: list[Any] = [user_id]
    sql_count = "SELECT COUNT(*) as total FROM jobs WHERE user_id = %s"
    sql = "SELECT id, name, status, total_groups, processed_groups, success_count, fail_count, error_message, started_at, finished_at, created_at FROM jobs WHERE user_id = %s"

    if status_filter:
        sql_count += " AND status = %s"
        sql += " AND status = %s"
        params.append(status_filter)

    sql += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
    offset = (page - 1) * limit
    params.append(limit)
    params.append(offset)

    count_params = params[:2] if status_filter else params[:1]
    count_params.append(status_filter) if status_filter else None
    # rebuild
    if status_filter:
        count_rows = query(f"SELECT COUNT(*) as total FROM jobs WHERE user_id = %s AND status = %s", (user_id, status_filter))
    else:
        count_rows = query(f"SELECT COUNT(*) as total FROM jobs WHERE user_id = %s", (user_id,))

    total = count_rows[0]["total"] if count_rows else 0

    rows = query(sql, tuple(p for p in [user_id, status_filter] if p) if status_filter else (user_id, limit, offset))
    # Re-hacer query correctamente
    if status_filter:
        rows = query(
            "SELECT id, name, status, total_groups, processed_groups, success_count, fail_count, "
            "error_message, started_at, finished_at, created_at FROM jobs "
            "WHERE user_id = %s AND status = %s ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (user_id, status_filter, limit, offset),
        )
    else:
        rows = query(
            "SELECT id, name, status, total_groups, processed_groups, success_count, fail_count, "
            "error_message, started_at, finished_at, created_at FROM jobs "
            "WHERE user_id = %s ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (user_id, limit, offset),
        )

    return {
        "jobs": [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "status": r["status"],
                "total_groups": r["total_groups"],
                "processed_groups": r["processed_groups"],
                "success_count": r["success_count"],
                "fail_count": r["fail_count"],
                "started_at": r["started_at"].isoformat() if r.get("started_at") else None,
                "finished_at": r["finished_at"].isoformat() if r.get("finished_at") else None,
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "limit": limit,
    }


def retry_failed_groups(job_id: str, user_id: str) -> dict:
    """Reintenta los grupos fallidos de un job."""
    job = get_job(job_id, user_id)
    if not job:
        return {"ok": False, "error": "Job no encontrado"}

    failed_groups = [g for g in job.get("groups", []) if g["status"] == "error"]
    if not failed_groups:
        return {"ok": False, "error": "No hay grupos fallidos para reintentar"}

    # Crear un nuevo job con los grupos fallidos
    new_groups = []
    for g in failed_groups:
        rows = query(
            "SELECT instance_name, instance_token, evolution_base_url FROM job_groups WHERE id = %s",
            (g["id"],),
        )
        if rows:
            r = rows[0]
            new_groups.append({
                "remote_jid": g["remote_jid"],
                "push_name": g["push_name"],
                "instance_name": r["instance_name"],
                "instance_token": r["instance_token"],
                "evolution_base_url": r["evolution_base_url"],
            })

    messages = query(
        "SELECT msg_type, content, media_base64, media_mimetype, file_name FROM job_messages WHERE job_id = %s ORDER BY sort_order",
        (job_id,),
    )

    new_job = create_job(
        user_id=user_id,
        name=f"Reintento: {job['name'] or job_id[:8]}",
        groups=new_groups,
        messages=[dict(m) for m in messages],
    )
    return {"ok": True, "job": new_job}
