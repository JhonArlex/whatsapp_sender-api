"""Servicio de programación de Jobs (scheduler)."""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timezone, timedelta
from typing import Any

from app.db import execute, query

logger = logging.getLogger(__name__)

_scheduler_thread: threading.Thread | None = None
_scheduler_stop = threading.Event()


# ── CRUD ────────────────────────────────────────────────────────────────


def list_schedules(user_id: str) -> list[dict]:
    rows = query(
        "SELECT js.*, j.name as job_name, j.status as job_status "
        "FROM job_schedules js JOIN jobs j ON j.id = js.job_id "
        "WHERE js.user_id = %s ORDER BY js.next_run ASC NULLS LAST",
        (user_id,),
    )
    return [_row_to_dict(r) for r in rows]


def get_schedule(schedule_id: str, user_id: str) -> dict | None:
    rows = query(
        "SELECT js.*, j.name as job_name, j.status as job_status "
        "FROM job_schedules js JOIN jobs j ON j.id = js.job_id "
        "WHERE js.id = %s AND js.user_id = %s",
        (schedule_id, user_id),
    )
    return _row_to_dict(rows[0]) if rows else None


def create_schedule(
    user_id: str,
    job_id: str,
    schedule_type: str = "once",
    run_date: str | None = None,
    run_time: str | None = None,
    days_of_week: list[str] | None = None,
    interval_minutes: int = 0,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Crea un schedule y calcula el próximo disparo."""
    from app.services.job_service import get_job

    job = get_job(job_id, user_id)
    if not job:
        raise ValueError("Job no encontrado")

    next_run = _calculate_next_run(schedule_type, run_date, run_time, days_of_week, interval_minutes, start_date)

    execute(
        """INSERT INTO job_schedules
           (job_id, user_id, schedule_type, run_date, run_time, days_of_week,
            interval_minutes, start_date, end_date, next_run)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (job_id, user_id, schedule_type,
         run_date, run_time,
         days_of_week or [],
         interval_minutes,
         start_date or datetime.now(timezone.utc).isoformat(),
         end_date,
         next_run),
    )

    rows = query(
        "SELECT * FROM job_schedules WHERE job_id = %s AND user_id = %s ORDER BY created_at DESC LIMIT 1",
        (job_id, user_id),
    )
    return _row_to_dict(rows[0]) if rows else {}


def update_schedule(schedule_id: str, user_id: str, **kwargs) -> dict | None:
    existing = query(
        "SELECT * FROM job_schedules WHERE id = %s AND user_id = %s",
        (schedule_id, user_id),
    )
    if not existing:
        return None

    updates = []
    params: list[Any] = []
    for field in ["schedule_type", "run_date", "run_time", "days_of_week",
                   "interval_minutes", "start_date", "end_date", "is_active"]:
        if field in kwargs:
            updates.append(f"{field} = %s")
            params.append(kwargs[field])

    if updates:
        updates.append("updated_at = %s")
        params.append(datetime.now(timezone.utc))
        params.append(schedule_id)
        execute(
            f"UPDATE job_schedules SET {', '.join(updates)} WHERE id = %s",
            tuple(params),
        )

    # Recalcular next_run
    r = query("SELECT * FROM job_schedules WHERE id = %s", (schedule_id,))[0]
    next_run = _calculate_next_run(
        r["schedule_type"], r.get("run_date"), r.get("run_time"),
        r.get("days_of_week"), r["interval_minutes"], r.get("start_date"),
    )
    if next_run:
        execute("UPDATE job_schedules SET next_run = %s WHERE id = %s", (next_run, schedule_id))

    rows = query("SELECT * FROM job_schedules WHERE id = %s", (schedule_id,))
    return _row_to_dict(rows[0]) if rows else None


def delete_schedule(schedule_id: str, user_id: str) -> bool:
    execute("DELETE FROM job_schedules WHERE id = %s AND user_id = %s", (schedule_id, user_id))
    return True


# ── Cálculo de próxima ejecución ───────────────────────────────────────


def _calculate_next_run(
    schedule_type: str,
    run_date: str | None = None,
    run_time: str | None = None,
    days_of_week: list[str] | None = None,
    interval_minutes: int = 0,
    start_date: str | None = None,
) -> str | None:
    """Calcula cuándo debería ejecutarse el schedule."""
    now = datetime.now(timezone.utc)
    start = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc) if start_date else now

    if schedule_type == "once" and run_date:
        return run_date

    if schedule_type == "daily" and run_time:
        hour, minute = map(int, run_time.split(":"))
        candidate = start.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate.isoformat()

    if schedule_type == "weekly" and days_of_week and run_time:
        day_map = {"dom": 0, "lun": 1, "mar": 2, "mie": 3, "jue": 4, "vie": 5, "sab": 6}
        hour, minute = map(int, run_time.split(":"))
        base = start.replace(hour=hour, minute=minute, second=0, microsecond=0)
        for _ in range(14):
            day_name = list(day_map.keys())[list(day_map.values()).index(base.weekday())]
            if day_name in days_of_week and base > now:
                return base.isoformat()
            base += timedelta(days=1)
        return None

    if schedule_type == "interval" and interval_minutes > 0:
        next_time = start
        while next_time <= now:
            next_time += timedelta(minutes=interval_minutes)
        return next_time.isoformat()

    return None


# ── Scheduler Daemon ────────────────────────────────────────────────────


def start_scheduler_daemon():
    """Inicia el hilo que revisa schedules periódicamente."""
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    _scheduler_stop.clear()
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
    _scheduler_thread.start()
    logger.info("Job scheduler daemon started")


def stop_scheduler_daemon():
    _scheduler_stop.set()
    logger.info("Job scheduler daemon stopped")


def _scheduler_loop():
    """Loop principal: cada 30s revisa schedules que deban ejecutarse."""
    while not _scheduler_stop.is_set():
        try:
            _check_and_fire()
        except Exception:
            logger.exception("Error en scheduler loop")
        _scheduler_stop.wait(30)


def _check_and_fire():
    """Busca schedules cuyo next_run <= ahora y los ejecuta."""
    now = datetime.now(timezone.utc)
    due = query(
        "SELECT js.*, j.name as job_name "
        "FROM job_schedules js JOIN jobs j ON j.id = js.job_id "
        "WHERE js.is_active = true AND js.next_run <= %s",
        (now,),
    )

    for s in due:
        schedule_id = str(s["id"])
        job_id = str(s["job_id"])
        user_id = str(s["user_id"])

        logger.info(f"Firing scheduled job {s.get('job_name', job_id[:8])}")

        # Re-crear el job ejecutándolo
        from app.services.job_service import (
            _run_job_worker,
            create_job,
            _active_jobs,
            _jobs_lock,
        )
        import threading
        import uuid

        # Obtener grupos y mensajes del job original
        groups = query(
            "SELECT remote_jid, push_name, instance_name, instance_token, evolution_base_url "
            "FROM job_groups WHERE job_id = %s", (job_id,),
        )
        messages = query(
            "SELECT msg_type, content, media_base64, media_mimetype, file_name "
            "FROM job_messages WHERE job_id = %s ORDER BY sort_order", (job_id,),
        )

        new_job_id = str(uuid.uuid4())
        execute(
            "INSERT INTO jobs (id, user_id, name, status, total_groups) VALUES (%s, %s, %s, 'pending', %s)",
            (new_job_id, user_id, f"[Programado] {s.get('job_name', job_id[:8])}", len(groups)),
        )

        for g in groups:
            execute(
                "INSERT INTO job_groups (job_id, remote_jid, push_name, instance_name, instance_token, evolution_base_url) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (new_job_id, g["remote_jid"], g.get("push_name", ""), g.get("instance_name", ""),
                 g.get("instance_token", ""), g.get("evolution_base_url", "")),
            )

        for i, m in enumerate(messages):
            execute(
                "INSERT INTO job_messages (job_id, msg_type, content, media_base64, media_mimetype, file_name, sort_order) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (new_job_id, m["msg_type"], m.get("content", ""),
                 m.get("media_base64", ""), m.get("media_mimetype", ""),
                 m.get("file_name", ""), i),
            )

        cancel_event = threading.Event()
        with _jobs_lock:
            _active_jobs[new_job_id] = cancel_event

        t = threading.Thread(target=_run_job_worker, args=(new_job_id, user_id, cancel_event), daemon=True)
        t.start()

        # Actualizar schedule
        execute("UPDATE job_schedules SET last_run = %s WHERE id = %s", (now, schedule_id))

        # Recalcular próximo disparo
        r = query("SELECT * FROM job_schedules WHERE id = %s", (schedule_id,))[0]
        next_run = _calculate_next_run(
            r["schedule_type"], r.get("run_date"), r.get("run_time"),
            r.get("days_of_week"), r["interval_minutes"], r.get("start_date"),
        )
        if next_run:
            execute("UPDATE job_schedules SET next_run = %s WHERE id = %s", (next_run, schedule_id))
        else:
            # Si no hay próxima ejecución, desactivar
            execute("UPDATE job_schedules SET is_active = false WHERE id = %s", (schedule_id,))


# ── Helpers ─────────────────────────────────────────────────────────────


def _row_to_dict(r: Any) -> dict:
    return {
        "id": str(r["id"]),
        "job_id": str(r["job_id"]),
        "user_id": str(r["user_id"]),
        "schedule_type": r["schedule_type"],
        "run_date": r["run_date"].isoformat() if r.get("run_date") else None,
        "run_time": str(r["run_time"]) if r.get("run_time") else None,
        "days_of_week": r.get("days_of_week") or [],
        "interval_minutes": r["interval_minutes"] or 0,
        "start_date": r["start_date"].isoformat() if r.get("start_date") else None,
        "end_date": r["end_date"].isoformat() if r.get("end_date") else None,
        "next_run": r["next_run"].isoformat() if r.get("next_run") else None,
        "last_run": r["last_run"].isoformat() if r.get("last_run") else None,
        "is_active": r["is_active"],
        "job_name": r.get("job_name", ""),
        "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        "updated_at": r["updated_at"].isoformat() if r.get("updated_at") else None,
    }
