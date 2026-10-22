-- Apply as the system_resources_monitoring PostgreSQL role.

CREATE TABLE IF NOT EXISTS public.resource_samples (
    sample_id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sampled_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cpu_percent             DOUBLE PRECISION,
    cpu_count               INTEGER NOT NULL,
    load_1                  DOUBLE PRECISION NOT NULL,
    load_5                  DOUBLE PRECISION NOT NULL,
    load_15                 DOUBLE PRECISION NOT NULL,
    memory_total_bytes      BIGINT NOT NULL,
    memory_available_bytes  BIGINT NOT NULL,
    swap_total_bytes        BIGINT NOT NULL,
    swap_free_bytes         BIGINT NOT NULL,
    root_fs_total_bytes     BIGINT NOT NULL,
    root_fs_used_bytes      BIGINT NOT NULL,
    root_fs_available_bytes BIGINT NOT NULL,
    uptime_seconds          DOUBLE PRECISION NOT NULL,
    CHECK (cpu_percent IS NULL OR (cpu_percent >= 0 AND cpu_percent <= 100)),
    CHECK (cpu_count > 0),
    CHECK (memory_total_bytes >= 0 AND memory_available_bytes >= 0),
    CHECK (swap_total_bytes >= 0 AND swap_free_bytes >= 0),
    CHECK (root_fs_total_bytes >= 0 AND root_fs_used_bytes >= 0 AND root_fs_available_bytes >= 0)
);

CREATE INDEX IF NOT EXISTS resource_samples_sampled_at_idx
    ON public.resource_samples (sampled_at DESC);

GRANT SELECT, INSERT, UPDATE, DELETE
    ON ALL TABLES IN SCHEMA public
    TO human;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO human;

GRANT SELECT
    ON ALL TABLES IN SCHEMA public
    TO assistant;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO assistant;

GRANT USAGE, SELECT, UPDATE
    ON ALL SEQUENCES IN SCHEMA public
    TO human;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO human;
