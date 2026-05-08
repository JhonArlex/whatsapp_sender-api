"""Routes para jobs de envío masivo."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import get_current_user
from app.services.job_service import (
    cancel_job,
    create_job,
    get_job,
    list_jobs,
    retry_failed_groups,
)

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@router.get("")
def list_all_jobs(
    status: str = Query("", description="Filtrar por estado"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    return list_jobs(str(user["id"]), status, page, limit)


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


@router.post("/{job_id}/cancel")
def cancel_existing_job(job_id: str, user: dict = Depends(get_current_user)):
    ok = cancel_job(job_id, str(user["id"]))
    return {"ok": ok, "message": "Job cancelado" if ok else "No había job activo"}


@router.post("/{job_id}/retry-failed")
def retry_failed(job_id: str, user: dict = Depends(get_current_user)):
    result = retry_failed_groups(job_id, str(user["id"]))
    return result
