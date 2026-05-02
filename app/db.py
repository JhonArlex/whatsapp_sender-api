"""Conexión a PostgreSQL y helpers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator

import psycopg2
import psycopg2.extras
import psycopg2.pool

from app.config import settings

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=5,
            dsn=settings.database_url,
        )
    return _pool


@contextmanager
def get_conn() -> Generator[psycopg2.extensions.connection, None, None]:
    pool = get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def query(sql: str, params: tuple | None = None) -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            try:
                return [dict(row) for row in cur.fetchall()]
            except psycopg2.ProgrammingError:
                return []


def execute(sql: str, params: tuple | None = None) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)


@contextmanager
def transaction() -> Generator[psycopg2.extensions.connection, None, None]:
    """Execute multiple operations in a single transaction.

    The connection is yielded directly so the caller can manage its own
    cursor and commit/rollback lifecycle.
    """
    pool = get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def validate_token(token: str) -> bool:
    rows = query(
        "SELECT id FROM api_tokens WHERE token = %s AND activo = true",
        (token,),
    )
    if rows:
        execute(
            "UPDATE api_tokens SET ultimo_uso = NOW() WHERE id = %s",
            (rows[0]["id"],),
        )
        return True
    return False
