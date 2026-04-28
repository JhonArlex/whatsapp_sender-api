"""Resolución de carpetas de mensaje (msg.txt + imágenes).

En Docker, DATA_DIR es /app/data (volumen desde el host: típicamente <repo bulk-sender-api>/data).
Mensaje: /app/data/mensaje/ o msg.txt en la raíz de /app/data. Ver bulk-sender-api/.env.example.
Si falta ahí pero la imagen incluye COPY data → /opt/default-data, se usa ese fallback
(misma idea que resolve_csv_path frente al CSV).
"""

from pathlib import Path

from app.config import settings


def resolve_msg_dir() -> Path:
    if settings.msg_dir:
        p = Path(settings.msg_dir)
        if not p.is_dir():
            raise FileNotFoundError(f"MSG_DIR no existe: {p}")
        if not (p / "msg.txt").is_file():
            raise FileNotFoundError(f"Falta {p}/msg.txt")
        return p

    dd = settings.data_dir
    fb = settings.default_data_dir / "mensaje"

    nested = dd / "mensaje"
    if (nested / "msg.txt").is_file():
        return nested
    if (dd / "msg.txt").is_file():
        return dd
    if (fb / "msg.txt").is_file():
        return fb

    raise FileNotFoundError(
        f"No hay msg.txt en {dd} ni en {fb}. "
        "Coloca data/mensaje/msg.txt en el volumen, define MSG_DIR, "
        "o despliega imagen con default-data (Dockerfile COPY data)."
    )


def resolve_csv_path() -> Path:
    """CSV en DATA_DIR; si no existe, mismo nombre bajo DEFAULT_DATA_DIR (copiado en imagen)."""
    p = settings.csv_path
    if p.is_file():
        return p
    fb = settings.default_data_dir / settings.csv_name
    if fb.is_file():
        return fb
    raise FileNotFoundError(f"No existe el CSV: {p}")


def load_message_bundle():
    """Texto + lista de imágenes."""
    msg_dir = resolve_msg_dir()
    msg_path = msg_dir / "msg.txt"
    if not msg_path.is_file():
        raise FileNotFoundError(f"Falta {msg_path}")

    texto = msg_path.read_text(encoding="utf-8").strip()
    imagenes = sorted(msg_dir.glob("*.jpeg")) + sorted(msg_dir.glob("*.jpg"))
    if not imagenes:
        raise FileNotFoundError(f"No hay .jpg/.jpeg en {msg_dir}")
    return texto, imagenes, msg_dir
