"""Async HTTP client for Evolution API con pool de conexiones."""

from __future__ import annotations

from typing import Any

import httpx


class EvolutionClient:
    """Cliente para interactuar con Evolution API.

    Evolution API tiene CORS habilitado y requiere un header ``Origin``
    válido (definido via ``EVOLUTION_REQUEST_ORIGIN``).

    Reutiliza el pool de conexiones HTTP para mejor rendimiento.
    """

    def __init__(
        self,
        base_url: str,
        global_api_key: str | None = None,
        origin: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.global_api_key = global_api_key
        self.origin = (origin or "").split(",")[0].strip() if origin else None
        # Timeout reducido: 30s connect, 90s total
        self._timeout = httpx.Timeout(90.0, connect=30.0)
        # Pool de conexiones reutilizable
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            headers={"Content-Type": "application/json"},
        )

    def _headers(self, api_key: str | None = None) -> dict[str, str]:
        headers = {}
        # Priorizar global_api_key cuando está configurada (server-level auth)
        key = self.global_api_key or api_key
        if key:
            headers["apikey"] = key
        if self.origin:
            headers["Origin"] = self.origin.rstrip("/")
        return headers

    async def verify_server(self) -> dict | None:
        try:
            r = await self._client.get(
                f"{self.base_url}/",
                headers=self._headers(),
            )
            try:
                return r.json()
            except Exception:
                if r.status_code < 500:
                    return {"status": r.status_code}
                return None
        except (httpx.ConnectError, httpx.TimeoutException):
            return None
        except Exception:
            return None

    async def verify_creds(self, api_key: str) -> dict | None:
        try:
            r = await self._client.post(
                f"{self.base_url}/verify-creds",
                headers=self._headers(api_key),
                json={},
            )
            try:
                data = r.json()
                if r.status_code >= 400:
                    msg = str(data)
                    if "CORS" in msg or "Not allowed" in msg:
                        return {
                            "ok": False,
                            "cors_error": True,
                            "message": "CORS bloqueando",
                        }
                    return None
                return data
            except Exception:
                if r.status_code < 500:
                    return {"status": r.status_code}
                return None
        except Exception:
            return None

    async def fetch_instances(self, api_key: str) -> list[dict]:
        try:
            r = await self._client.get(
                f"{self.base_url}/instance/fetchInstances",
                headers=self._headers(api_key),
            )
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and "instances" in data:
                    return data["instances"]
            return []
        except Exception:
            return []

    async def get_instance(self, instance_name: str, api_key: str) -> dict | None:
        try:
            r = await self._client.get(
                f"{self.base_url}/instance/fetch/{instance_name}",
                headers=self._headers(api_key),
            )
            if r.status_code == 200:
                return r.json()
            return None
        except Exception:
            return None

    async def get_instance_token(self, instance_name: str, api_key: str) -> str | None:
        try:
            r = await self._client.get(
                f"{self.base_url}/instance/connectionState/{instance_name}",
                headers=self._headers(api_key),
            )
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict):
                    return data.get("instance", {}).get("token") or data.get("token")
            return None
        except Exception:
            return None

    async def fetch_all_groups(self, instance_name: str, instance_token: str, get_participants: bool = False) -> list[dict]:
        """Obtiene TODOS los grupos del usuario desde WhatsApp (no solo los cacheados).

        Evolution API: GET /group/fetchAllGroups/{instanceName}?getParticipants=true
        A diferencia de find_chats, este endpoint consulta WhatsApp directamente
        vía Baileys groupFetchAllParticipating, obteniendo grupos nuevos aunque
        no hayan tenido actividad reciente.
        """
        try:
            params = {}
            if get_participants:
                params["getParticipants"] = "true"
            r = await self._client.get(
                f"{self.base_url}/group/fetchAllGroups/{instance_name}",
                headers=self._headers(instance_token),
                params=params,
            )
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and "groups" in data:
                    return data["groups"]
                if isinstance(data, dict) and "records" in data:
                    return data["records"]
                # Evolution API v2 devuelve un objeto {jid: metadata} desde
                # Baileys groupFetchAllParticipating. Convertir a lista.
                if isinstance(data, dict):
                    groups = []
                    for jid, metadata in data.items():
                        if isinstance(metadata, dict):
                            metadata.setdefault("id", jid)
                            groups.append(metadata)
                        else:
                            groups.append({"id": jid, "subject": str(metadata)})
                    return groups
            return []
        except Exception:
            return []

    async def find_chats(self, instance_name: str, instance_token: str) -> list[dict]:
        try:
            r = await self._client.post(
                f"{self.base_url}/chat/findChats/{instance_name}",
                headers=self._headers(instance_token),
                json={"where": {"isGroup": True}},
            )
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and "chats" in data:
                    return data["chats"]
                if isinstance(data, dict) and "records" in data:
                    return data["records"]
            return []
        except Exception:
            return []

    async def find_all_chats(self, instance_name: str, instance_token: str) -> list[dict]:
        try:
            r = await self._client.post(
                f"{self.base_url}/chat/findChats/{instance_name}",
                headers=self._headers(instance_token),
                json={},
            )
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and "chats" in data:
                    return data["chats"]
                if isinstance(data, dict) and "records" in data:
                    return data["records"]
            return []
        except Exception:
            return []

    async def send_media(
        self,
        instance_name: str,
        instance_token: str,
        number: str,
        caption: str,
        media_base64: str,
        mimetype: str = "image/jpeg",
        filename: str = "image.jpg",
    ) -> dict:
        payload = {
            "number": number,
            "mediatype": "image",
            "mimetype": mimetype,
            "media": media_base64,
            "fileName": filename,
            "caption": caption,
        }
        r = await self._client.post(
            f"{self.base_url}/message/sendMedia/{instance_name}",
            headers=self._headers(instance_token),
            json=payload,
        )
        try:
            return r.json()
        except Exception:
            return {"status": r.status_code, "raw": r.text[:500]}

    async def send_text(
        self,
        instance_name: str,
        instance_token: str,
        number: str,
        text: str,
        delay: int = 0,
    ) -> dict:
        payload = {
            "number": number,
            "text": text,
            "options": {"delay": delay},
        }
        r = await self._client.post(
            f"{self.base_url}/message/sendText/{instance_name}",
            headers=self._headers(instance_token),
            json=payload,
        )
        try:
            return r.json()
        except Exception:
            return {"status": r.status_code, "raw": r.text[:500]}

    async def close(self):
        """Cierra el pool de conexiones HTTP."""
        await self._client.aclose()
