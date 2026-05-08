"""Auth routes: register, login, refresh, logout, me."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    get_current_user,
    hash_password,
    revoke_all_user_tokens,
    revoke_refresh_token,
    validate_refresh_token,
    verify_password,
)
from app.db import execute, query

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register")
def register(body: dict):
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")
    full_name = body.get("full_name", "").strip()

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email y contraseña requeridos")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 6 caracteres")

    existing = query("SELECT id FROM users WHERE email = %s", (email,))
    if existing:
        raise HTTPException(status_code=409, detail="El email ya está registrado")

    pwd_hash = hash_password(password)
    execute(
        "INSERT INTO users (email, password_hash, full_name) VALUES (%s, %s, %s)",
        (email, pwd_hash, full_name),
    )

    user = query("SELECT id, email, full_name FROM users WHERE email = %s", (email,))[0]
    user_id = str(user["id"])
    access_token = create_access_token(user_id)
    refresh_token, _ = create_refresh_token(user_id)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user": {"id": user_id, "email": email, "full_name": full_name},
    }


@router.post("/login")
def login(body: dict):
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email y contraseña requeridos")

    rows = query("SELECT id, email, password_hash, full_name FROM users WHERE email = %s", (email,))
    if not rows:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    user = rows[0]
    if not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    user_id = str(user["id"])
    access_token = create_access_token(user_id)
    refresh_token, _ = create_refresh_token(user_id)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user": {"id": user_id, "email": user["email"], "full_name": user["full_name"]},
    }


@router.post("/refresh")
def refresh(body: dict):
    raw_token = body.get("refresh_token", "")
    if not raw_token:
        raise HTTPException(status_code=400, detail="Refresh token requerido")

    user_id = validate_refresh_token(raw_token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Refresh token inválido o expirado")

    # Revocar el viejo y crear nuevo (rotation)
    revoke_refresh_token(raw_token)
    new_access = create_access_token(user_id)
    new_refresh, _ = create_refresh_token(user_id)

    return {"access_token": new_access, "refresh_token": new_refresh}


@router.post("/logout")
def logout(body: dict, user: dict = Depends(get_current_user)):
    raw_token = body.get("refresh_token", "")
    if raw_token:
        revoke_refresh_token(raw_token)
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return {
        "id": str(user["id"]),
        "email": user["email"],
        "full_name": user["full_name"],
    }
