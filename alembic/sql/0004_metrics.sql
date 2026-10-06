-- AgeCare canonical DDL, source sections 5, 6
-- Keep this migration SQL immutable after deployment.

-- 5 · Métricas comerciales (agregados por jobs)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.store_downloads_daily (
  tenant_id   uuid        NOT NULL REFERENCES admin.tenants(id),
  day         date        NOT NULL,
  store       varchar(10) NOT NULL CHECK (store IN ('ios','android')),
  downloads   integer     NOT NULL DEFAULT 0 CHECK (downloads >= 0),
  ingested_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, day, store)
);
COMMENT ON TABLE admin.store_downloads_daily IS 'Descargas diarias ingeridas de App Store Connect y Google Play Console (spec 2.8).';

CREATE TABLE IF NOT EXISTS admin.metrics_daily_users (
  tenant_id        uuid        NOT NULL REFERENCES admin.tenants(id),
  day              date        NOT NULL,
  signups          integer     NOT NULL DEFAULT 0 CHECK (signups >= 0),
  cancellations    integer     NOT NULL DEFAULT 0 CHECK (cancellations >= 0),
  churned_users    integer     NOT NULL DEFAULT 0 CHECK (churned_users >= 0),
  downloads        integer     NOT NULL DEFAULT 0 CHECK (downloads >= 0),
  active_users_eod integer     NOT NULL DEFAULT 0 CHECK (active_users_eod >= 0),
  paying_users_eod integer     NOT NULL DEFAULT 0 CHECK (paying_users_eod >= 0),
  mrr_amount       bigint      NOT NULL DEFAULT 0 CHECK (mrr_amount >= 0),
  is_final         boolean     NOT NULL DEFAULT false,
  computed_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, day)
);
COMMENT ON TABLE admin.metrics_daily_users IS 'Serie diaria de Uso comercial (spec 4). Job 03:00 consolida el día anterior (is_final); job horario refresca el día en curso.';
COMMENT ON COLUMN admin.metrics_daily_users.churned_users IS 'Bajas explícitas + inactivos según churn_definition (system_settings).';
COMMENT ON COLUMN admin.metrics_daily_users.mrr_amount IS 'MRR al cierre del día en la moneda del tenant (CLP en Chile).';

CREATE TABLE IF NOT EXISTS admin.metrics_hourly_users (
  tenant_id     uuid        NOT NULL REFERENCES admin.tenants(id),
  ts_hour       timestamptz NOT NULL CHECK (date_trunc('hour', ts_hour) = ts_hour),
  signups       integer     NOT NULL DEFAULT 0 CHECK (signups >= 0),
  cancellations integer     NOT NULL DEFAULT 0 CHECK (cancellations >= 0),
  computed_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, ts_hour)
);
COMMENT ON TABLE admin.metrics_hourly_users IS 'Buckets horarios para el periodo today (spec 4.2). Retención 7 días.';

CREATE TABLE IF NOT EXISTS admin.metrics_plan_snapshot (
  tenant_id     uuid         NOT NULL REFERENCES admin.tenants(id),
  as_of         date         NOT NULL,
  plan_code     varchar(12)  NOT NULL REFERENCES admin.plans(code),
  users         integer      NOT NULL CHECK (users >= 0),
  price_amount  integer      CHECK (price_amount >= 0),
  mrr_amount    bigint       NOT NULL DEFAULT 0 CHECK (mrr_amount >= 0),
  monthly_churn numeric(6,5) NOT NULL DEFAULT 0 CHECK (monthly_churn BETWEEN 0 AND 1),
  computed_at   timestamptz  NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, as_of, plan_code)
);
COMMENT ON TABLE admin.metrics_plan_snapshot IS 'Mezcla de planes (spec 4.3). Snapshot diario; price_amount congela el precio vigente.';

CREATE TABLE IF NOT EXISTS admin.metrics_funnel_snapshot (
  tenant_id       uuid        NOT NULL REFERENCES admin.tenants(id),
  as_of           date        NOT NULL,
  downloads_total bigint      NOT NULL CHECK (downloads_total >= 0),
  accounts_total  bigint      NOT NULL CHECK (accounts_total >= 0),
  active_30d      integer     NOT NULL CHECK (active_30d >= 0),
  paying          integer     NOT NULL CHECK (paying >= 0),
  computed_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, as_of)
);
COMMENT ON TABLE admin.metrics_funnel_snapshot IS 'Embudo acumulado a la fecha (spec 4.4).';

-- ---------------------------------------------------------------------
-- 6 · Perfiles y adopción de funcionalidades
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.role_activity_window (
  tenant_id           uuid         NOT NULL REFERENCES admin.tenants(id),
  days_window         smallint     NOT NULL CHECK (days_window BETWEEN 7 AND 90),
  app_role_code       varchar(12)  NOT NULL REFERENCES admin.app_roles(code),
  active_users        integer      NOT NULL CHECK (active_users >= 0),
  growth_8w           numeric(7,4) NOT NULL DEFAULT 0,
  sessions_per_week   numeric(7,2) NOT NULL DEFAULT 0 CHECK (sessions_per_week >= 0),
  avg_session_seconds integer      NOT NULL DEFAULT 0 CHECK (avg_session_seconds >= 0),
  retention_30d       numeric(6,5) NOT NULL DEFAULT 0 CHECK (retention_30d BETWEEN 0 AND 1),
  computed_at         timestamptz  NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, days_window, app_role_code)
);
COMMENT ON TABLE admin.role_activity_window IS 'Intensidad de uso por perfil (spec 6.1). Ventanas precalculadas 7/14/30/60/90 días; la API elige la ventana ≥ days pedida.';

CREATE TABLE IF NOT EXISTS admin.role_weekly_active (
  tenant_id     uuid        NOT NULL REFERENCES admin.tenants(id),
  week_start    date        NOT NULL CHECK (extract(isodow FROM week_start) = 1),
  app_role_code varchar(12) NOT NULL REFERENCES admin.app_roles(code),
  active_users  integer     NOT NULL CHECK (active_users >= 0),
  is_partial    boolean     NOT NULL DEFAULT false,
  computed_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, week_start, app_role_code)
);
COMMENT ON TABLE admin.role_weekly_active IS 'Activos semanales por perfil (spec 6.2). week_start = lunes ISO.';

CREATE TABLE IF NOT EXISTS admin.feature_usage_events (
  event_id      uuid        NOT NULL DEFAULT gen_random_uuid(),
  tenant_id     uuid        NOT NULL REFERENCES admin.tenants(id),
  occurred_at   timestamptz NOT NULL,
  user_id       uuid        NOT NULL,
  app_role_code varchar(12) NOT NULL REFERENCES admin.app_roles(code),
  feature_key   varchar(50) NOT NULL REFERENCES admin.features(key),
  session_id    uuid,
  platform      varchar(10) CHECK (platform IN ('ios','android','web')),
  app_version   varchar(20),
  PRIMARY KEY (event_id, occurred_at)
) PARTITION BY RANGE (occurred_at);
CREATE INDEX IF NOT EXISTS feature_usage_events_feature_idx ON admin.feature_usage_events (tenant_id, occurred_at, feature_key);
CREATE INDEX IF NOT EXISTS feature_usage_events_user_idx    ON admin.feature_usage_events (tenant_id, user_id, occurred_at);
COMMENT ON TABLE admin.feature_usage_events IS 'Eventos crudos de uso emitidos por la app (spec 7). Particionada por mes; retención 13 meses. Solo INSERT.';
COMMENT ON COLUMN admin.feature_usage_events.user_id IS 'Referencia lógica a app.users.id (sin FK: otra BD/esquema).';
CREATE OR REPLACE TRIGGER trg_immutable BEFORE UPDATE OR DELETE ON admin.feature_usage_events
  FOR EACH ROW EXECUTE FUNCTION admin.tg_forbid_change();

CREATE TABLE IF NOT EXISTS admin.feature_usage_daily (
  tenant_id     uuid        NOT NULL REFERENCES admin.tenants(id),
  day           date        NOT NULL,
  feature_key   varchar(50) NOT NULL REFERENCES admin.features(key),
  app_role_code varchar(12) NOT NULL REFERENCES admin.app_roles(code),
  unique_users  integer     NOT NULL CHECK (unique_users >= 0),
  events        integer     NOT NULL CHECK (events >= unique_users),
  computed_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, day, feature_key, app_role_code)
);
COMMENT ON TABLE admin.feature_usage_daily IS 'Usuarios únicos por función, rol y día (spec 2.8), derivada de feature_usage_events.';

CREATE TABLE IF NOT EXISTS admin.feature_usage_window (
  tenant_id         uuid         NOT NULL REFERENCES admin.tenants(id),
  days_window       smallint     NOT NULL CHECK (days_window IN (7,30,90)),
  feature_key       varchar(50)  NOT NULL REFERENCES admin.features(key),
  app_role_code     varchar(12)  NOT NULL REFERENCES admin.app_roles(code),
  users             integer      NOT NULL CHECK (users >= 0),
  role_active_users integer      NOT NULL CHECK (role_active_users >= 0),
  adoption          numeric(6,5) GENERATED ALWAYS AS (
                      CASE WHEN role_active_users > 0
                           THEN LEAST(users::numeric / role_active_users, 1) ELSE 0 END) STORED,
  computed_at       timestamptz  NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, days_window, feature_key, app_role_code),
  FOREIGN KEY (feature_key, app_role_code) REFERENCES admin.feature_roles(feature_key, app_role_code)
);
COMMENT ON TABLE admin.feature_usage_window IS 'Matriz de adopción (spec 7). Solo pares (función, rol) de feature_roles; ausencia de fila = null en la API.';
COMMENT ON COLUMN admin.feature_usage_window.adoption IS 'Generada: users / role_active_users (0–1).';

-- ---------------------------------------------------------------------
