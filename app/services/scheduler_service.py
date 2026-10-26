"""Servicio de programación de Jobs (scheduler).

Un thread daemon revisa cada 30 s las programaciones activas y dispara un job
nuevo cuando llega su ``next_run``. Toda la aritmética de fechas se hace en la
zona horaria configurada (``TIMEZONE``, por defecto America/Bogota) y se guarda
en UTC en PostgreSQL.

Tipos soportados:
  - once      → una fecha/hora concreta (se desactiva al ejecutarse)
  - daily     → todos los días a una hora
  - weekly    → días de la semana a una hora
  - interval  → cada N minutos
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import settings
from app.db import execute, query

logger = logging.getLogger(__name__)

_scheduler_thread: threading.Thread | None = None
_scheduler_stop = threading.Event()

DAY_MAP = {"lun": 0, "mar": 1, "mie": 2, "jue": 3, "vie": 4, "sab": 5, "dom": 6}
_DAY_NAMES = {v: k for k, v in DAY_MAP.items()}


def _local_tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.timezone or "America/Bogota")
    except ZoneInfoNotFoundError:  # pragma: no cover
        logger.warning("Zona horaria %s inválida — usando UTC", settings.timezone)
        return ZoneInfo("UTC")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Any, *, end_of_day: bool = False) -> datetime | None:
    """Convierte date/datetime/str a datetime UTC (naive = hora local)."""
    if value is None or value == "":
        return None
    tz = _local_tz()
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, dt_time.max if end_of_day else dt_time.min)
    else:
        raw = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            try:
                dt = datetime.combine(date.fromisoformat(raw[:10]), dt_time.min)
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


def _parse_hhmm(run_time: Any) -> tuple[int, int] | None:
    if run_time is None:
        return None
    if isinstance(run_time, dt_time):
        return run_time.hour, run_time.minute
    try:
        parts = str(run_time).strip().split(":")
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None


def _normalize_days(days: Any) -> list[str]:
    if not days:
        return []
    if isinstance(days, str):
        days = [days]
    out: list[str] = []
    for d in days:
        key = str(d).strip().lower()
        if key in DAY_MAP:
            out.append(key)
    return out


# ── CRUD ────────────────────────────────────────────────────────────────


def list_schedules(user_id: str, include_inactive: bool = True) -> list[dict]:
    sql = (
        "SELECT js.*, j.name AS job_name, j.status AS job_status, j.deleted_at AS job_deleted_at "
        "FROM job_schedules js JOIN jobs j ON j.id = js.job_id "
        "WHERE js.user_id = %s"
    )
    if not include_inactive:
        sql += " AND js.is_active = true"
    sql += " ORDER BY js.is_active DESC, js.next_run ASC NULLS LAST, js.created_at DESC"
    return [_row_to_dict(r) for r in query(sql, (user_id,))]


def get_schedule(schedule_id: str, user_id: str) -> dict | None:
    rows = query(
        "SELECT js.*, j.name AS job_name, j.status AS job_status, j.deleted_at AS job_deleted_at "
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
    """Crea una programación y calcula el próximo disparo.

    Lanza ``ValueError`` con un mensaje claro si los datos no permiten
    programar (job inexistente/archivado, grupos sin token, fecha vencida...).
    """
    from app.services.job_service import get_job

    job = get_job(job_id, user_id)
    if not job:
        raise ValueError("Job no encontrado")
    if job.get("is_deleted"):
        raise ValueError("El job está archivado — restáuralo antes de programarlo")

    groups = query(
        "SELECT id, instance_token, evolution_base_url FROM job_groups WHERE job_id = %s",
        (job_id,),
    )
    if not groups:
        raise ValueError("El job no tiene grupos asociados")
    missing_token = [g for g in groups if not g.get("instance_token")]
    if missing_token:
        raise ValueError(
            "Algunos grupos no tienen token de instancia. "
            "Vuelve a seleccionar los grupos desde la sincronización de WhatsApp."
        )
    missing_url = [g for g in groups if not g.get("evolution_base_url")]
    if missing_url:
        raise ValueError(
            "Algunos grupos no tienen URL base de Evolution. "
            "Vuelve a crear el job con grupos sincronizados."
        )

    schedule_type = (schedule_type or "once").strip().lower()
    if schedule_type not in ("once", "daily", "weekly", "interval"):
        raise ValueError("Tipo de programación inválido (once | daily | weekly | interval)")

    days = _normalize_days(days_of_week)
    if schedule_type == "weekly" and not days:
        raise ValueError("Selecciona al menos un día de la semana")
    if schedule_type in ("daily", "weekly") and _parse_hhmm(run_time) is None:
        raise ValueError("Indica una hora válida (HH:MM)")
    if schedule_type == "interval" and int(interval_minutes or 0) <= 0:
        raise ValueError("El intervalo debe ser mayor a 0 minutos")

    next_run = _calculate_next_run(
        schedule_type, run_date, run_time, days, int(interval_minutes or 0), start_date, end_date
    )
    if not next_run:
        if schedule_type == "once":
            raise ValueError("La fecha/hora indicada ya pasó — elige una futura")
        raise ValueError("No se pudo calcular la próxima ejecución con esos datos")

    schedule_id = str(uuid.uuid4())
    execute(
        """INSERT INTO job_schedules
           (id, job_id, user_id, schedule_type, run_date, run_time, days_of_week,
            interval_minutes, start_date, end_date, next_run, is_active)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true)""",
        (
            schedule_id,
            job_id,
            user_id,
            schedule_type,
            _as_utc(run_date),
            run_time or None,
            days,
            int(interval_minutes or 0),
            _as_utc(start_date) or _now_utc(),
            _as_utc(end_date, end_of_day=True),
            next_run,
        ),
    )

    return get_schedule(schedule_id, user_id) or {}


def update_schedule(schedule_id: str, user_id: str, **kwargs) -> dict | None:
    existing = query(
        "SELECT * FROM job_schedules WHERE id = %s AND user_id = %s",
        (schedule_id, user_id),
    )
    if not existing:
        return None

    current = existing[0]
    fields = {
        "schedule_type": kwargs.get("schedule_type", current["schedule_type"]),
        "run_date": kwargs.get("run_date", current.get("run_date")),
        "run_time": kwargs.get("run_time", current.get("run_time")),
        "days_of_week": _normalize_days(kwargs.get("days_of_week", current.get("days_of_week"))),
        "interval_minutes": int(kwargs.get("interval_minutes", current.get("interval_minutes") or 0)),
        "start_date": kwargs.get("start_date", current.get("start_date")),
        "end_date": kwargs.get("end_date", current.get("end_date")),
    }

    next_run = _calculate_next_run(
        fields["schedule_type"],
        fields["run_date"],
        fields["run_time"],
        fields["days_of_week"],
        fields["interval_minutes"],
        fields["start_date"],
        fields["end_date"],
    )

    execute(
        """UPDATE job_schedules SET schedule_type = %s, run_date = %s, run_time = %s,
           days_of_week = %s, interval_minutes = %s, start_date = %s, end_date = %s,
           next_run = %s, updated_at = NOW()
           WHERE id = %s AND user_id = %s""",
        (
            fields["schedule_type"],
            _as_utc(fields["run_date"]),
            fields["run_time"] or None,
            fields["days_of_week"],
            fields["interval_minutes"],
            _as_utc(fields["start_date"]) or _now_utc(),
            _as_utc(fields["end_date"], end_of_day=True),
            next_run,
            schedule_id,
            user_id,
        ),
    )
    return get_schedule(schedule_id, user_id)


def toggle_schedule(schedule_id: str, user_id: str, is_active: bool | None = None) -> dict | None:
    """Activa/pausa una programación. Al reactivar recalcula ``next_run``."""
    rows = query(
        "SELECT * FROM job_schedules WHERE id = %s AND user_id = %s",
        (schedule_id, user_id),
    )
    if not rows:
        return None

    current = rows[0]
    new_state = (not bool(current.get("is_active"))) if is_active is None else bool(is_active)

    next_run = current.get("next_run")
    if new_state:
        next_run = _calculate_next_run(
            current["schedule_type"],
            current.get("run_date"),
            current.get("run_time"),
            _normalize_days(current.get("days_of_week")),
            int(current.get("interval_minutes") or 0),
            current.get("start_date"),
            current.get("end_date"),
        )

    execute(
        "UPDATE job_schedules SET is_active = %s, next_run = %s, updated_at = NOW() "
        "WHERE id = %s AND user_id = %s",
        (new_state, next_run, schedule_id, user_id),
    )
    return get_schedule(schedule_id, user_id)


def delete_schedule(schedule_id: str, user_id: str) -> bool:
    rows = query(
        "SELECT id FROM job_schedules WHERE id = %s AND user_id = %s",
        (schedule_id, user_id),
    )
    if not rows:
        return False
    execute("DELETE FROM job_schedules WHERE id = %s AND user_id = %s", (schedule_id, user_id))
    return True


def list_schedule_history(user_id: str, limit: int = 50) -> list[dict]:
    """Historial de ejecuciones del scheduler (últimas N)."""
    rows = query(
        "SELECT sh.*, js.schedule_type, j.name AS job_name "
        "FROM schedule_history sh "
        "JOIN job_schedules js ON js.id = sh.schedule_id "
        "JOIN jobs j ON j.id = sh.job_id "
        "WHERE js.user_id = %s "
        "ORDER BY sh.ejecutado_en DESC LIMIT %s",
        (user_id, limit),
    )
    out = []
    for r in rows:
        out.append(
            {
                "id": str(r["id"]),
                "schedule_id": str(r["schedule_id"]) if r.get("schedule_id") else None,
                "job_id": str(r["job_id"]) if r.get("job_id") else None,
                "job_name": r.get("job_name") or "",
                "schedule_type": r.get("schedule_type") or "",
                "hora_programada": str(r.get("hora_programada") or ""),
                "ejecutado_en": r["ejecutado_en"].isoformat() if r.get("ejecutado_en") else None,
                "finalizado_en": r["finalizado_en"].isoformat() if r.get("finalizado_en") else None,
                "estado": r.get("estado"),
                "detalle": r.get("detalle"),
            }
        )
    return out


# ── Cálculo de próxima ejecución ───────────────────────────────────────


def _calculate_next_run(
    schedule_type: str,
    run_date: Any = None,
    run_time: Any = None,
    days_of_week: list[str] | None = None,
    interval_minutes: int = 0,
    start_date: Any = None,
    end_date: Any = None,
) -> datetime | None:
    """Devuelve el próximo disparo en UTC, o ``None`` si no hay futuro válido."""
    tz = _local_tz()
    now = _now_utc()
    now_local = now.astimezone(tz)

    start = _as_utc(start_date) or now
    end = _as_utc(end_date, end_of_day=True)

    def _valid(candidate_utc: datetime) -> datetime | None:
        if candidate_utc <= now:
            return None
        if candidate_utc < start:
            return None
        if end and candidate_utc > end:
            return None
        return candidate_utc

    if schedule_type == "once":
        run_dt = _as_utc(run_date)
        if run_dt is None:
            return None
        hhmm = _parse_hhmm(run_time)
        # Si run_date viene sin hora (medianoche) y hay run_time, aplicarlo
        if hhmm and run_dt.astimezone(tz).time() == dt_time(0, 0):
            run_dt = run_dt.astimezone(tz).replace(hour=hhmm[0], minute=hhmm[1]).astimezone(timezone.utc)
        return _valid(run_dt)

    if schedule_type == "daily":
        hhmm = _parse_hhmm(run_time)
        if hhmm is None:
            return None
        candidate = now_local.replace(hour=hhmm[0], minute=hhmm[1], second=0, microsecond=0)
        if candidate <= now_local:
            candidate += timedelta(days=1)
        result = _valid(candidate.astimezone(timezone.utc))
        return result

    if schedule_type == "weekly":
        hhmm = _parse_hhmm(run_time)
        days = _normalize_days(days_of_week)
        if hhmm is None or not days:
            return None
        wanted = {DAY_MAP[d] for d in days}
        for offset in range(0, 15):
            day = (now_local + timedelta(days=offset)).date()
            if day.weekday() not in wanted:
                continue
            candidate = datetime.combine(day, dt_time(hhmm[0], hhmm[1]), tzinfo=tz)
            if candidate.astimezone(timezone.utc) <= now:
                continue
            result = _valid(candidate.astimezone(timezone.utc))
            if result:
                return result
        return None

    if schedule_type == "interval":
        minutes = int(interval_minutes or 0)
        if minutes <= 0:
            return None
        base = _as_utc(start_date) or now
        candidate = base
        while candidate <= now:
            candidate += timedelta(minutes=minutes)
        return _valid(candidate)

    return None


# ── Scheduler daemon ───────────────────────────────────────────────────


def start_scheduler_daemon() -> None:
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    _scheduler_stop.clear()
    _scheduler_thread = threading.Thread(target=_scheduler_loop, name="job-scheduler", daemon=True)
    _scheduler_thread.start()
    logger.info("Job scheduler daemon started (tz=%s)", settings.timezone)


def stop_scheduler_daemon() -> None:
    _scheduler_stop.set()
    logger.info("Job scheduler daemon stopped")


def _scheduler_loop() -> None:
    """Cada 30 s busca programaciones vencidas y las dispara."""
    interval = max(10, int(getattr(settings, "scheduler_check_interval", 30) or 30))
    while not _scheduler_stop.is_set():
        try:
            _check_and_fire()
        except Exception:
            logger.exception("Error en scheduler loop")
        try:
            _refresh_schedule_history()
        except Exception:
            logger.exception("Error actualizando historial de programaciones")
        _scheduler_stop.wait(interval)


def _check_and_fire() -> None:
    now = _now_utc()
    due = query(
        "SELECT js.*, j.name AS job_name "
        "FROM job_schedules js JOIN jobs j ON j.id = js.job_id "
        "WHERE js.is_active = true AND js.next_run IS NOT NULL AND js.next_run <= %s "
        "AND j.deleted_at IS NULL "
        "ORDER BY js.next_run ASC LIMIT 20",
        (now,),
    )

    for s in due:
        try:
            _fire_schedule(s, now)
        except Exception:
            logger.exception("Error disparando programación %s", s.get("id"))


def _fire_schedule(schedule: dict, now: datetime) -> None:
    from app.services.job_service import create_job

    schedule_id = str(schedule["id"])
    job_id = str(schedule["job_id"])
    user_id = str(schedule["user_id"])

    # Evitar solapamiento: si el último job disparado sigue corriendo, esperar
    last_job_id = schedule.get("last_job_id")
    if last_job_id:
        running = query(
            "SELECT status FROM jobs WHERE id = %s", (str(last_job_id),)
        )
        if running and running[0]["status"] in ("pending", "running"):
            logger.info(
                "Programación %s omitida: el job anterior (%s) sigue en ejecución",
                schedule_id, str(last_job_id)[:8],
            )
            return

    groups = query(
        "SELECT remote_jid, push_name, instance_name, instance_token, evolution_base_url "
        "FROM job_groups WHERE job_id = %s ORDER BY id",
        (job_id,),
    )
    messages = query(
        "SELECT msg_type, content, media_base64, media_mimetype, file_name "
        "FROM job_messages WHERE job_id = %s ORDER BY sort_order",
        (job_id,),
    )
    if not groups or not messages:
        logger.warning("Programación %s sin grupos/mensajes — se desactiva", schedule_id)
        execute("UPDATE job_schedules SET is_active = false, updated_at = NOW() WHERE id = %s", (schedule_id,))
        return

    name = f"[Programado] {schedule.get('job_name') or job_id[:8]}"
    created = create_job(
        user_id=user_id,
        name=name,
        groups=[dict(g) for g in groups],
        messages=[dict(m) for m in messages],
    )
    new_job_id = created["id"]

    # Registrar ejecución en el historial de programaciones
    execute(
        """INSERT INTO schedule_history
           (id, schedule_id, user_id, hora_programada, ejecutado_en, job_id, estado, detalle)
           VALUES (%s, %s, %s, %s, %s, %s, 'ejecutando', %s)""",
        (
            str(uuid.uuid4()),
            schedule_id,
            user_id,
            now.astimezone(_local_tz()).strftime("%H:%M"),
            now,
            new_job_id,
            "Ejecución programada",
        ),
    )

    # Recalcular próximo disparo
    run_time = schedule.get("run_time")
    next_run = _calculate_next_run(
        schedule["schedule_type"],
        schedule.get("run_date"),
        run_time,
        _normalize_days(schedule.get("days_of_week")),
        int(schedule.get("interval_minutes") or 0),
        schedule.get("start_date"),
        schedule.get("end_date"),
    )

    if next_run:
        execute(
            "UPDATE job_schedules SET last_run = %s, last_job_id = %s, last_status = 'pending', "
            "next_run = %s, updated_at = NOW() WHERE id = %s",
            (now, new_job_id, next_run, schedule_id),
        )
    else:
        # Sin próxima ejecución (once vencido o fin de rango): desactivar
        execute(
            "UPDATE job_schedules SET last_run = %s, last_job_id = %s, last_status = 'pending', "
            "next_run = NULL, is_active = false, updated_at = NOW() WHERE id = %s",
            (now, new_job_id, schedule_id),
        )
        logger.info("Programación %s completada — desactivada (sin próxima ejecución)", schedule_id)

    logger.info("Programación %s disparó el job %s", schedule_id, new_job_id[:8])

    # Actualizar next_run si es recurrente
    if next_run:
        logger.info("  próxima ejecución: %s", next_run.isoformat())


def run_schedule_now(schedule_id: str, user_id: str) -> dict:
    """Ejecuta una programación inmediatamente, sin alterar ``next_run``."""
    sched = query(
        "SELECT js.*, j.name AS job_name FROM job_schedules js JOIN jobs j ON j.id = js.job_id "
        "WHERE js.id = %s AND js.user_id = %s",
        (schedule_id, user_id),
    )
    if not sched:
        return {"ok": False, "message": "Programación no encontrada"}

    s = sched[0]
    job_id = str(s["job_id"])

    groups = query(
        "SELECT remote_jid, push_name, instance_name, instance_token, evolution_base_url "
        "FROM job_groups WHERE job_id = %s ORDER BY id",
        (job_id,),
    )
    messages = query(
        "SELECT msg_type, content, media_base64, media_mimetype, file_name "
        "FROM job_messages WHERE job_id = %s ORDER BY sort_order",
        (job_id,),
    )
    if not groups or not messages:
        return {"ok": False, "message": "El job de esta programación no tiene grupos o mensajes"}

    from app.services.job_service import create_job

    now = _now_utc()
    created = create_job(
        user_id=user_id,
        name=f"[Manual] {s.get('job_name') or job_id[:8]}",
        groups=[dict(g) for g in groups],
        messages=[dict(m) for m in messages],
    )

    execute(
        """INSERT INTO schedule_history
           (id, schedule_id, user_id, hora_programada, ejecutado_en, job_id, estado, detalle)
           VALUES (%s, %s, %s, %s, %s, %s, 'ejecutando', 'Ejecución manual')""",
        (
            str(uuid.uuid4()),
            schedule_id,
            user_id,
            now.astimezone(_local_tz()).strftime("%H:%M"),
            now,
            created["id"],
        ),
    )
    execute(
        "UPDATE job_schedules SET last_run = %s, last_job_id = %s, last_status = 'pending', "
        "updated_at = NOW() WHERE id = %s",
        (now, created["id"], schedule_id),
    )
    return {"ok": True, "message": "Job ejecutado", "job": created}


def _refresh_schedule_history() -> None:
    """Cierra las filas de historial cuyos jobs ya terminaron."""
    execute(
        """UPDATE schedule_history sh
           SET estado = j.status,
               detalle = COALESCE(NULLIF(j.error_message, ''), sh.detalle),
               finalizado_en = COALESCE(j.finished_at, NOW())
           FROM jobs j
           WHERE j.id = sh.job_id
             AND sh.estado = 'ejecutando'
             AND j.status NOT IN ('pending', 'running')"""
    )
    execute(
        """UPDATE job_schedules js
           SET last_status = j.status, updated_at = NOW()
           FROM jobs j
           WHERE j.id = js.last_job_id
             AND js.last_status IN ('pending', 'running')
             AND j.status NOT IN ('pending', 'running')"""
    )


# ── Helpers ─────────────────────────────────────────────────────────────


def _row_to_dict(r: Any) -> dict:
    run_time = r.get("run_time")
    if isinstance(run_time, dt_time):
        run_time_str = run_time.strftime("%H:%M")
    elif run_time is not None:
        run_time_str = str(run_time)[:5]
    else:
        run_time_str = None

    return {
        "id": str(r["id"]),
        "job_id": str(r["job_id"]),
        "user_id": str(r["user_id"]),
        "schedule_type": r["schedule_type"],
        "run_date": r["run_date"].isoformat() if r.get("run_date") else None,
        "run_time": run_time_str,
        "days_of_week": _normalize_days(r.get("days_of_week")),
        "interval_minutes": r.get("interval_minutes") or 0,
        "start_date": r["start_date"].isoformat() if r.get("start_date") else None,
        "end_date": r["end_date"].isoformat() if r.get("end_date") else None,
        "next_run": r["next_run"].isoformat() if r.get("next_run") else None,
        "last_run": r["last_run"].isoformat() if r.get("last_run") else None,
        "last_job_id": str(r["last_job_id"]) if r.get("last_job_id") else None,
        "last_status": r.get("last_status"),
        "is_active": bool(r.get("is_active")),
        "job_name": r.get("job_name") or "",
        "job_status": r.get("job_status"),
        "job_deleted": r.get("job_deleted_at") is not None,
        "schedule_type_label": {
            "once": "Una vez",
            "daily": "Diario",
            "weekly": "Semanal",
            "interval": "Por intervalo",
        }.get(r["schedule_type"], r["schedule_type"]),
        "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        "updated_at": r["updated_at"].isoformat() if r.get("updated_at") else None,
    }
