from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.config import settings
from app.jobs import jobs

app = FastAPI(
    title="Bulk Sender API",
    description="Inicia envíos masivos a grupos (Evolution API) y consulta progreso.",
    version="1.0.0",
)

# CORS: si CORS_ORIGINS está vacío, se permite cualquier origen (sin credenciales de cookie).
# En producción conviene fijar CORS_ORIGINS=https://tu-ui.com para restringir orígenes.
_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
_allow_credentials = True
if not _origins:
    _origins = ["*"]
    _allow_credentials = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_service_key(x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None) -> None:
    need = (settings.service_api_key or "").strip()
    if not need:
        return
    if not x_api_key or x_api_key != need:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="X-API-Key inválida o faltante")


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
