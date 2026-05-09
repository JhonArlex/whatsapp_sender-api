"""Routes para plantillas de mensajes."""

import json
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

_ALLOWED = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
_MAX_SIZE = 5 * 1024 * 1024  # 5MB


@router.get("")
def get_templates(user: dict = Depends(get_current_user)):
    return {"templates": list_templates(str(user["id"]))}


@router.post("")
def add_template(body: dict, user: dict = Depends(get_current_user)):
    name = body.get("name", "").strip()
    content = body.get("content", "").strip()
    media_urls = body.get("media_urls", [])
    link_url = body.get("link_url", "").strip()

    if not name:
        raise HTTPException(status_code=400, detail="El nombre es requerido")
    if not content:
        raise HTTPException(status_code=400, detail="El contenido es requerido")

    return create_template(str(user["id"]), name, content, media_urls, link_url)


@router.put("/{template_id}")
def edit_template(template_id: str, body: dict, user: dict = Depends(get_current_user)):
    name = body.get("name")
    content = body.get("content")
    media_urls = body.get("media_urls")
    link_url = body.get("link_url")

    if not any([name, content, media_urls is not None, link_url is not None]):
        raise HTTPException(status_code=400, detail="Debes enviar al menos un campo")

    result = update_template(
        template_id, str(user["id"]),
        name=name.strip() if name else None,
        content=content.strip() if content else None,
        media_urls=media_urls if media_urls is not None else None,
        link_url=link_url.strip() if link_url else None,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
    return result


@router.delete("/{template_id}")
def remove_template(template_id: str, user: dict = Depends(get_current_user)):
    delete_template(template_id, str(user["id"]))
    return {"ok": True}


@router.post("/upload")
async def upload_media(
    files: list[UploadFile] = File(...),
    user: dict = Depends(get_current_user),
):
    """Sube uno o varios archivos multimedia. Máx 5MB c/u."""
    if len(files) > 10:
        raise HTTPException(status_code=400, detail="Máximo 10 archivos por vez")

    results = []
    for file in files:
        if file.content_type not in _ALLOWED:
            raise HTTPException(
                status_code=400,
                detail=f"Tipo no permitido: {file.filename}. Usa: jpg, png, webp, gif",
            )

        ext = _EXT.get(file.content_type, ".bin")
        filename = f"{uuid.uuid4().hex}{ext}"
        filepath = UPLOAD_DIR / filename

        content = await file.read()
        if len(content) > _MAX_SIZE:
            raise HTTPException(status_code=400, detail=f"{file.filename} supera los 5MB")

        with open(filepath, "wb") as f:
            f.write(content)

        results.append({
            "url": f"/media/templates/{filename}",
            "media_type": file.content_type,
            "filename": filename,
        })

    return {"files": results}
