"""Encriptación de API Keys usando Fernet (symmetric)."""

from cryptography.fernet import Fernet
from app.config import settings


def _get_fernet() -> Fernet:
    key = settings.crypto_key
    # Si no tiene formato Fernet válido, generamos uno derivado
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        # Derive a Fernet key using SHA256
        import base64, hashlib
        digest = hashlib.sha256(key.encode()).digest()
        fernet_key = base64.urlsafe_b64encode(digest)
        return Fernet(fernet_key)


_fernet = _get_fernet()


def encrypt_api_key(plain_text: str) -> str:
    if not plain_text:
        return ""
    return _fernet.encrypt(plain_text.encode()).decode()


def decrypt_api_key(encrypted: str) -> str:
    if not encrypted:
        return ""
    try:
        return _fernet.decrypt(encrypted.encode()).decode()
    except Exception:
        return encrypted  # fallback: devolver tal cual si no está encriptado
