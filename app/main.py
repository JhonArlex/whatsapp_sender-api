import json
import logging
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import settings
from app import db
from app.cors_utils import cors_headers_for_request
from app.db import validate_token
from app.jobs import jobs
from app.schedule_models import (
    CreateScheduleRequest,
    HistoryResponse,
    Schedule,
    ScheduleHistory,
    ScheduleResponse,
)
from app.scheduler import start_scheduler, store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Bulk Sender API",
    description="Inicia envíos masivos a grupos (Evolution API) y consulta progreso.",
    version="1.0.0",
)

# CORS: CORS_ORIGINS en .env (coma-separado). Por defecto el dashboard en sender.jhonocampo.com.
_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
_allow_credentials = True
if not _origins:
    _origins = ["*"]
    _allow_credentials = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Evita 500 en texto plano sin CORS; deja el detalle en logs."""
    logger.exception("Error no manejado: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Error interno del servidor"},
        headers=cors_headers_for_request(request, settings.cors_origins),
    )


# ── Arrancar scheduler en background ────────────────────────────────────────


@app.on_event("startup")
def startup_scheduler():
    start_scheduler()



def require_service_key(x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None) -> None:
    if not x_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="X-API-Key faltante")
    if not validate_token(x_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido o inactivo")


class IniciarBody(BaseModel):
    desde: int = Field(default=1, ge=1, description="Fila del CSV desde la que empezar (1 = desde el inicio)")


class IniciarResponse(BaseModel):
    job_id: str
    estado: str
    mensaje: str


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/v1/envios", response_model=IniciarResponse)
def iniciar_envio(
    body: IniciarBody = IniciarBody(),
    _: None = Depends(require_service_key),
) -> IniciarResponse:
    job = jobs.start_new_job(desde=body.desde)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya hay un envío en curso. Consulta su estado o espera.",
        )
    return IniciarResponse(
        job_id=job.id,
        estado=job.state.value,
        mensaje="Envío iniciado. Usa GET /api/v1/envios/{job_id} para el progreso.",
    )


@app.get("/api/v1/envios/{job_id}")
def estado_envio(
    job_id: str,
    _: None = Depends(require_service_key),
) -> dict:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job no encontrado")
    return job.to_dict()


@app.post("/api/v1/envios/cancelar")
def cancelar_envio(
    _: None = Depends(require_service_key),
) -> dict:
    """Detiene el envío masivo en curso (entre un grupo y el siguiente)."""
    hubo = jobs.cancelar_envio_actual()
    return {
        "ok": hubo,
        "mensaje": "Envío detenido." if hubo else "No había ningún envío activo.",
    }


@app.get("/api/v1/envios")
def listar_ultimos(
    _: None = Depends(require_service_key),
) -> dict:
    """Ids conocidos en memoria (reiniciar el proceso borra el historial)."""
    return {"job_ids": jobs.list_job_ids(20)}


# ═══════════════════════════════════════════════════════════════════════════
# Endpoints de Programación (Schedules)
# ═══════════════════════════════════════════════════════════════════════════


@app.post("/api/v1/schedules", response_model=Schedule)
def crear_schedule(
    body: CreateScheduleRequest,
    _: None = Depends(require_service_key),
) -> Schedule:
    # Validar días de semana
    valid_dias = {"lun", "mar", "mie", "jue", "vie", "sab", "dom"}
    for d in body.dias_semana:
        if d.lower() not in valid_dias:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Día inválido: {d}. Usa: lun,mar,mie,jue,vie,sab,dom",
            )

    sch = Schedule(
        hora=body.hora,
        dias_semana=[d.lower() for d in body.dias_semana],
        desde_fila=body.desde_fila,
    )
    data = sch.model_dump(mode="json")
    # Insertar directamente en BD
    db.execute(
        """INSERT INTO schedules
           (id, hora, dias_semana, desde_fila, activo, creado)
           VALUES (%s, %s, %s::jsonb, %s, %s, %s::timestamptz)""",
        (
            str(data["id"]),
            data["hora"],
            json.dumps(data["dias_semana"]),
            data["desde_fila"],
            data["activo"],
            data["creado"],
        ),
    )
    return sch


@app.get("/api/v1/schedules", response_model=ScheduleResponse)
def listar_schedules(
    _: None = Depends(require_service_key),
) -> ScheduleResponse:
    raw = store.load_schedules()
    return ScheduleResponse(schedules=[Schedule(**s) for s in raw])


@app.delete("/api/v1/schedules/{schedule_id}")
def eliminar_schedule(
    schedule_id: str,
    _: None = Depends(require_service_key),
) -> dict:
    # Verificar que existe antes de borrar
    exists = store.load_schedule_by_id(schedule_id)
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Schedule no encontrado",
        )
    store.remove_schedule(schedule_id)
    return {"ok": True}


@app.put("/api/v1/schedules/{schedule_id}/toggle")
def toggle_schedule(
    schedule_id: str,
    _: None = Depends(require_service_key),
) -> dict:
    rows = db.query(
        "SELECT activo FROM schedules WHERE id = %s", (schedule_id,)
    )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Schedule no encontrado",
        )
    store.toggle_schedule(schedule_id)
    nuevo_activo = not rows[0]["activo"]
    return {"ok": True, "activo": nuevo_activo}


@app.get("/api/v1/schedules/history", response_model=HistoryResponse)
def historial_schedules(
    _: None = Depends(require_service_key),
) -> HistoryResponse:
    raw = store.load_history()
    return HistoryResponse(history=[ScheduleHistory(**h) for h in raw])
