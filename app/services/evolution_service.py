"""Servicio que coordina conexiones, instancias y grupos contra Evolution API."""

from __future__ import annotations

from datetime import datetime, timezone

from app.clients.evolution import EvolutionClient
from app.config import settings
from app.core.crypto import decrypt_api_key, encrypt_api_key
from app.db import execute, query


# ── Conexiones ──────────────────────────────────────────────────────────


def get_connections_for_user(user_id: str) -> list[dict]:
    """Lista las conexiones Evolution de un usuario."""
    rows = query(
        "SELECT id, name, base_url, api_key_encrypted, is_active, last_verified_at, created_at "
        "FROM evolution_connections WHERE user_id = %s ORDER BY created_at DESC",
        (user_id,),
    )
    result = []
    for r in rows:
        result.append({
            "id": str(r["id"]),
            "name": r["name"],
            "base_url": r["base_url"],
            "has_api_key": bool(r["api_key_encrypted"]),
            "is_active": r["is_active"],
            "last_verified_at": r["last_verified_at"].isoformat() if r.get("last_verified_at") else None,
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        })
    return result


def create_connection(user_id: str, name: str, base_url: str, api_key: str) -> dict:
    encrypted = encrypt_api_key(api_key)
    execute(
        "INSERT INTO evolution_connections (user_id, name, base_url, api_key_encrypted) VALUES (%s, %s, %s, %s)",
        (user_id, name, base_url.rstrip("/"), encrypted),
    )
    rows = query(
        "SELECT id, name, base_url, created_at FROM evolution_connections WHERE user_id = %s AND name = %s",
        (user_id, name),
    )
    r = rows[0]
    return {
        "id": str(r["id"]),
        "name": r["name"],
        "base_url": r["base_url"],
        "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
    }


def update_connection(
    connection_id: str,
    user_id: str,
    name: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> dict | None:
    """Actualiza los campos enviados de una conexión. Retorna la conexión actualizada o None si no existe."""
    # Verificar que existe y pertenece al usuario
    existing = query(
        "SELECT id FROM evolution_connections WHERE id = %s AND user_id = %s",
        (connection_id, user_id),
    )
    if not existing:
        return None

    updates = []
    params = []

    if name is not None:
        updates.append("name = %s")
        params.append(name)
    if base_url is not None:
        updates.append("base_url = %s")
        params.append(base_url.rstrip("/"))
    if api_key is not None:
        updates.append("api_key_encrypted = %s")
        params.append(encrypt_api_key(api_key))

    if updates:
        params.append(connection_id)
        execute(
            f"UPDATE evolution_connections SET {', '.join(updates)} WHERE id = %s",
            tuple(params),
        )

    # Devolver la conexión actualizada
    rows = query(
        "SELECT id, name, base_url, api_key_encrypted, is_active, last_verified_at, created_at "
        "FROM evolution_connections WHERE id = %s",
        (connection_id,),
    )
    if not rows:
        return None
    r = rows[0]
    return {
        "id": str(r["id"]),
        "name": r["name"],
        "base_url": r["base_url"],
        "has_api_key": bool(r["api_key_encrypted"]),
        "is_active": r["is_active"],
        "last_verified_at": r["last_verified_at"].isoformat() if r.get("last_verified_at") else None,
        "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
    }


def delete_connection(connection_id: str, user_id: str) -> bool:
    execute(
        "DELETE FROM evolution_connections WHERE id = %s AND user_id = %s",
        (connection_id, user_id),
    )
    return True


def verify_connection(connection_id: str, user_id: str) -> dict:
    """
    Verifica la conexión y retorna un error detallado explicando por qué falla.
    """
    rows = query(
        "SELECT base_url, api_key_encrypted FROM evolution_connections WHERE id = %s AND user_id = %s",
        (connection_id, user_id),
    )
    if not rows:
        return {"ok": False, "error": "Conexión no encontrada. Puede haber sido eliminada."}

    r = rows[0]
    api_key = decrypt_api_key(r["api_key_encrypted"])
    base_url = r["base_url"]
    client = EvolutionClient(base_url, api_key, origin=settings.evolution_request_origin)

    import asyncio

    async def _verify():
        # 1. Verificar que el servidor responde
        try:
            server = await client.verify_server()
        except Exception as e:
            return {
                "ok": False,
                "error": (
                    f"❌ No se pudo conectar con el servidor.\n\n"
                    f"URL: {base_url}\n\n"
                    f"Posibles causas:\n"
                    f"• La URL es incorrecta — revisa que sea exacta\n"
                    f"• El servidor Evolution API está apagado o caído\n"
                    f"• Hay un firewall o proxy bloqueando la conexión\n"
                    f"• El puerto no está expuesto\n\n"
                    f"Detalle técnico: {str(e)}"
                ),
            }

        if not server:
            return {
                "ok": False,
                "error": (
                    f"❌ El servidor respondió pero no devolvió datos válidos.\n\n"
                    f"URL: {base_url}\n\n"
                    f"Posibles causas:\n"
                    f"• La URL no apunta a un servidor Evolution API válido\n"
                    f"• El servidor está mal configurado\n"
                    f"• Es una versión incompatible de Evolution API"
                ),
            }

        # 2. Verificar credenciales (API Key)
        try:
            creds = await client.verify_creds(api_key)
        except Exception as e:
            return {
                "ok": False,
                "error": (
                    f"❌ Error al verificar la API Key.\n\n"
                    f"URL: {base_url}\n\n"
                    f"El servidor respondió pero ocurrió un error al validar la API Key.\n"
                    f"Detalle técnico: {str(e)}"
                ),
            }

        if not creds:
            return {
                "ok": False,
                "error": (
                    f"❌ API Key inválida.\n\n"
                    f"El servidor {base_url} responde correctamente, "
                    f"pero la API Key global no es válida.\n\n"
                    f"Para solucionarlo:\n"
                    f"1. Ve a la configuración de tu servidor Evolution API\n"
                    f"2. Genera una nueva API Key global\n"
                    f"3. Cópiala exactamente (sin espacios extras)\n"
                    f"4. Edita esta conexión y pega la nueva API Key"
                ),
            }

        # 3. Todo OK
        version = server.get("version") or server.get("apiVersion", "desconocida")
        instance_name = server.get("instance") or server.get("name", "")

        execute(
            "UPDATE evolution_connections SET last_verified_at = NOW() WHERE id = %s",
            (connection_id,),
        )

        msg = "✅ Conexión exitosa"
        details_parts = []
        if version:
            details_parts.append(f"Versión: {version}")
        if instance_name:
            details_parts.append(f"Servidor: {instance_name}")
        if details_parts:
            msg += f" | {' | '.join(details_parts)}"

        return {
            "ok": True,
            "message": msg,
            "version": version,
            "server": instance_name,
        }

    return asyncio.run(_verify())


# ── Instancias ──────────────────────────────────────────────────────────


def sync_instances(user_id: str) -> list[dict]:
    """Sincroniza todas las instancias desde todas las conexiones del usuario."""
    connections = query(
        "SELECT id, base_url, api_key_encrypted FROM evolution_connections WHERE user_id = %s AND is_active = true",
        (user_id,),
    )
    all_instances = []

    import asyncio

    async def _sync_all():
        for conn in connections:
            conn_id = str(conn["id"])
            api_key = decrypt_api_key(conn["api_key_encrypted"])
            client = EvolutionClient(conn["base_url"], api_key, origin=settings.evolution_request_origin)
            instances = await client.fetch_instances(api_key)
            for inst in instances:
                inst_id = inst.get("id", inst.get("instanceId", ""))
                inst_name = inst.get("name", inst.get("instanceName", ""))
                token = inst.get("token", "")
                status = inst.get("connectionStatus", inst.get("status", "unknown"))

                # Upsert en cache
                existing = query(
                    "SELECT id FROM instances_cache WHERE connection_id = %s AND instance_id = %s",
                    (conn_id, inst_id),
                )
                now = datetime.now(timezone.utc)
                if existing:
                    execute(
                        "UPDATE instances_cache SET instance_name=%s, connection_status=%s, "
                        "owner_jid=%s, profile_name=%s, token=%s, synced_at=%s WHERE id=%s",
                        (inst_name, status, inst.get("ownerJid", ""),
                         inst.get("profileName", ""), token, now, existing[0]["id"]),
                    )
                else:
                    execute(
                        "INSERT INTO instances_cache (connection_id, instance_id, instance_name, "
                        "connection_status, owner_jid, profile_name, token, synced_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (conn_id, inst_id, inst_name, status,
                         inst.get("ownerJid", ""), inst.get("profileName", ""), token, now),
                    )
                all_instances.append({
                    "connection_id": conn_id,
                    "instance_id": inst_id,
                    "instance_name": inst_name,
                    "status": status,
                    "owner_jid": inst.get("ownerJid", ""),
                    "profile_name": inst.get("profileName", ""),
                })
        return all_instances

    return asyncio.run(_sync_all())


def get_instances(user_id: str) -> list[dict]:
    """Devuelve instancias cacheadas del usuario."""
    rows = query(
        """SELECT ic.id, ic.connection_id, ec.name as connection_name, ec.base_url,
                  ic.instance_id, ic.instance_name, ic.connection_status,
                  ic.owner_jid, ic.profile_name, ic.token, ic.synced_at
           FROM instances_cache ic
           JOIN evolution_connections ec ON ec.id = ic.connection_id
           WHERE ec.user_id = %s
           ORDER BY ec.name, ic.instance_name""",
        (user_id,),
    )
    return [
        {
            "id": str(r["id"]),
            "connection_id": str(r["connection_id"]),
            "connection_name": r["connection_name"],
            "base_url": r["base_url"],
            "instance_id": r["instance_id"],
            "instance_name": r["instance_name"],
            "status": r["connection_status"],
            "owner_jid": r["owner_jid"],
            "profile_name": r["profile_name"],
            "has_token": bool(r["token"]),
            "synced_at": r["synced_at"].isoformat() if r.get("synced_at") else None,
        }
        for r in rows
    ]


# ── Grupos ──────────────────────────────────────────────────────────────


def sync_groups(user_id: str) -> list[dict]:
    """Sincroniza grupos desde todas las instancias del usuario."""
    instances = query(
        """SELECT ic.id, ic.instance_name, ic.token, ec.base_url, ec.user_id
           FROM instances_cache ic
           JOIN evolution_connections ec ON ec.id = ic.connection_id
           WHERE ec.user_id = %s AND ic.connection_status = 'open' AND ic.token != ''""",
        (user_id,),
    )

    all_groups = []

    import asyncio

    async def _sync():
        for inst in instances:
            instance_name = inst["instance_name"]
            token = inst["token"]
            base_url = inst["base_url"]
            inst_cache_id = str(inst["id"])

            client = EvolutionClient(base_url, origin=settings.evolution_request_origin)
            chats = await client.find_chats(instance_name, token)

            for chat in chats:
                remote_jid = chat.get("remoteJid", chat.get("id", ""))
                push_name = chat.get("pushName", chat.get("name", ""))
                subject = chat.get("subject", push_name)

                existing = query(
                    "SELECT id FROM groups_cache WHERE instance_cache_id = %s AND remote_jid = %s",
                    (inst_cache_id, remote_jid),
                )
                now = datetime.now(timezone.utc)
                if existing:
                    execute(
                        "UPDATE groups_cache SET push_name=%s, subject=%s, synced_at=%s WHERE id=%s",
                        (push_name, subject, now, existing[0]["id"]),
                    )
                else:
                    execute(
                        "INSERT INTO groups_cache (instance_cache_id, remote_jid, push_name, subject, synced_at) "
                        "VALUES (%s,%s,%s,%s,%s)",
                        (inst_cache_id, remote_jid, push_name, subject, now),
                    )
                all_groups.append({
                    "instance_name": instance_name,
                    "remote_jid": remote_jid,
                    "push_name": push_name,
                    "subject": subject,
                })
        return all_groups

    return asyncio.run(_sync())


def get_groups(user_id: str, search: str = "", instance_id: str = "") -> list[dict]:
    """Devuelve grupos cacheados del usuario."""
    params: list = [user_id]
    sql = """SELECT gc.id, gc.instance_cache_id, ic.instance_name, ic.token as instance_token,
                    ec.base_url as evolution_base_url,
                    gc.remote_jid, gc.push_name, gc.subject, gc.synced_at
             FROM groups_cache gc
             JOIN instances_cache ic ON ic.id = gc.instance_cache_id
             JOIN evolution_connections ec ON ec.id = ic.connection_id
             WHERE ec.user_id = %s"""

    if search:
        sql += " AND (gc.push_name ILIKE %s OR gc.subject ILIKE %s OR gc.remote_jid ILIKE %s)"
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

    if instance_id:
        sql += " AND ic.id = %s"
        params.append(instance_id)

    sql += " ORDER BY gc.push_name ASC"

    rows = query(sql, tuple(params))
    return [
        {
            "id": str(r["id"]),
            "instance_name": r["instance_name"],
            "remote_jid": r["remote_jid"],
            "push_name": r["push_name"],
            "subject": r["subject"],
            "instance_token": r["instance_token"] or "",
            "evolution_base_url": r["evolution_base_url"] or "",
            "synced_at": r["synced_at"].isoformat() if r.get("synced_at") else None,
        }
        for r in rows
    ]
