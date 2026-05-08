"""JWT Authentication helpers and dependencies."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.db import execute, query

security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_access_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes),
        "iat": datetime.now(timezone.utc),
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != "access":
            return None
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def create_refresh_token(user_id: str) -> tuple[str, str]:
    """Returns (raw_token, token_hash). Stores hash in DB."""
    raw = secrets.token_urlsafe(64)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expires = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    execute(
        "INSERT INTO refresh_tokens (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
        (user_id, token_hash, expires),
    )
    return raw, token_hash


def validate_refresh_token(raw_token: str) -> str | None:
    """Returns user_id if valid, None otherwise."""
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    rows = query(
        "SELECT user_id, expires_at FROM refresh_tokens WHERE token_hash = %s",
        (token_hash,),
    )
    if not rows:
        return None
    row = rows[0]
    if row["expires_at"] < datetime.now(timezone.utc):
        return None
    return str(row["user_id"])


def revoke_refresh_token(raw_token: str) -> None:
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    execute("DELETE FROM refresh_tokens WHERE token_hash = %s", (token_hash,))


def revoke_all_user_tokens(user_id: str) -> None:
    execute("DELETE FROM refresh_tokens WHERE user_id = %s", (user_id,))


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
) -> dict:
    """FastAPI dependency: extracts current user from JWT."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de acceso requerido",
        )
    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado",
        )
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
        )
    rows = query("SELECT id, email, full_name, is_active FROM users WHERE id = %s", (user_id,))
    if not rows or not rows[0].get("is_active"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario no encontrado o inactivo",
        )
    return rows[0]
