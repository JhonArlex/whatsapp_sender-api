-- =============================================================
-- Migration 005: zona horaria por programación
-- =============================================================
-- Cada schedule puede ejecutarse con la hora de su país (Colombia,
-- Chile, etc.). Vacío/NULL = usar la zona global del servidor
-- (TIMEZONE, por defecto America/Bogota).
-- =============================================================

ALTER TABLE job_schedules ADD COLUMN IF NOT EXISTS timezone VARCHAR(64) DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_job_schedules_tz ON job_schedules(timezone);
