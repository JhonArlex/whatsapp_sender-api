from __future__ import annotations

import csv
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.config import settings
from app.sender_service import send_to_group


class JobState(str, Enum):
    pendiente = "pendiente"
    ejecutando = "ejecutando"
    completado = "completado"
    cancelado = "cancelado"
    error = "error"


@dataclass
class ResultadoFila:
    fila: int
    grupo_id: str
    nombre: str
    estado: str  # pendiente | enviando | ok | error
    detalle: str | None = None


@dataclass
class Job:
    id: str
    state: JobState = JobState.pendiente
    desde: int = 1
    total: int = 0
    procesados: int = 0
    ok: int = 0
    errores: int = 0
    nombre_actual: str | None = None
    mensaje_error: str | None = None
    resultados: list[ResultadoFila] = field(default_factory=list)
    creado: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    iniciado: datetime | None = None
    finalizado: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "estado": self.state.value,
            "desde_fila": self.desde,
            "total_grupos": self.total,
            "procesados": self.procesados,
            "exitosos": self.ok,
            "fallidos": self.errores,
            "grupo_actual": self.nombre_actual,
            "error": self.mensaje_error,
            "creado": self.creado.isoformat(),
            "iniciado": self.iniciado.isoformat() if self.iniciado else None,
            "finalizado": self.finalizado.isoformat() if self.finalizado else None,
            "envios": [
                {
                    "fila": r.fila,
                    "grupo_id": r.grupo_id,
                    "nombre": r.nombre,
                    "estado": r.estado,
                    "detalle": r.detalle,
                }
                for r in self.resultados
            ],
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._running_id: str | None = None
        self._cancel_event = threading.Event()

    def list_job_ids(self, limit: int = 20) -> list[str]:
        with self._lock:
            ids = list(self._jobs.keys())
        return ids[-limit:]

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def start_new_job(self, desde: int) -> Job | None:
        """
        Crea e inicia un job en un hilo. None si ya hay otro en curso.
        """
        with self._lock:
            if self._running_id is not None:
                return None
            self._cancel_event.clear()
            job = Job(id=str(uuid.uuid4()), desde=max(1, desde))
            self._jobs[job.id] = job
            self._running_id = job.id
            job.state = JobState.ejecutando
            job.iniciado = datetime.now(timezone.utc)
        t = threading.Thread(target=self._run_job, args=(job.id,), daemon=True)
        t.start()
        return job

    def cancelar_envio_actual(self) -> bool:
        """
        Pide parar el lote en curso (hilo daemon). Devuelve True si había un envío activo.
        El envío se marca como cancelado al salir del bucle entre grupos o tras el envío actual.
        """
        with self._lock:
            if self._running_id is None:
                return False
        self._cancel_event.set()
        return True

    def _run_job(self, job_id: str) -> None:
        try:
            self._execute_sends(job_id)
        finally:
            with self._lock:
                if self._running_id == job_id:
                    self._running_id = None

    def _execute_sends(self, job_id: str) -> None:
        from app.paths import load_message_bundle, resolve_csv_path

        job = self._jobs[job_id]
        try:
            texto, imagenes, _msg_dir = load_message_bundle()
        except Exception as e:
            job.state = JobState.error
            job.mensaje_error = str(e)
            job.finalizado = datetime.now(timezone.utc)
            return

        if not settings.evolution_api_key.strip():
            job.state = JobState.error
            job.mensaje_error = "EVOLUTION_API_KEY no configurada"
            job.finalizado = datetime.now(timezone.utc)
            return

        try:
            path = resolve_csv_path()
        except FileNotFoundError as e:
            job.state = JobState.error
            job.mensaje_error = str(e)
            job.finalizado = datetime.now(timezone.utc)
            return

        grupos: list[tuple[int, str, str]] = []
        with open(path, encoding="utf-8") as f:
            for i, row in enumerate(csv.DictReader(f), 1):
                if i >= job.desde:
                    gid = row.get("ID", row.get("id", ""))
                    nombre = row.get("Nombre", row.get("nombre", ""))
                    grupos.append((i, gid, nombre))

        job.total = len(grupos)
        job.resultados = [
            ResultadoFila(fila=i, grupo_id=g, nombre=n, estado="pendiente") for i, g, n in grupos
        ]

        base_url = settings.evolution_api_url.rstrip("/")
        headers_base: dict[str, str] = {"apikey": settings.evolution_api_key, "Content-Type": "application/json"}
        origin_candidates = settings.evolution_request_origins_list

        for idx, (fila, gid, nombre) in enumerate(grupos):
            if self._cancel_event.is_set():
                job.state = JobState.cancelado
                job.nombre_actual = None
                job.finalizado = datetime.now(timezone.utc)
                return

            job.nombre_actual = nombre[:120] if nombre else None
            res = job.resultados[idx]
            res.estado = "enviando"
            res.detalle = None

            try:
                ok, detalle = send_to_group(
                    base_url=base_url,
                    instance=settings.instance,
                    headers_base=headers_base,
                    origin_candidates=origin_candidates,
                    number=gid,
                    texto=texto,
                    imagenes=imagenes,
                    extra_delay=settings.extra_image_delay,
                )
                if ok:
                    res.estado = "ok"
                    res.detalle = None
                    job.ok += 1
                else:
                    res.estado = "error"
                    res.detalle = detalle
                    job.errores += 1
            except Exception as e:
                res.estado = "error"
                res.detalle = str(e)
                job.errores += 1

            job.procesados += 1
            if idx < len(grupos) - 1:
                time.sleep(settings.delay_seg)
                if self._cancel_event.is_set():
                    job.state = JobState.cancelado
                    job.nombre_actual = None
                    job.finalizado = datetime.now(timezone.utc)
                    return

        job.state = JobState.completado
        job.nombre_actual = None
        job.finalizado = datetime.now(timezone.utc)


jobs = JobManager()
