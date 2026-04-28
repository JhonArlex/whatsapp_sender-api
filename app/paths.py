"""Resolución de carpetas de mensaje (msg.txt + imágenes)."""

from pathlib import Path

from app.config import settings


def resolve_msg_dir() -> Path:
    if settings.msg_dir:
        p = Path(settings.msg_dir)
        if not p.is_dir():
            raise FileNotFoundError(f"MSG_DIR no existe: {p}")
        return p

    nested = settings.data_dir / "mensaje"
    if nested.is_dir():
        return nested
    if (settings.data_dir / "msg.txt").is_file():
        return settings.data_dir
    raise FileNotFoundError(
        f"No hay msg.txt en {settings.data_dir} ni carpeta {nested}. "
        "Coloca mensaje/msg.txt e imágenes o define MSG_DIR."
    )


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
