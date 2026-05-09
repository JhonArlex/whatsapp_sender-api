"""Routes para plantillas de mensajes."""

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import get_current_user
from app.services.template_service import (
    create_template,
    delete_template,
    list_templates,
    update_template,
)

router = APIRouter(prefix="/api/v1/message-templates", tags=["message-templates"])


@router.get("")
def get_templates(user: dict = Depends(get_current_user)):
    return {"templates": list_templates(str(user["id"]))}


@router.post("")
def add_template(body: dict, user: dict = Depends(get_current_user)):
    name = body.get("name", "").strip()
    content = body.get("content", "").strip()
    msg_type = body.get("msg_type", "text")

    if not name:
        raise HTTPException(status_code=400, detail="El nombre es requerido")
    if not content:
        raise HTTPException(status_code=400, detail="El contenido es requerido")

    return create_template(str(user["id"]), name, content, msg_type)


@router.put("/{template_id}")
def edit_template(template_id: str, body: dict, user: dict = Depends(get_current_user)):
    name = body.get("name", "").strip() or None
    content = body.get("content", "").strip() or None

    if not name and not content:
        raise HTTPException(status_code=400, detail="Debes enviar al menos un campo")

    result = update_template(template_id, str(user["id"]), name=name, content=content)
    if not result:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
    return result


@router.delete("/{template_id}")
def remove_template(template_id: str, user: dict = Depends(get_current_user)):
    delete_template(template_id, str(user["id"]))
    return {"ok": True}
