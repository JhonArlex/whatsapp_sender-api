"""Resolución de carpetas de mensaje (msg.txt + imágenes).

En Docker, DATA_DIR es /app/data (volumen desde el host: típicamente <repo bulk-sender-api>/data).
Mensaje: /app/data/mensaje/ o msg.txt en la raíz de /app/data. Ver bulk-sender-api/.env.example.
Si falta ahí pero la imagen incluye COPY data → /opt/default-data, se usa ese fallback
(misma idea que resolve_csv_path frente al CSV).
"""

from pathlib import Path

from app.config import settings


def _image_paths(d: Path) -> list[Path]:
    return sorted(d.glob("*.jpeg")) + sorted(d.glob("*.jpg"))


def _bundle_ready(d: Path) -> bool:
    """msg.txt + al menos una imagen (evita elegir volumen incompleto: solo msg.txt)."""
    return (d / "msg.txt").is_file() and len(_image_paths(d)) > 0


def resolve_msg_dir() -> Path:
    if settings.msg_dir:
        p = Path(settings.msg_dir)
        if not p.is_dir():
            raise FileNotFoundError(f"MSG_DIR no existe: {p}")
        if not _bundle_ready(p):
            raise FileNotFoundError(
                f"Mensaje incompleto en {p}: se requiere msg.txt y al menos un .jpg o .jpeg"
            )
        return p

    dd = settings.data_dir
    fb = settings.default_data_dir / "mensaje"
    nested = dd / "mensaje"

    # Preferir bundle completo; si el volumen solo tiene msg.txt, usar default embebido.
    if _bundle_ready(nested):
        return nested
    if _bundle_ready(dd):
        return dd
    if _bundle_ready(fb):
        return fb

    if (nested / "msg.txt").is_file() and not _image_paths(nested):
        raise FileNotFoundError(
            f"Incompleto: {nested} tiene msg.txt pero no hay imágenes. "
            f"Añade .jpg/.jpeg ahí o despliega imagen con datos en {fb}."
        )
    if (dd / "msg.txt").is_file() and not _image_paths(dd):
        raise FileNotFoundError(
            f"Incompleto: {dd} tiene msg.txt pero no hay imágenes junto a él."
        )

    raise FileNotFoundError(
        f"No hay bundle de mensaje (msg.txt + imágenes) en {dd} ni en {fb}. "
        "Coloca data/mensaje/, define MSG_DIR, o build Docker con COPY data."
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
    """Texto + lista de imágenes (rutas verificadas)."""
    msg_dir = resolve_msg_dir()
    msg_path = msg_dir / "msg.txt"
    texto = msg_path.read_text(encoding="utf-8").strip()
    imagenes = [p for p in _image_paths(msg_dir) if p.is_file()]
    if not imagenes:
        raise FileNotFoundError(f"No hay .jpg/.jpeg legibles en {msg_dir}")
    return texto, imagenes, msg_dir
