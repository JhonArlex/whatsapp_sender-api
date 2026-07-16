-- =============================================================
-- Migration 002: Web Sender - Login, Grupos Evolution, Jobs
-- =============================================================
-- ⚠️  IMPORTANTE: las tablas deben crearse en orden de dependencias
--    (una tabla con FK debe crearse DESPUÉS de la tabla referenciada)

-- 1. Tablas sin dependencias -------------------------------------------

-- Tabla de usuarios (base: sin FK)
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(255) NOT NULL DEFAULT '',
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Tabla de API tokens (legacy, sin FK)
CREATE TABLE IF NOT EXISTS api_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token VARCHAR(255) NOT NULL,
    activo BOOLEAN DEFAULT true,
    ultimo_uso TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Tabla de schedules (envíos programados, sin FK)
CREATE TABLE IF NOT EXISTS schedules (
    id UUID PRIMARY KEY,
    hora VARCHAR(5) NOT NULL,
    dias_semana JSONB DEFAULT '[]'::jsonb,
    desde_fila INT DEFAULT 1,
    activo BOOLEAN DEFAULT true,
    ultima_ejecucion TIMESTAMPTZ,
    creado TIMESTAMPTZ DEFAULT NOW()
);

-- Tabla de historial de schedules (sin FK)
CREATE TABLE IF NOT EXISTS schedule_history (
    id UUID PRIMARY KEY,
    schedule_id UUID,
    hora_programada VARCHAR(5) NOT NULL,
    ejecutado_en TIMESTAMPTZ DEFAULT NOW(),
    finalizado_en TIMESTAMPTZ,
    job_id UUID,
    estado VARCHAR(50) DEFAULT 'pendiente',
    detalle TEXT
);

-- 2. Tablas que dependen solo de users --------------------------------

-- Refresh tokens para JWT
CREATE TABLE IF NOT EXISTS refresh_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(255) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Plantillas de mensajes
CREATE TABLE IF NOT EXISTS message_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    msg_type VARCHAR(50) DEFAULT 'text',
    content TEXT DEFAULT '',
    media_url TEXT DEFAULT '',
    link_url TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Conexiones Evolution guardadas por usuario
CREATE TABLE IF NOT EXISTS evolution_connections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    base_url VARCHAR(512) NOT NULL,
    api_key_encrypted VARCHAR(512) NOT NULL,
    is_active BOOLEAN DEFAULT true,
    last_verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, name)
);

-- Jobs de envío masivo
CREATE TABLE IF NOT EXISTS jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    name VARCHAR(255) DEFAULT '',
    status VARCHAR(50) DEFAULT 'pending',
    total_groups INT DEFAULT 0,
    processed_groups INT DEFAULT 0,
    success_count INT DEFAULT 0,
    fail_count INT DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Tablas con dependencias encadenadas ------------------------------

-- Cache de instancias Evolution (depende de evolution_connections)
CREATE TABLE IF NOT EXISTS instances_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    connection_id UUID NOT NULL REFERENCES evolution_connections(id) ON DELETE CASCADE,
    instance_id VARCHAR(255) NOT NULL,
    instance_name VARCHAR(255) NOT NULL,
    connection_status VARCHAR(50) DEFAULT 'open',
    owner_jid VARCHAR(255),
    profile_name VARCHAR(255),
    profile_pic_url TEXT,
    integration VARCHAR(50) DEFAULT 'WHATSAPP_BUSINESS',
    token VARCHAR(512),
    client_name VARCHAR(255),
    synced_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(connection_id, instance_id)
);

-- Cache de grupos de WhatsApp (depende de instances_cache)
CREATE TABLE IF NOT EXISTS groups_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instance_cache_id UUID NOT NULL REFERENCES instances_cache(id) ON DELETE CASCADE,
    remote_jid VARCHAR(255) NOT NULL,
    push_name VARCHAR(512),
    subject VARCHAR(512),
    profile_pic_url TEXT,
    participants_count INT DEFAULT 0,
    labels TEXT[] DEFAULT '{}',
    synced_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(instance_cache_id, remote_jid)
);

-- Grupos seleccionados para un job (depende de jobs)
CREATE TABLE IF NOT EXISTS job_groups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    remote_jid VARCHAR(255) NOT NULL,
    push_name VARCHAR(512) DEFAULT '',
    instance_name VARCHAR(255) DEFAULT '',
    instance_token VARCHAR(512) DEFAULT '',
    evolution_base_url VARCHAR(512) DEFAULT '',
    status VARCHAR(50) DEFAULT 'pending',
    detail TEXT,
    sent_at TIMESTAMPTZ,
    UNIQUE(job_id, remote_jid)
);

-- Mensajes configurados para un job (depende de jobs)
CREATE TABLE IF NOT EXISTS job_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    msg_type VARCHAR(50) DEFAULT 'text',
    content TEXT DEFAULT '',
    media_base64 TEXT DEFAULT '',
    media_mimetype VARCHAR(100) DEFAULT '',
    file_name VARCHAR(255) DEFAULT '',
    sort_order INT DEFAULT 0
);

-- Programaciones de jobs (depende de jobs y users)
CREATE TABLE IF NOT EXISTS job_schedules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id),
    schedule_type VARCHAR(50) NOT NULL DEFAULT 'once',
    run_date DATE,
    run_time TIME,
    days_of_week TEXT[] DEFAULT '{}',
    interval_minutes INT DEFAULT 0,
    start_date DATE,
    end_date DATE,
    last_run TIMESTAMPTZ,
    next_run TIMESTAMPTZ,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Historial persistente de mensajes enviados (depende de jobs, job_groups, users)
CREATE TABLE IF NOT EXISTS message_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
    job_group_id UUID REFERENCES job_groups(id) ON DELETE SET NULL,
    user_id UUID REFERENCES users(id),
    remote_jid VARCHAR(255) NOT NULL,
    push_name VARCHAR(512) DEFAULT '',
    instance_name VARCHAR(255) DEFAULT '',
    msg_type VARCHAR(50) DEFAULT 'text',
    content TEXT DEFAULT '',
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    error_detail TEXT,
    evolution_message_id VARCHAR(255),
    sent_at TIMESTAMPTZ DEFAULT NOW()
);

-- Tabla para relacionar schedules con grupos (depende de schedules)
CREATE TABLE IF NOT EXISTS schedule_groups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    schedule_id UUID NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
    remote_jid VARCHAR(255) NOT NULL,
    push_name VARCHAR(512) DEFAULT '',
    instance_name VARCHAR(255) DEFAULT '',
    instance_token VARCHAR(512) DEFAULT '',
    evolution_base_url VARCHAR(512) DEFAULT ''
);

-- 4. Modificaciones a tablas existentes --------------------------------

-- Modificar schedules para soportar user_id y job_id
ALTER TABLE schedules ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id);
ALTER TABLE schedules ADD COLUMN IF NOT EXISTS job_id UUID REFERENCES jobs(id);
ALTER TABLE schedules ADD COLUMN IF NOT EXISTS message_template TEXT DEFAULT '';

-- Modificar schedule_history
ALTER TABLE schedule_history ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id);

-- Modificar job_schedules (si la tabla ya existe sin is_active)
ALTER TABLE job_schedules ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true;

-- 5. Índices -----------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_job_groups_job_id ON job_groups(job_id);
CREATE INDEX IF NOT EXISTS idx_job_groups_status ON job_groups(status);
CREATE INDEX IF NOT EXISTS idx_message_history_job_id ON message_history(job_id);
CREATE INDEX IF NOT EXISTS idx_message_history_user_id ON message_history(user_id);
CREATE INDEX IF NOT EXISTS idx_groups_cache_instance ON groups_cache(instance_cache_id);
CREATE INDEX IF NOT EXISTS idx_evolution_connections_user ON evolution_connections(user_id);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_hash ON refresh_tokens(token_hash);
