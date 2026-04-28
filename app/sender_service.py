"""Envío vía Evolution API (sendMedia)."""

import base64
from pathlib import Path

import requests


def send_to_group(
    base_url: str,
    instance: str,
    headers: dict[str, str],
    number: str,
    texto: str,
    imagenes: list[Path],
    extra_delay: float,
) -> tuple[bool, str | None]:
    """
    Devuelve (éxito, detalle_error).
    """
    import time

    url = f"{base_url}/message/sendMedia/{instance}"

    def post_image(path: Path, caption: str) -> tuple[int, dict]:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        payload = {
            "number": number,
            "mediatype": "image",
            "mimetype": "image/jpeg",
            "media": b64,
            "fileName": path.name,
            "caption": caption,
        }
        r = requests.post(url, headers=headers, json=payload, timeout=120)
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text[:500]}
        return r.status_code, body

    status, resp = post_image(imagenes[0], caption=texto)
    if status != 201:
        msg = _error_message(status, resp)
        return False, msg

    for img in imagenes[1:]:
        st, resp2 = post_image(img, caption="")
        if st != 201:
            return False, _error_message(st, resp2) or f"Imagen adicional {img.name}"
        time.sleep(extra_delay)

    return True, None


def _error_message(status: int, resp: dict) -> str:
    m = resp.get("message")
    if m:
        return f"HTTP {status}: {m}"
    inner = resp.get("response", {})
    if isinstance(inner, dict) and inner.get("message"):
        return f"HTTP {status}: {inner['message']}"
    return f"HTTP {status}: {resp!s}"
