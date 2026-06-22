"""Routes para plantillas de mensajes."""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import Response

from app.core.auth import get_current_user
from app.services.minio_service import get_file, upload_file
from app.services.template_service import (
    create_template,
    delete_template,
    list_templates,
    update_template,
)

router = APIRouter(prefix="/api/v1/message-templates", tags=["message-templates"])

_ALLOWED = {"image/jpeg", "image/png", "image/webp", "image/gif"}
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
    """Sube uno o varios archivos a MinIO. Máx 5MB c/u, hasta 10 archivos."""
    if len(files) > 10:
        raise HTTPException(status_code=400, detail="Máximo 10 archivos por vez")

    results = []
    for file in files:
        if file.content_type not in _ALLOWED:
            raise HTTPException(
                status_code=400,
                detail=f"Tipo no permitido: {file.filename}. Usa: jpg, png, webp, gif",
            )

        content = await file.read()
        if len(content) > _MAX_SIZE:
            raise HTTPException(status_code=400, detail=f"{file.filename} supera los 5MB")

        filename = upload_file(content, file.content_type)
        file_url = f"/api/v1/message-templates/media/{filename}"
        results.append({
            "url": file_url,
            "media_type": file.content_type,
            "filename": filename,
        })

    return {"files": results}


@router.get("/media/{filename}")
def serve_media(filename: str):
    """Sirve un archivo de MinIO a través de la API."""
    result = get_file(filename)
    if not result:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    bytes_data, content_type = result
    return Response(content=bytes_data, media_type=content_type)


@router.post("/{template_id}/test")
def test_template(template_id: str, body: dict, user: dict = Depends(get_current_user)):
    """Envía la plantilla a un número específico para probarla."""
    from app.db import query as db_query
    from app.services.template_service import list_templates as _list
    from app.clients.evolution import EvolutionClient as _EC
    from app.config import settings as _settings

    import asyncio

    # Obtener plantilla
    templates = _list(str(user["id"]))
    tpl = next((t for t in templates if t["id"] == template_id), None)
    if not tpl:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")

    instance_name = body.get("instance_name", "").strip()
    remote_jid = body.get("remote_jid", "").strip()
    if not instance_name or not remote_jid:
        raise HTTPException(status_code=400, detail="instance_name y remote_jid son requeridos")

    # Buscar conexión activa que tenga la instancia
    rows = db_query(
        """SELECT ec.base_url, ic.token
           FROM evolution_connections ec
           JOIN instances_cache ic ON ic.connection_id = ec.id
           WHERE ec.user_id = %s AND ic.instance_name = %s AND ec.is_active = true
           LIMIT 1""",
        (str(user["id"]), instance_name),
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"Instancia '{instance_name}' no encontrada")

    base_url = rows[0]["base_url"]
    instance_token = rows[0]["token"]

    client = _EC(base_url, _settings.evolution_api_key, origin=_settings.evolution_request_origin)

    async def _send():
        msg = tpl.get("content", "")
        media_urls = tpl.get("media_urls", [])
        # Limpiar número: quitar sufijos de WhatsApp
        number = remote_jid.replace("@s.whatsapp.net", "").replace("@g.us", "")

        if media_urls and len(media_urls) > 0:
            # Enviar primera imagen con caption, el resto sin caption
            for i, murl in enumerate(media_urls):
                caption = msg if i == 0 else ""
                # Descargar imagen de MinIO
                from app.services.minio_service import get_file as _get_file
                fname = murl.split("/")[-1]
                fdata = _get_file(fname)
                if fdata is None:
                    continue
                import base64
                b64 = base64.b64encode(fdata[0]).decode()
                mimetype = fdata[1]
                await client.send_media(
                    instance_name, instance_token,
                    number, caption, b64, mimetype, fname,
                )
            return {"ok": True, "message": f"Enviado a {remote_jid}", "evolution_status": "sent"}

        # Solo texto
        resp = await client.send_text(instance_name, instance_token, number, msg)
        if not resp or resp.get("status") == "PENDING" or resp.get("key"):
            return {"ok": True, "message": f"Enviado a {remote_jid}", "evolution_status": resp.get("status", "ok")}
        return {"ok": False, "message": f"Error al enviar: {resp}"}

    return asyncio.run(_send())
