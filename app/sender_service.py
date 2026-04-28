"""Envío vía Evolution API (sendMedia)."""

import base64
import time
from pathlib import Path

import requests


def _merge_cors_headers(headers_base: dict[str, str], origin: str | None) -> dict[str, str]:
    h = {**headers_base}
    if origin:
        base = origin.rstrip("/")
        h["Origin"] = base
        h["Referer"] = base + "/"
    return h


def _evolution_cors_rejected(resp: dict) -> bool:
    for key in ("message",):
        m = resp.get(key)
        if isinstance(m, str) and "Not allowed by CORS" in m:
            return True
    inner = resp.get("response", {})
    if isinstance(inner, dict):
        m = inner.get("message")
        if isinstance(m, str) and "Not allowed by CORS" in m:
            return True
    return False


def send_to_group(
    base_url: str,
    instance: str,
    headers_base: dict[str, str],
    origin_candidates: list[str],
    number: str,
    texto: str,
    imagenes: list[Path],
    extra_delay: float,
) -> tuple[bool, str | None]:
    """
    Devuelve (éxito, detalle_error).
    Evolution aplica cors() a todas las rutas; sin Origin válido ante CORS_ORIGIN,
    suele responder 500. Si ves CORS tras un intento, reintenta con otros orígenes
    (lista en EVOLUTION_REQUEST_ORIGIN separada por comas).
    """
    url = f"{base_url}/message/sendMedia/{instance}"

    def post_image(hdrs: dict[str, str], path: Path, caption: str) -> tuple[int, dict]:
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
        r = requests.post(url, headers=hdrs, json=payload, timeout=120)
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text[:500]}
        return r.status_code, body

    rounds: list[str | None] = (
        [*origin_candidates] if origin_candidates else [None]
    )

    headers_ok: dict[str, str] | None = None
    last_err = ""

    # Primer POST exitoso establece Origin/CORS válido para el resto
    first_probe = imagenes[0]
    caption_first = texto if len(imagenes) == 1 else ""

    for origin in rounds:
        hdrs_try = _merge_cors_headers(headers_base, origin)
        status, resp = post_image(hdrs_try, first_probe, caption=caption_first)
        if status == 201:
            headers_ok = hdrs_try
            break
        last_err = _error_message(status, resp)
        if not _evolution_cors_rejected(resp) or origin == rounds[-1]:
            return False, last_err

    if headers_ok is None:
        return False, last_err or "Sin respuesta de Evolution."

    if len(imagenes) > 1:
        for img in imagenes[1:-1]:
            st, resp2 = post_image(headers_ok, img, caption="")
            if st != 201:
                return False, _error_message(st, resp2) or f"Imagen adicional {img.name}"
            time.sleep(extra_delay)
        st, resp2 = post_image(headers_ok, imagenes[-1], caption=texto)
        if st != 201:
            return False, _error_message(st, resp2) or f"Última imagen {imagenes[-1].name}"
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
