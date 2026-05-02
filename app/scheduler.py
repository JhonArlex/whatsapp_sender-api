"""Scheduler daemon para envíos programados.

Ejecuta un thread daemon que cada N segundos revisa los schedules activos.
Dispara envíos cuando la hora coincide y no se ha ejecutado ya en la ventana.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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


def _schedules_path() -> Path:
    return settings.data_dir / "schedules.json"


def _history_path() -> Path:
    return settings.data_dir / "schedules_history.json"


def _load_json(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
        return json.loads(raw) if raw.strip() else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_json(path: Path, data: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


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
    """Persistencia JSON thread-safe para schedules."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def load_schedules(self) -> list[dict[str, Any]]:
        with self._lock:
            return _load_json(_schedules_path())

    def save_schedules(self, schedules: list[dict[str, Any]]) -> None:
        with self._lock:
            _save_json(_schedules_path(), schedules)

    def load_history(self) -> list[dict[str, Any]]:
        with self._lock:
            return _load_json(_history_path())

    def save_history(self, history: list[dict[str, Any]]) -> None:
        with self._lock:
            _save_json(_history_path(), history)

    def add_history_entry(self, entry: dict[str, Any]) -> None:
        history = self.load_history()
        history.append(entry)
        # Mantener solo los últimos 500 registros
        if len(history) > 500:
            history = history[-500:]
        self.save_history(history)


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
    schedules = store.load_schedules()
    for s in schedules:
        if s.get("id") == schedule_id:
            s["ultima_ejecucion"] = ahora.isoformat()
            break
    store.save_schedules(schedules)

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
            entry["estado"] = job.state.value
            entry["detalle"] = (
                job.mensaje_error
                or f"{job.ok} enviados, {job.errores} fallidos de {job.total}"
            )
            entry["finalizado_en"] = (
                job.finalizado.isoformat() if job.finalizado else None
            )
            modified = True
    if modified:
        store.save_history(history)


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
