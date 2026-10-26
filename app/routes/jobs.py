"""Routes para jobs de envío masivo (incluye historial y borrado lógico)."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import get_current_user
from app.services.job_service import (
    archive_finished_jobs,
    cancel_job,
    create_job,
    get_job,
    list_jobs,
    restore_job,
    retry_failed_groups,
    soft_delete_job,
)

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@router.get("")
def list_all_jobs(
    status: str = Query("", description="Filtrar por estado"),
    view: str = Query("active", description="active | history | all"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    return list_jobs(
        user_id=str(user["id"]),
        page=page,
        limit=limit,
        status_filter=status or None,
        view=view,
    )


@router.get("/history")
def list_job_history(
    status: str = Query("", description="Filtrar por estado"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    """Jobs archivados (borrado lógico) — vista de historial."""
    return list_jobs(
        user_id=str(user["id"]),
        page=page,
        limit=limit,
        status_filter=status or None,
        view="history",
    )


@router.post("/archive-finished")
def archive_finished(
    body: dict | None = None,
    user: dict = Depends(get_current_user),
):
    """Archiva masivamente los jobs ya ejecutados (borrado lógico).

    body opcional: {"older_than_days": 0, "statuses": ["completed", "error"]}
    """
    body = body or {}
    older_than_days = body.get("older_than_days") or 0
    statuses = body.get("statuses") or None
    return archive_finished_jobs(
        user_id=str(user["id"]),
        older_than_days=int(older_than_days),
        statuses=statuses,
    )


@router.post("")
def create_new_job(body: dict, user: dict = Depends(get_current_user)):
    name = body.get("name", "")
    groups = body.get("groups", [])
    messages = body.get("messages", [])

    if not groups:
        raise HTTPException(status_code=400, detail="Se requiere al menos un grupo")
    if not messages:
        raise HTTPException(status_code=400, detail="Se requiere al menos un mensaje")

    job = create_job(str(user["id"]), name, groups, messages)
    return job


@router.get("/{job_id}")
def get_job_detail(job_id: str, user: dict = Depends(get_current_user)):
    job = get_job(job_id, str(user["id"]))
    if not job:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    return job


@router.delete("/{job_id}")
def delete_job(
    job_id: str,
    reason: str = Query("", description="Motivo del archivado"),
    user: dict = Depends(get_current_user),
):
    """Borrado lógico: el job pasa al historial y puede restaurarse."""
    result = soft_delete_job(job_id, str(user["id"]), reason=reason)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.post("/{job_id}/restore")
def restore_archived_job(job_id: str, user: dict = Depends(get_current_user)):
    result = restore_job(job_id, str(user["id"]))
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.post("/{job_id}/cancel")
def cancel_existing_job(job_id: str, user: dict = Depends(get_current_user)):
    ok = cancel_job(job_id, str(user["id"]))
    return {"ok": ok, "message": "Job cancelado" if ok else "No había job activo"}


@router.post("/{job_id}/retry-failed")
def retry_failed(job_id: str, user: dict = Depends(get_current_user)):
    result = retry_failed_groups(job_id, str(user["id"]))
    return result
