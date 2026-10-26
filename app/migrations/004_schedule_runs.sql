-- =============================================================
-- Migration 004: historial de ejecuciones del scheduler (schedule_runs)
-- =============================================================
-- La tabla legacy `schedule_history` tiene una FK a `schedules` (el
-- scheduler viejo basado en CSV), por lo que no sirve para registrar las
-- ejecuciones de `job_schedules`. Se crea una tabla propia.
-- =============================================================

CREATE TABLE IF NOT EXISTS schedule_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    schedule_id UUID NOT NULL REFERENCES job_schedules(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id),
    job_id UUID,
    hora_programada VARCHAR(10) DEFAULT '',
    estado VARCHAR(50) DEFAULT 'ejecutando',
    detalle TEXT,
    ejecutado_en TIMESTAMPTZ DEFAULT NOW(),
    finalizado_en TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_schedule_runs_schedule ON schedule_runs(schedule_id, ejecutado_en DESC);
CREATE INDEX IF NOT EXISTS idx_schedule_runs_job ON schedule_runs(job_id);
CREATE INDEX IF NOT EXISTS idx_schedule_runs_user ON schedule_runs(user_id);
