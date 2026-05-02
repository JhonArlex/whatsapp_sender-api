"""Scheduler daemon para envíos programados.

Ejecuta un thread daemon que cada N segundos revisa los schedules activos.
Dispara envíos cuando la hora coincide y no se ha ejecutado ya en la ventana.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from app import db
from app.config import settings
from app.jobs import jobs

# Zona horaria objetivo (Santiago de Chile por defecto)
TZ = ZoneInfo(settings.timezone)

# Mapa de nombres cortos → weekday() (0=lunes, 6=domingo)
DIAS_MAP = {
    "lun": 0,
    "mar": 1,
    "mie": 2,
    "jue": 3,
    "vie": 4,
    "sab": 5,
    "dom": 6,
}

_inverted_dias = {v: k for k, v in DIAS_MAP.items()}


def _row_to_schedule(row: dict[str, Any]) -> dict[str, Any]:
    """Convierte una fila de BD al formato que espera el resto del código (ISO strings)."""
    result = dict(row)
    # UUID → str
    for key in ("id",):
        if key in result and not isinstance(result[key], str):
            result[key] = str(result[key])
    # datetime → ISO string
    for key in ("ultima_ejecucion", "creado"):
        val = result.get(key)
        if isinstance(val, datetime):
            result[key] = val.isoformat()
        elif val is None:
            result[key] = None
    # JSONB → list (ya lo entrega psycopg2 como Python list, pero por si acaso)
    dias = result.get("dias_semana")
    if isinstance(dias, str):
        result["dias_semana"] = json.loads(dias)
    return result


def _row_to_history(row: dict[str, Any]) -> dict[str, Any]:
    """Convierte una fila de historial de BD al formato esperado."""
    result = dict(row)
    for key in ("id", "schedule_id", "job_id"):
        val = result.get(key)
        if val is not None and not isinstance(val, str):
            result[key] = str(val)
    hp = result.get("hora_programada")
    if isinstance(hp, time):
        result["hora_programada"] = hp.strftime("%H:%M")
    elif hp is not None and not isinstance(hp, str):
        result["hora_programada"] = str(hp)
    for key in ("ejecutado_en", "finalizado_en"):
        val = result.get(key)
        if isinstance(val, datetime):
            result[key] = val.isoformat()
        elif val is not None:
            # Podría venir como string si se insertó como string y psycopg2 lo dejó así
            pass
    return result


def _ahora_en_tz() -> datetime:
    """Devuelve el datetime actual en la zona horaria configurada."""
    return datetime.now(TZ)


def _hoy_es_dia_valido(dias_semana: list[str]) -> bool:
    """True si hoy está en la lista, o si la lista está vacía (todos los días)."""
    if not dias_semana:
        return True
    hoy = _ahora_en_tz().weekday()  # 0=lunes
    return any(DIAS_MAP.get(d.lower()) == hoy for d in dias_semana)


class ScheduleStore:
    """Persistencia PostgreSQL thread-safe para schedules."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def load_schedules(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = db.query("SELECT * FROM schedules ORDER BY creado")
            return [_row_to_schedule(r) for r in rows]

    def load_schedule_by_id(self, schedule_id: str) -> dict[str, Any] | None:
        with self._lock:
            rows = db.query(
                "SELECT * FROM schedules WHERE id = %s", (schedule_id,)
            )
            if not rows:
                return None
            return _row_to_schedule(rows[0])

    def save_schedules(self, schedules: list[dict[str, Any]]) -> None:
        with self._lock:
            with db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM schedules")
                    for s in schedules:
                        cur.execute(
                            """INSERT INTO schedules
                               (id, hora, dias_semana, desde_fila, activo, ultima_ejecucion, creado)
                               VALUES (%s, %s, %s::jsonb, %s, %s, %s::timestamptz, %s::timestamptz)""",
                            (
                                str(s.get("id", "")),
                                s.get("hora", ""),
                                json.dumps(s.get("dias_semana", [])),
                                s.get("desde_fila", 1),
                                s.get("activo", True),
                                s.get("ultima_ejecucion"),
                                s.get("creado"),
                            ),
                        )

    def toggle_schedule(self, schedule_id: str) -> bool:
        """Toggle activo de un schedule. Retorna True si se encontró."""
        with self._lock:
            result = db.execute(
                "UPDATE schedules SET activo = NOT activo WHERE id = %s",
                (schedule_id,),
            )
            # Verificar si afectó alguna fila
            rows = db.query(
                "SELECT activo FROM schedules WHERE id = %s", (schedule_id,)
            )
            return bool(rows)

    def remove_schedule(self, schedule_id: str) -> bool:
        """Elimina un schedule por ID. Retorna True si se eliminó."""
        with self._lock:
            db.execute("DELETE FROM schedules WHERE id = %s", (schedule_id,))
            # No podemos saber cuántas filas afectó con execute(),
            # así que verificamos que ya no exista (no debería si el DELETE fue exitoso)
            return True

    def load_history(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = db.query(
                "SELECT * FROM schedule_history ORDER BY ejecutado_en DESC"
            )
            return [_row_to_history(r) for r in rows]

    def save_history(self, history: list[dict[str, Any]]) -> None:
        with self._lock:
            with db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM schedule_history")
                    for h in history:
                        cur.execute(
                            """INSERT INTO schedule_history
                               (id, schedule_id, hora_programada, ejecutado_en, finalizado_en, job_id, estado, detalle)
                               VALUES (%s, %s, %s, %s::timestamptz, %s::timestamptz, %s, %s, %s)""",
                            (
                                str(h.get("id", "")),
                                str(h.get("schedule_id", "")),
                                h.get("hora_programada"),
                                h.get("ejecutado_en"),
                                h.get("finalizado_en"),
                                str(h.get("job_id")) if h.get("job_id") else None,
                                h.get("estado", "pendiente"),
                                h.get("detalle"),
                            ),
                        )

    def add_history_entry(self, entry: dict[str, Any]) -> None:
        db.execute(
            """INSERT INTO schedule_history
               (id, schedule_id, hora_programada, ejecutado_en, finalizado_en, job_id, estado, detalle)
               VALUES (%s, %s, %s, %s::timestamptz, %s::timestamptz, %s, %s, %s)""",
            (
                str(entry.get("id", "")),
                str(entry.get("schedule_id", "")),
                entry.get("hora_programada"),
                entry.get("ejecutado_en"),
                entry.get("finalizado_en"),
                str(entry.get("job_id")) if entry.get("job_id") else None,
                entry.get("estado", "pendiente"),
                entry.get("detalle"),
            ),
        )


store = ScheduleStore()


def _check_and_fire(sch: dict[str, Any]) -> bool:
    """Evalúa un schedule y dispara el envío si corresponde. Retorna True si disparó."""
    if not sch.get("activo", False):
        return False

    hora = sch.get("hora", "")
    if not hora or ":" not in hora:
        return False

    ahora = _ahora_en_tz()
    ahora_hhmm = f"{ahora.hour:02d}:{ahora.minute:02d}"

    if ahora_hhmm != hora:
        return False

    # Verificar día de la semana
    dias = sch.get("dias_semana", [])
    if not _hoy_es_dia_valido(dias):
        return False

    # Evitar duplicados en la misma ventana (HH:MM)
    ultima = sch.get("ultima_ejecucion")
    if ultima is not None:
        try:
            dt_ultima = datetime.fromisoformat(ultima)
            if (
                dt_ultima.hour == ahora.hour
                and dt_ultima.minute == ahora.minute
            ):
                # Ya se ejecutó en esta ventana horaria
                return False
        except (ValueError, TypeError):
            pass

    # ¡Disparar!
    desde = sch.get("desde_fila", 1)
    job = jobs.start_new_job(desde=desde)

    schedule_id = sch.get("id", "")
    job_id = job.id if job else None
    estado = "ejecutando" if job else "error"
    detalle = None if job else "No se pudo iniciar: otro envío en curso o error interno"

    # Registrar en historial
    entry = {
        "id": str(__import__("uuid").uuid4()),
        "schedule_id": schedule_id,
        "hora_programada": hora,
        "ejecutado_en": ahora.isoformat(),
        "job_id": job_id,
        "estado": estado,
        "detalle": detalle,
    }
    store.add_history_entry(entry)

    # Actualizar ultima_ejecucion del schedule
    db.execute(
        "UPDATE schedules SET ultima_ejecucion = %s::timestamptz WHERE id = %s",
        (ahora.isoformat(), schedule_id),
    )

    return job is not None


def _update_historicos() -> None:
    """
    Revisa entradas del historial con estado 'ejecutando' y,
    si el job ya terminó (completado/error/cancelado), actualiza el estado.
    """
    history = store.load_history()
    modified = False
    for entry in history:
        if entry.get("estado") != "ejecutando":
            continue
        job_id = entry.get("job_id")
        if not job_id:
            continue
        job = jobs.get(job_id)
        if job is None:
            continue
        if job.state.value not in ("ejecutando", "pendiente"):
            finalizado = job.finalizado.isoformat() if job.finalizado else None
            detalle = (
                job.mensaje_error
                or f"{job.ok} enviados, {job.errores} fallidos de {job.total}"
            )
            db.execute(
                """UPDATE schedule_history
                   SET estado = %s, detalle = %s, finalizado_en = %s::timestamptz
                   WHERE id = %s""",
                (job.state.value, detalle, finalizado, entry.get("id")),
            )
            modified = True
    # Nota: ya no necesitamos save_history() porque actualizamos fila por fila


def scheduler_loop() -> None:
    """Loop principal del scheduler (corre en un thread daemon)."""
    interval = getattr(settings, "scheduler_check_interval", 30)
    history_poll = getattr(settings, "scheduler_history_poll", 15)
    ticks = 0
    while True:
        try:
            schedules = store.load_schedules()
            for sch in schedules:
                try:
                    _check_and_fire(sch)
                except Exception:
                    pass  # No dejar que un schedule malo mate el loop

            # Actualizar historial cada 'history_poll' segundos
            ticks += interval
            if ticks >= history_poll:
                try:
                    _update_historicos()
                except Exception:
                    pass
                ticks = 0
        except Exception:
            pass
        time.sleep(interval)


def start_scheduler() -> threading.Thread:
    """Arranca el scheduler en un thread daemon. No bloquea."""
    t = threading.Thread(target=scheduler_loop, daemon=True, name="scheduler")
    t.start()
    return t
