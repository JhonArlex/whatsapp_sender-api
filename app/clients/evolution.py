"""Async HTTP client for Evolution API."""

from __future__ import annotations

from typing import Any

import httpx


class EvolutionClient:
    """Cliente para interactuar con Evolution API."""

    def __init__(self, base_url: str, global_api_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.global_api_key = global_api_key
        self._timeout = httpx.Timeout(120.0)

    def _headers(self, api_key: str | None = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = api_key or self.global_api_key
        if key:
            headers["apikey"] = key
        return headers

    async def verify_server(self) -> dict | None:
        """GET / - verifica que el servidor responda."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.get(f"{self.base_url}/", headers=self._headers())
                if r.status_code == 200:
                    return r.json()
                return None
        except Exception:
            return None

    async def verify_creds(self, api_key: str) -> dict | None:
        """POST /verify-creds - verifica credenciales."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.post(
                    f"{self.base_url}/verify-creds",
                    headers=self._headers(api_key),
                    json={},
                )
                if r.status_code in (200, 201):
                    return r.json()
                return None
        except Exception:
            return None

    async def fetch_instances(self, api_key: str) -> list[dict]:
        """GET /instance/fetchAll - lista todas las instancias."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.get(
                    f"{self.base_url}/instance/fetchAll",
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
        """GET /instance/fetch/{instance}."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.get(
                    f"{self.base_url}/instance/fetch/{instance_name}",
                    headers=self._headers(api_key),
                )
                if r.status_code == 200:
                    return r.json()
                return None
        except Exception:
            return None

    async def get_instance_token(self, instance_name: str, api_key: str) -> str | None:
        """GET /instance/connectionState/{instance} - devuelve el token de la instancia."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.get(
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

    async def find_chats(self, instance_name: str, instance_token: str) -> list[dict]:
        """GET /chat/findChats/{instance} - obtiene todos los chats."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                url = f"{self.base_url}/chat/findChats/{instance_name}"
                params = {"where": '{"isGroup":true}'}
                r = await client.get(
                    url,
                    headers=self._headers(instance_token),
                    params=params,
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
        """GET /chat/findChats/{instance} sin filtro - todos los chats."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.get(
                    f"{self.base_url}/chat/findChats/{instance_name}",
                    headers=self._headers(instance_token),
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
        """POST /message/sendMedia/{instance}."""
        payload = {
            "number": number,
            "mediatype": "image",
            "mimetype": mimetype,
            "media": media_base64,
            "fileName": filename,
            "caption": caption,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
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
        """POST /message/sendText/{instance}."""
        payload = {
            "number": number,
            "text": text,
            "options": {"delay": delay},
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                f"{self.base_url}/message/sendText/{instance_name}",
                headers=self._headers(instance_token),
                json=payload,
            )
            try:
                return r.json()
            except Exception:
                return {"status": r.status_code, "raw": r.text[:500]}
