"""Routes para programación de Jobs (scheduler)."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import settings
from app.core.auth import get_current_user
from app.services.scheduler_service import (
    SUPPORTED_TIMEZONES,
    create_schedule,
    delete_schedule,
    get_schedule,
    list_schedule_history,
    list_schedules,
    run_schedule_now,
    toggle_schedule,
    tz_label,
    update_schedule,
)

router = APIRouter(prefix="/api/v1/schedules", tags=["schedules"])


@router.get("/timezones")
def get_timezones(user: dict = Depends(get_current_user)):
    """Zonas horarias disponibles para programar (Colombia, Chile, ...)."""
    return {
        "timezones": SUPPORTED_TIMEZONES,
        "default": settings.timezone,
        "default_label": tz_label(None),
    }


@router.get("")
def get_schedules(
    include_inactive: bool = Query(True),
    user: dict = Depends(get_current_user),
):
    return {"schedules": list_schedules(str(user["id"]), include_inactive=include_inactive)}


@router.get("/history")
def get_schedule_history(
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    """Historial de ejecuciones automáticas/manuales de las programaciones."""
    return {"history": list_schedule_history(str(user["id"]), limit=limit)}


@router.get("/{schedule_id}")
def get_schedule_route(schedule_id: str, user: dict = Depends(get_current_user)):
    s = get_schedule(schedule_id, str(user["id"]))
    if not s:
        raise HTTPException(status_code=404, detail="Schedule no encontrado")
    return s


@router.post("")
def add_schedule(body: dict, user: dict = Depends(get_current_user)):
    job_id = str(body.get("job_id", "")).strip()
    if not job_id:
        raise HTTPException(status_code=400, detail="job_id es requerido")

    try:
        return create_schedule(
            str(user["id"]),
            job_id,
            schedule_type=body.get("schedule_type", "once"),
            run_date=body.get("run_date"),
            run_time=body.get("run_time"),
            days_of_week=body.get("days_of_week"),
            interval_minutes=int(body.get("interval_minutes") or 0),
            start_date=body.get("start_date"),
            end_date=body.get("end_date"),
            timezone_name=body.get("timezone"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{schedule_id}")
def edit_schedule(schedule_id: str, body: dict, user: dict = Depends(get_current_user)):
    try:
        result = update_schedule(schedule_id, str(user["id"]), **body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not result:
        raise HTTPException(status_code=404, detail="Schedule no encontrado")
    return result


@router.patch("/{schedule_id}/toggle")
def toggle_schedule_route(
    schedule_id: str,
    body: dict | None = None,
    user: dict = Depends(get_current_user),
):
    """Activa o pausa una programación."""
    body = body or {}
    is_active = body.get("is_active")
    result = toggle_schedule(schedule_id, str(user["id"]), is_active)
    if not result:
        raise HTTPException(status_code=404, detail="Schedule no encontrado")
    return result


@router.post("/{schedule_id}/run-now")
def run_now_route(schedule_id: str, user: dict = Depends(get_current_user)):
    """Ejecuta la programación ahora mismo (no altera la próxima ejecución)."""
    result = run_schedule_now(schedule_id, str(user["id"]))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "No se pudo ejecutar"))
    return result


@router.delete("/{schedule_id}")
def remove_schedule(schedule_id: str, user: dict = Depends(get_current_user)):
    if not delete_schedule(schedule_id, str(user["id"])):
        raise HTTPException(status_code=404, detail="Schedule no encontrado")
    return {"ok": True}
