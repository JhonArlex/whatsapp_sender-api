"""Routes para programación de Jobs."""

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import get_current_user
from app.services.scheduler_service import (
    create_schedule,
    delete_schedule,
    get_schedule,
    list_schedules,
    update_schedule,
)

router = APIRouter(prefix="/api/v1/schedules", tags=["schedules"])


@router.get("")
def get_schedules(user: dict = Depends(get_current_user)):
    return {"schedules": list_schedules(str(user["id"]))}


@router.get("/{schedule_id}")
def get_schedule_route(schedule_id: str, user: dict = Depends(get_current_user)):
    s = get_schedule(schedule_id, str(user["id"]))
    if not s:
        raise HTTPException(status_code=404, detail="Schedule no encontrado")
    return s


@router.post("")
def add_schedule(body: dict, user: dict = Depends(get_current_user)):
    job_id = body.get("job_id", "").strip()
    schedule_type = body.get("schedule_type", "once")

    if not job_id:
        raise HTTPException(status_code=400, detail="job_id es requerido")

    try:
        s = create_schedule(
            str(user["id"]),
            job_id,
            schedule_type=schedule_type,
            run_date=body.get("run_date"),
            run_time=body.get("run_time"),
            days_of_week=body.get("days_of_week"),
            interval_minutes=body.get("interval_minutes", 0),
            start_date=body.get("start_date"),
            end_date=body.get("end_date"),
        )
        return s
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{schedule_id}")
def edit_schedule(schedule_id: str, body: dict, user: dict = Depends(get_current_user)):
    result = update_schedule(schedule_id, str(user["id"]), **body)
    if not result:
        raise HTTPException(status_code=404, detail="Schedule no encontrado")
    return result


@router.delete("/{schedule_id}")
def remove_schedule(schedule_id: str, user: dict = Depends(get_current_user)):
    delete_schedule(schedule_id, str(user["id"]))
    return {"ok": True}
