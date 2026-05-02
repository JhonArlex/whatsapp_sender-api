"""Helpers para devolver headers CORS coherentes (p. ej. en respuestas de error)."""

from __future__ import annotations

from fastapi import Request


def _allowed_origins_list(cors_origins_raw: str) -> tuple[list[str], bool]:
    origins = [o.strip() for o in cors_origins_raw.split(",") if o.strip()]
    allow_credentials = True
    if not origins:
        origins = ["*"]
        allow_credentials = False
    return origins, allow_credentials


def cors_headers_for_request(request: Request, cors_origins_raw: str) -> dict[str, str]:
    """Misma lógica que CORSMiddleware: eco del Origin si está permitido, o '*' si aplica."""
    origins, allow_credentials = _allowed_origins_list(cors_origins_raw)
    out: dict[str, str] = {}
    origin_hdr = request.headers.get("origin")

    if origins == ["*"]:
        out["Access-Control-Allow-Origin"] = "*"
        return out

    if origin_hdr and origin_hdr in origins:
        out["Access-Control-Allow-Origin"] = origin_hdr
    else:
        out["Access-Control-Allow-Origin"] = origins[0]

    if allow_credentials:
        out["Access-Control-Allow-Credentials"] = "true"

    return out
