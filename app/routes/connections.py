"""Routes para conexiones Evolution."""

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import get_current_user
from app.services.evolution_service import (
    create_connection,
    delete_connection,
    get_connections_for_user,
    verify_connection,
)

router = APIRouter(prefix="/api/v1/connections", tags=["connections"])


@router.get("")
def list_connections(user: dict = Depends(get_current_user)):
    return {"connections": get_connections_for_user(str(user["id"]))}


@router.post("")
def add_connection(body: dict, user: dict = Depends(get_current_user)):
    name = body.get("name", "").strip()
    base_url = body.get("base_url", "").strip().rstrip("/")
    api_key = body.get("api_key", "")

    if not name or not base_url or not api_key:
        raise HTTPException(status_code=400, detail="name, base_url y api_key son requeridos")

    conn = create_connection(str(user["id"]), name, base_url, api_key)
    return conn


@router.delete("/{connection_id}")
def remove_connection(connection_id: str, user: dict = Depends(get_current_user)):
    delete_connection(connection_id, str(user["id"]))
    return {"ok": True}


@router.post("/{connection_id}/verify")
def verify_conn(connection_id: str, user: dict = Depends(get_current_user)):
    return verify_connection(connection_id, str(user["id"]))
