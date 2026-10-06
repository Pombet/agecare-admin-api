-- AgeCare canonical DDL, source sections 0, 1, 2, 3
-- Keep this migration SQL immutable after deployment.

-- 0 · Extensiones y esquema
-- ---------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS citext;    -- correos sin distinguir mayúsculas
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- búsqueda ILIKE (q) con índices GIN

CREATE SCHEMA IF NOT EXISTS admin;
COMMENT ON SCHEMA admin IS 'Consola de Administración de AgeCare (staff, métricas agregadas, operación). Separado de las tablas de la app.';

SET search_path = admin, public;

-- ---------------------------------------------------------------------
-- 1 · Roles de base de datos (NOLOGIN; los usuarios reales heredan)
--     agecare_admin_api  : servicio FastAPI. Sujeto a RLS. Solo INSERT en audit_log.
--     agecare_admin_jobs : workers de agregación/monitor. Sin RLS (multi-tenant).
--     agecare_admin_ro   : BI / analistas SQL. Solo lectura, sujeto a RLS.
--   Los usuarios de login se crean aparte, p. ej.:
--     CREATE ROLE api_prod  LOGIN PASSWORD '…' IN ROLE agecare_admin_api;
--     CREATE ROLE jobs_prod LOGIN PASSWORD '…' BYPASSRLS IN ROLE agecare_admin_jobs;
--   Nota: BYPASSRLS es un atributo de rol y NO se hereda por pertenencia;
--   el usuario de login de los jobs debe declararlo explícitamente.
-- ---------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agecare_admin_api') THEN
    CREATE ROLE agecare_admin_api NOLOGIN NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agecare_admin_jobs') THEN
    CREATE ROLE agecare_admin_jobs NOLOGIN BYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agecare_admin_ro') THEN
    CREATE ROLE agecare_admin_ro NOLOGIN NOBYPASSRLS;
  END IF;
END $$;

-- ---------------------------------------------------------------------
-- 2 · Funciones comunes
-- ---------------------------------------------------------------------

-- Tenant de la petición actual (lo fija la API con SET LOCAL app.tenant_id = '…').
CREATE OR REPLACE FUNCTION admin.current_tenant_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid
$$;

-- Actor de la petición actual (SET LOCAL app.actor_id = '…'); NULL en jobs.
CREATE OR REPLACE FUNCTION admin.current_actor_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('app.actor_id', true), '')::uuid
$$;

-- Valida nombres IANA de zona horaria (declarada IMMUTABLE para poder usarse en CHECK)
CREATE OR REPLACE FUNCTION admin.is_valid_timezone(p_tz text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
  SELECT EXISTS (SELECT 1 FROM pg_timezone_names WHERE name = p_tz)
$$;

-- updated_at automático
CREATE OR REPLACE FUNCTION admin.tg_set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$;

-- Impide UPDATE/DELETE (tablas inmutables)
CREATE OR REPLACE FUNCTION admin.tg_forbid_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'La tabla %.% es inmutable: no admite %', TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP
    USING ERRCODE = 'insufficient_privilege';
END $$;

-- Historial genérico: copia cada versión de la fila (I/U → NEW, D → OLD)
-- en admin.<tabla>_history, que tiene las mismas columnas más metadatos.
CREATE OR REPLACE FUNCTION admin.tg_write_history() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = admin, pg_temp AS $$
DECLARE
  v_cols text;
  v_row  jsonb;
BEGIN
  v_row := CASE WHEN TG_OP = 'DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
  -- Los secretos nunca se copian al historial
  v_row := v_row - 'password_hash' - 'mfa_secret_enc' - 'refresh_token_hash' - 'token_hash';
  SELECT string_agg(quote_ident(attname), ',' ORDER BY attnum)
    INTO v_cols
    FROM pg_attribute
   WHERE attrelid = TG_RELID AND attnum > 0 AND NOT attisdropped;
  EXECUTE format(
    'INSERT INTO %I.%I (op, changed_at, changed_by, %s) SELECT $1, now(), $2, %s FROM jsonb_populate_record(NULL::%I.%I, $3) r',
    TG_TABLE_SCHEMA, TG_TABLE_NAME || '_history', v_cols,
    (SELECT string_agg('r.' || quote_ident(attname), ',' ORDER BY attnum)
       FROM pg_attribute WHERE attrelid = TG_RELID AND attnum > 0 AND NOT attisdropped),
    TG_TABLE_SCHEMA, TG_TABLE_NAME)
  USING left(TG_OP, 1), admin.current_actor_id(), v_row;
  RETURN NULL;
END $$;

-- Crea la tabla <t>_history a partir de <t> y engancha el trigger.
CREATE OR REPLACE PROCEDURE admin.enable_history(p_table text)
LANGUAGE plpgsql AS $$
BEGIN
  EXECUTE format($f$
    CREATE TABLE IF NOT EXISTS admin.%1$I (
      history_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      op          char(1)     NOT NULL CHECK (op IN ('I','U','D')),
      changed_at  timestamptz NOT NULL DEFAULT now(),
      changed_by  uuid,
      LIKE admin.%2$I
    )$f$, p_table || '_history', p_table);
  EXECUTE format('COMMENT ON TABLE admin.%I IS %L',
    p_table || '_history', 'Historial de versiones de admin.' || p_table || ' (una fila por INSERT/UPDATE/DELETE; changed_by = admin_users.id).');
  EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON admin.%I (tenant_id, changed_at)',
    p_table || '_history_tenant_time_idx', p_table || '_history');
  EXECUTE format('CREATE OR REPLACE TRIGGER trg_history AFTER INSERT OR UPDATE OR DELETE ON admin.%I FOR EACH ROW EXECUTE FUNCTION admin.tg_write_history()', p_table);
  -- El historial también es inmutable
  EXECUTE format('CREATE OR REPLACE TRIGGER trg_immutable BEFORE UPDATE OR DELETE ON admin.%I FOR EACH ROW EXECUTE FUNCTION admin.tg_forbid_change()', p_table || '_history');
END $$;

-- ---------------------------------------------------------------------
-- 3 · Tenants y catálogos globales
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.tenants (
  id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  code          varchar(20) NOT NULL UNIQUE CHECK (code ~ '^[a-z0-9-]+$'),
  name          varchar(120) NOT NULL,
  country_code  char(2)     NOT NULL CHECK (country_code ~ '^[A-Z]{2}$'),
  currency_code char(3)     NOT NULL CHECK (currency_code ~ '^[A-Z]{3}$'),
  timezone      varchar(40) NOT NULL,
  region        varchar(60),
  environment   varchar(20) NOT NULL DEFAULT 'production' CHECK (environment IN ('production','staging','development')),
  is_active     boolean     NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT tenants_timezone_valid CHECK (admin.is_valid_timezone(timezone))
);
COMMENT ON TABLE admin.tenants IS 'Despliegue/mercado administrado por la consola. Aísla sus datos por RLS (tenant_id).';
COMMENT ON COLUMN admin.tenants.currency_code IS 'ISO 4217. Todos los importes (mrr_amount, price_amount) del tenant se expresan en esta moneda, en unidades enteras (CLP sin decimales).';
COMMENT ON COLUMN admin.tenants.timezone IS 'Zona horaria de negocio con la que los jobs cortan los días y semanas (America/Santiago).';

CREATE TABLE IF NOT EXISTS admin.tenant_counters (
  tenant_id    uuid        NOT NULL REFERENCES admin.tenants(id),
  counter_name varchar(40) NOT NULL,
  next_value   bigint      NOT NULL DEFAULT 1,
  PRIMARY KEY (tenant_id, counter_name)
);
COMMENT ON TABLE admin.tenant_counters IS 'Correlativos por tenant (ticket_number). Se incrementa con bloqueo de fila en el trigger.';

CREATE OR REPLACE FUNCTION admin.next_counter(p_tenant uuid, p_name text) RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE v bigint;
BEGIN
  INSERT INTO admin.tenant_counters (tenant_id, counter_name, next_value)
       VALUES (p_tenant, p_name, 2)
  ON CONFLICT (tenant_id, counter_name)
  DO UPDATE SET next_value = admin.tenant_counters.next_value + 1
  RETURNING next_value - 1 INTO v;
  RETURN v;
END $$;

CREATE TABLE IF NOT EXISTS admin.admin_roles (
  code                 varchar(20) PRIMARY KEY,
  name                 varchar(60) NOT NULL,
  description          varchar(300),
  requires_mfa_default boolean     NOT NULL DEFAULT false,
  sort_order           smallint    NOT NULL DEFAULT 0
);
COMMENT ON TABLE admin.admin_roles IS 'Roles internos del staff (spec 2.3): admin, analyst, support, editor, moderator.';

CREATE TABLE IF NOT EXISTS admin.admin_role_permissions (
  role_code varchar(20) NOT NULL REFERENCES admin.admin_roles(code),
  module    varchar(30) NOT NULL CHECK (module IN ('metrics','ops','support','content','marketplace','moderation','settings','legal','staff','audit')),
  access    varchar(5)  NOT NULL CHECK (access IN ('read','write')),
  PRIMARY KEY (role_code, module)
);
COMMENT ON TABLE admin.admin_role_permissions IS 'Matriz de permisos por módulo (spec 2.3). read = L, write = E; sin fila = sin acceso.';

CREATE TABLE IF NOT EXISTS admin.app_roles (
  code       varchar(12) PRIMARY KEY,
  name       varchar(60) NOT NULL,
  sort_order smallint    NOT NULL DEFAULT 0
);
COMMENT ON TABLE admin.app_roles IS 'Perfiles de usuario final (AppRole): family, caregiver, elder, doctor.';

CREATE TABLE IF NOT EXISTS admin.plans (
  code         varchar(12) PRIMARY KEY,
  name         varchar(60) NOT NULL,
  billing_unit varchar(12) NOT NULL CHECK (billing_unit IN ('none','user','family','provider')),
  is_paid      boolean     NOT NULL,
  sort_order   smallint    NOT NULL DEFAULT 0
);
COMMENT ON TABLE admin.plans IS 'Catálogo de planes (PlanCode). Precios vigentes en system_settings.plan_prices; congelados en metrics_plan_snapshot.';

CREATE TABLE IF NOT EXISTS admin.components (
  key          varchar(30) PRIMARY KEY,
  name         varchar(80) NOT NULL,
  is_async     boolean     NOT NULL DEFAULT false,
  latency_note varchar(200),
  sort_order   smallint    NOT NULL DEFAULT 0
);
COMMENT ON TABLE admin.components IS 'Componentes monitorizados (ComponentKey). Umbrales en system_settings ops_thresholds.<key>.';

CREATE TABLE IF NOT EXISTS admin.critical_processes (
  key        varchar(40)  PRIMARY KEY,
  name       varchar(120) NOT NULL,
  chain      varchar(300) NOT NULL,
  sort_order smallint     NOT NULL DEFAULT 0
);
COMMENT ON TABLE admin.critical_processes IS 'Procesos críticos de negocio medidos extremo a extremo (spec 5.3).';

CREATE TABLE IF NOT EXISTS admin.ticket_categories (
  code       varchar(20) PRIMARY KEY,
  name       varchar(80) NOT NULL,
  sort_order smallint    NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS admin.features (
  key          varchar(50)  PRIMARY KEY,
  name         varchar(120) NOT NULL,
  expected_low boolean      NOT NULL DEFAULT false,
  note         varchar(300),
  sort_order   smallint     NOT NULL DEFAULT 0
);
COMMENT ON TABLE admin.features IS 'Catálogo de funcionalidades instrumentadas; se administra por seed/migración (spec 7).';
COMMENT ON COLUMN admin.features.expected_low IS 'true en funciones de emergencia (SOS): uso bajo correcto por diseño.';

CREATE TABLE IF NOT EXISTS admin.feature_roles (
  feature_key   varchar(50) NOT NULL REFERENCES admin.features(key) ON DELETE CASCADE,
  app_role_code varchar(12) NOT NULL REFERENCES admin.app_roles(code),
  PRIMARY KEY (feature_key, app_role_code)
);
COMMENT ON TABLE admin.feature_roles IS 'Perfiles a los que aplica cada función. Sin fila = null (celda gris) en la matriz de adopción.';

CREATE TABLE IF NOT EXISTS admin.setting_definitions (
  key           varchar(80)  PRIMARY KEY CHECK (key ~ '^[a-z0-9_.]+$'),
  description   varchar(300) NOT NULL,
  value_schema  jsonb        NOT NULL,
  default_value jsonb        NOT NULL,
  category      varchar(40)  NOT NULL,
  is_secret     boolean      NOT NULL DEFAULT false
);
COMMENT ON TABLE admin.setting_definitions IS 'Catálogo de claves de configuración con su JSON Schema (spec 12). Sin DELETE por API.';

CREATE TABLE IF NOT EXISTS admin.ticket_status_transitions (
  from_status varchar(15) NOT NULL,
  to_status   varchar(15) NOT NULL,
  PRIMARY KEY (from_status, to_status)
);
COMMENT ON TABLE admin.ticket_status_transitions IS 'Transiciones válidas de ticket (spec 8.4), aplicadas por trigger.';

CREATE TABLE IF NOT EXISTS admin.incident_status_transitions (
  from_status    varchar(15) NOT NULL,
  to_status      varchar(15) NOT NULL,
  is_maintenance boolean     NOT NULL,
  PRIMARY KEY (from_status, to_status, is_maintenance)
);
COMMENT ON TABLE admin.incident_status_transitions IS 'Transiciones válidas de incidente (spec 5.6), aplicadas por trigger.';

-- ---------------------------------------------------------------------
