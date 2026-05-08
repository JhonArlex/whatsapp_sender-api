"""Routes para instancias Evolution."""

from fastapi import APIRouter, Depends

from app.core.auth import get_current_user
from app.services.evolution_service import get_instances, sync_instances

router = APIRouter(prefix="/api/v1/instances", tags=["instances"])


@router.get("")
def list_instances(user: dict = Depends(get_current_user)):
    return {"instances": get_instances(str(user["id"]))}


@router.post("/sync")
def sync_all_instances(user: dict = Depends(get_current_user)):
    instances = sync_instances(str(user["id"]))
    return {"instances": instances, "count": len(instances)}
