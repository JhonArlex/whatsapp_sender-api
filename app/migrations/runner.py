"""Auto-runner de migraciones SQL al arrancar la aplicación.

Busca archivos .sql en app/migrations/ y los ejecuta en orden
si la tabla users (marcador) no existe.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from app.db import execute, query

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).resolve().parent


def _needs_migration() -> bool:
    """True si alguna tabla o columna faltante requiere migración."""
    try:
        rows = query(
            "SELECT EXISTS (SELECT FROM information_schema.tables "
            "WHERE table_name = 'users') AS existe"
        )
        if not rows or not rows[0]["existe"]:
            return True  # BD vacía — necesita DDL completo
    except Exception:
        return True

    # Verificar columnas que pudieron agregarse después del DDL inicial
    try:
        rows = query(
            "SELECT EXISTS (SELECT FROM information_schema.columns "
            "WHERE table_name = 'job_schedules' AND column_name = 'is_active') AS existe"
        )
        if rows and not rows[0]["existe"]:
            return True  # Falta columna is_active
    except Exception:
        return True  # La tabla ni siquiera existe
    return False


def run_migrations() -> None:
    """Ejecuta todos los .sql de app/migrations/ en orden alfabético."""
    if not _needs_migration():
        logger.info("Migraciones ya aplicadas — saltando")
        return

    sql_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    if not sql_files:
        logger.warning("No se encontraron archivos .sql en %s", _MIGRATIONS_DIR)
        return

    logger.info("Ejecutando %d migraciones...", len(sql_files))
    for f in sql_files:
        sql = f.read_text(encoding="utf-8")
        logger.info("  → %s (%d caracteres)", f.name, len(sql))
        try:
            execute(sql)
        except Exception as exc:
            logger.warning("  ⚠️  Error en %s: %s (continuando)", f.name, exc)
    logger.info("Migraciones completadas")
