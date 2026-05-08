"""Routes para grupos de WhatsApp."""

from fastapi import APIRouter, Depends, Query

from app.core.auth import get_current_user
from app.services.evolution_service import get_groups, sync_groups

router = APIRouter(prefix="/api/v1/groups", tags=["groups"])


@router.get("")
def list_groups(
    search: str = Query("", description="Búsqueda por nombre/grupo"),
    instance_id: str = Query("", description="Filtrar por instancia"),
    user: dict = Depends(get_current_user),
):
    return {"groups": get_groups(str(user["id"]), search, instance_id)}


@router.post("/sync")
def sync_all_groups(user: dict = Depends(get_current_user)):
    groups = sync_groups(str(user["id"]))
    return {"groups": groups, "count": len(groups)}
