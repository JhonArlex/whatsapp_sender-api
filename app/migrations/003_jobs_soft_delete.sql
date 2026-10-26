-- =============================================================
-- Migration 003: Borrado lógico de jobs + soporte de programación
-- =============================================================
-- Los jobs ya ejecutados no se eliminan físicamente: se marcan con
-- deleted_at para que la UI los muestre en el "Historial".
-- job_schedules.last_job_id guarda el último job disparado por el
-- scheduler (para historial y para evitar solapamientos).
-- =============================================================

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS deleted_by UUID REFERENCES users(id);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS deleted_reason VARCHAR(255) DEFAULT '';

ALTER TABLE job_schedules ADD COLUMN IF NOT EXISTS last_job_id UUID;
ALTER TABLE job_schedules ADD COLUMN IF NOT EXISTS last_status VARCHAR(50);

CREATE INDEX IF NOT EXISTS idx_jobs_deleted_at ON jobs(deleted_at);
CREATE INDEX IF NOT EXISTS idx_jobs_user_created ON jobs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_scheduled ON jobs(user_id, deleted_at, status);
CREATE INDEX IF NOT EXISTS idx_job_schedules_active ON job_schedules(is_active, next_run);
CREATE INDEX IF NOT EXISTS idx_schedule_history_schedule ON schedule_history(schedule_id);
