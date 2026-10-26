"""Auto-runner de migraciones SQL al arrancar la aplicación.

Aplica en orden alfabético los archivos ``app/migrations/*.sql`` que aún no
estén registrados en la tabla ``schema_migrations``. Esto permite agregar
migraciones nuevas (como la 003 de borrado lógico) sin tener que recrear la
base de datos.

Las migraciones deben ser idempotentes (``CREATE TABLE IF NOT EXISTS``,
``ALTER TABLE ... ADD COLUMN IF NOT EXISTS``) porque la 002 puede volver a
ejecutarse en bases creadas antes de existir ``schema_migrations``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.db import execute, query

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).resolve().parent

_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(255) PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT NOW()
);
"""


def _ensure_table() -> None:
    execute(_BOOTSTRAP)


def _applied_versions() -> set[str]:
    try:
        rows = query("SELECT version FROM schema_migrations")
        return {str(r["version"]) for r in rows}
    except Exception:
        return set()


def _needs_migration() -> bool:
    """True si existe algún .sql pendiente de aplicar."""
    applied = _applied_versions()
    sql_files = sorted(p.name for p in _MIGRATIONS_DIR.glob("*.sql"))
    return any(name not in applied for name in sql_files)


def run_migrations(force: bool = False) -> None:
    """Ejecuta los .sql pendientes de app/migrations/ en orden alfabético."""
    try:
        _ensure_table()
    except Exception as exc:  # pragma: no cover - BD caída al arrancar
        logger.warning("No se pudo preparar schema_migrations: %s", exc)
        return

    applied = set() if force else _applied_versions()
    sql_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    pending = [f for f in sql_files if f.name not in applied]

    if not pending:
        logger.info("Migraciones al día (%d aplicadas)", len(applied))
        return

    logger.info("Ejecutando %d migración(es) pendiente(s)...", len(pending))
    for f in pending:
        sql = f.read_text(encoding="utf-8")
        logger.info("  → %s (%d caracteres)", f.name, len(sql))
        try:
            execute(sql)
            execute(
                "INSERT INTO schema_migrations (version) VALUES (%s) "
                "ON CONFLICT (version) DO NOTHING",
                (f.name,),
            )
            logger.info("  ✓ %s aplicada", f.name)
        except Exception as exc:
            logger.warning("  ⚠️  Error en %s: %s (se reintentará al arrancar)", f.name, exc)
    logger.info("Migraciones completadas")
