"""Routes para plantillas de mensajes."""

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File

from app.config import settings
from app.core.auth import get_current_user
from app.services.template_service import (
    create_template,
    delete_template,
    list_templates,
    update_template,
)

router = APIRouter(prefix="/api/v1/message-templates", tags=["message-templates"])

UPLOAD_DIR = settings.data_dir / "template-media"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@router.get("")
def get_templates(user: dict = Depends(get_current_user)):
    return {"templates": list_templates(str(user["id"]))}


@router.post("")
def add_template(body: dict, user: dict = Depends(get_current_user)):
    name = body.get("name", "").strip()
    content = body.get("content", "").strip()
    msg_type = body.get("msg_type", "text")
    media_url = body.get("media_url", "")
    media_type = body.get("media_type", "")

    if not name:
        raise HTTPException(status_code=400, detail="El nombre es requerido")
    if not content:
        raise HTTPException(status_code=400, detail="El contenido es requerido")

    return create_template(str(user["id"]), name, content, msg_type, media_url, media_type)


@router.put("/{template_id}")
def edit_template(template_id: str, body: dict, user: dict = Depends(get_current_user)):
    name = body.get("name")
    content = body.get("content")
    msg_type = body.get("msg_type")
    media_url = body.get("media_url")
    media_type = body.get("media_type")

    if not any([name, content, msg_type, media_url is not None, media_type is not None]):
        raise HTTPException(status_code=400, detail="Debes enviar al menos un campo")

    result = update_template(
        template_id, str(user["id"]),
        name=name.strip() if name else None,
        content=content.strip() if content else None,
        msg_type=msg_type or None,
        media_url=media_url if media_url is not None else None,
        media_type=media_type if media_type is not None else None,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
    return result


@router.delete("/{template_id}")
def remove_template(template_id: str, user: dict = Depends(get_current_user)):
    delete_template(template_id, str(user["id"]))
    return {"ok": True}


@router.post("/upload")
async def upload_media(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    """Sube un archivo multimedia para usar en plantillas."""
    allowed = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    if file.content_type not in allowed:
        raise HTTPException(status_code=400, detail="Tipo de archivo no permitido. Usa: jpg, png, webp, gif")

    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
    filename = f"{uuid.uuid4().hex}{ext.get(file.content_type, '.bin')}"
    filepath = UPLOAD_DIR / filename

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:  # 5MB
        raise HTTPException(status_code=400, detail="La imagen no puede superar los 5MB")

    with open(filepath, "wb") as f:
        f.write(content)

    media_url = f"/media/templates/{filename}"
    return {"url": media_url, "media_type": file.content_type, "filename": filename}
