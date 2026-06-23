"""Servicio de jobs: creación, worker asíncrono optimizado con paralelismo."""

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


def _send_to_single_group(group: dict, messages: list[dict], cancel_event: threading.Event) -> tuple[bool, str]:
    """Envía todos los mensajes a un solo grupo. Usa su propio event loop."""
    if cancel_event.is_set():
        return False, "cancelado"

    remote_jid = group["remote_jid"]
    instance_name = group["instance_name"]
    instance_token = group["instance_token"]
    evo_base_url = group["evolution_base_url"]

    client = EvolutionClient(evo_base_url, settings.evolution_api_key, origin=settings.evolution_request_origin)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        for msg in messages:
            if cancel_event.is_set():
                return False, "cancelado"

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

                status_code = resp.get("status", 200)
                if isinstance(status_code, str) and status_code == "PENDING":
                    continue
                elif status_code not in (200, 201):
                    return False, f"HTTP {status_code}: {resp.get('message', str(resp)[:200])}"
            except Exception as e:
                return False, str(e)[:500]

            # Pausa entre mensajes del mismo grupo para evitar rate-limit (WhatsApp ~5-8s recomendado)
            time.sleep(6)

        return True, ""
    finally:
        loop.close()


def _run_job_worker(job_id: str, user_id: str, cancel_event: threading.Event):
    """Worker que ejecuta un job en segundo plano con paralelismo."""
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

        # Procesar grupos secuencialmente para evitar rate-limit de WhatsApp
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=1) as executor:
            future_to_group = {}
            for g in groups:
                future = executor.submit(_send_to_single_group, g, messages, cancel_event)
                future_to_group[future] = g

            for future in as_completed(future_to_group):
                if cancel_event.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    execute(
                        "UPDATE jobs SET status = 'cancelled', processed_groups = %s, success_count = %s, fail_count = %s, finished_at = NOW() WHERE id = %s",
                        (processed, success, fails, job_id),
                    )
                    return

                g = future_to_group[future]
                group_id = str(g["id"])
                try:
                    ok, error_detail = future.result(timeout=180)
                except Exception as e:
                    ok = False
                    error_detail = str(e)[:500]

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
                    (job_id, group_id, user_id, g["remote_jid"], g["push_name"], g["instance_name"],
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
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(_broadcast(job_id, {
                        "type": "progress",
                        "data": {
                            "processed": processed,
                            "total": total,
                            "success": success,
                            "fails": fails,
                            "current_group": g["push_name"],
                            "remote_jid": g["remote_jid"],
                        },
                    }))
                    loop.run_until_complete(_broadcast(job_id, {
                        "type": "group_update",
                        "data": {
                            "remote_jid": g["remote_jid"],
                            "push_name": g["push_name"],
                            "status": group_status,
                            "detail": error_detail if not ok else None,
                        },
                    }))
                finally:
                    loop.close()

                # Pausa entre grupos para evitar rate-limit de WhatsApp
                time.sleep(8)

        # Finalizar
        final_status = "completed" if fails == 0 else "completed_with_errors"
        execute(
            "UPDATE jobs SET status = %s, processed_groups = %s, success_count = %s, fail_count = %s, finished_at = NOW() WHERE id = %s",
            (final_status, processed, success, fails, job_id),
        )

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_broadcast(job_id, {
                "type": "completed",
                "data": {"status": final_status, "success": success, "fails": fails, "total": total},
            }))
        finally:
            loop.close()

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


def list_jobs(user_id: str, page: int = 1, limit: int = 20, status_filter: str | None = None) -> dict:
    """Lista jobs con paginación."""
    offset = (page - 1) * limit

    if status_filter:
        count_rows = query(
            "SELECT COUNT(*) as total FROM jobs WHERE user_id = %s AND status = %s",
            (user_id, status_filter),
        )
        rows = query(
            "SELECT id, name, status, total_groups, processed_groups, success_count, fail_count, "
            "error_message, started_at, finished_at, created_at FROM jobs "
            "WHERE user_id = %s AND status = %s ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (user_id, status_filter, limit, offset),
        )
    else:
        count_rows = query(
            "SELECT COUNT(*) as total FROM jobs WHERE user_id = %s",
            (user_id,),
        )
        rows = query(
            "SELECT id, name, status, total_groups, processed_groups, success_count, fail_count, "
            "error_message, started_at, finished_at, created_at FROM jobs "
            "WHERE user_id = %s ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (user_id, limit, offset),
        )

    total = count_rows[0]["total"] if count_rows else 0

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
