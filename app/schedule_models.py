"""Modelos Pydantic para el sistema de envíos programados."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, field_validator


class Schedule(BaseModel):
    """Un schedule de envío programado."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    hora: str  # "HH:MM"
    dias_semana: list[str] = Field(default_factory=list)
    # Vacío = todos los días; ["lun","mar","mie","jue","vie","sab","dom"]
    desde_fila: int = 1
    activo: bool = True
    ultima_ejecucion: str | None = None  # ISO datetime
    creado: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ScheduleHistory(BaseModel):
    """Registro de una ejecución automática del scheduler."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    # Vacío cuando el schedule fue borrado (NULL en BD por ON DELETE SET NULL)
    schedule_id: str = ""
    hora_programada: str
    ejecutado_en: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    finalizado_en: str | None = None
    job_id: str | None = None
    estado: str = "pendiente"  # pendiente | ejecutando | completado | cancelado | error
    detalle: str | None = None

    @field_validator("schedule_id", mode="before")
    @classmethod
    def _schedule_id_none_to_empty(cls, v: Any) -> str:
        if v is None:
            return ""
        return v if isinstance(v, str) else str(v)


# ── DTOs para la API ──────────────────────────────────────────────────────────


class CreateScheduleRequest(BaseModel):
    hora: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    dias_semana: list[str] = Field(default_factory=list)
    desde_fila: int = Field(default=1, ge=1)


class ScheduleResponse(BaseModel):
    schedules: list[Schedule]


class HistoryResponse(BaseModel):
    history: list[ScheduleHistory]
