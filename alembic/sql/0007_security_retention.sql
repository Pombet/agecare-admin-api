-- AgeCare canonical DDL, source sections 13, 14, 15, 16, 17
-- Keep this migration SQL immutable after deployment.

-- 13 · Historial (*_history) de las entidades mutables
-- ---------------------------------------------------------------------
CALL admin.enable_history('admin_users');
CALL admin.enable_history('ops_incidents');
CALL admin.enable_history('support_tickets');
CALL admin.enable_history('content_items');
CALL admin.enable_history('marketplace_caregivers');
CALL admin.enable_history('marketplace_products');
CALL admin.enable_history('moderation_items');
CALL admin.enable_history('system_settings');
CALL admin.enable_history('legal_versions');

-- ---------------------------------------------------------------------
-- 14 · Row-Level Security por tenant
--     La API ejecuta al inicio de cada transacción:
--       SET LOCAL app.tenant_id = '<uuid>'; SET LOCAL app.actor_id = '<uuid>';
--     Los jobs (agecare_admin_jobs, BYPASSRLS) ven todos los tenants.
--     El propietario del esquema (migraciones) queda exento (sin FORCE).
-- ---------------------------------------------------------------------
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT c.relname
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
     WHERE n.nspname = 'admin' AND c.relkind IN ('r','p') AND NOT c.relispartition
  LOOP
    EXECUTE format('ALTER TABLE admin.%I ENABLE ROW LEVEL SECURITY', r.relname);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON admin.%I', r.relname);
    EXECUTE format($p$CREATE POLICY tenant_isolation ON admin.%I
                     USING (tenant_id = admin.current_tenant_id())
                     WITH CHECK (tenant_id = admin.current_tenant_id())$p$, r.relname);
  END LOOP;
END $$;

-- ---------------------------------------------------------------------
-- 15 · Grants
-- ---------------------------------------------------------------------
GRANT USAGE ON SCHEMA admin TO agecare_admin_api, agecare_admin_jobs, agecare_admin_ro;

-- API: CRUD en tablas operativas, lectura en agregados y catálogos
GRANT SELECT ON ALL TABLES IN SCHEMA admin TO agecare_admin_api, agecare_admin_ro;
GRANT INSERT, UPDATE ON
  admin.tenant_counters, admin.admin_users, admin.admin_invitations, admin.admin_sessions,
  admin.ops_incidents, admin.support_tickets, admin.support_csat_surveys,
  admin.content_items, admin.marketplace_caregivers, admin.marketplace_products,
  admin.moderation_items, admin.moderation_escalations, admin.system_settings, admin.legal_versions
  TO agecare_admin_api;
GRANT INSERT ON admin.audit_log, admin.admin_login_attempts, admin.support_ticket_replies TO agecare_admin_api;
GRANT DELETE ON admin.admin_sessions, admin.admin_invitations TO agecare_admin_api;
-- Las tablas *_history reciben filas solo vía trigger (SECURITY DEFINER): sin grants de escritura.

-- Solo lectura: sin acceso a credenciales ni sesiones
REVOKE SELECT ON admin.admin_users, admin.admin_users_history, admin.admin_sessions, admin.admin_invitations,
                 admin.admin_login_attempts FROM agecare_admin_ro;
GRANT SELECT (id, tenant_id, full_name, email, role_code, is_active, last_login_at, created_at)
  ON admin.admin_users TO agecare_admin_ro;

-- Jobs: todo salvo borrar auditoría
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA admin TO agecare_admin_jobs;
REVOKE UPDATE, DELETE ON admin.audit_log FROM agecare_admin_jobs;
REVOKE DELETE ON admin.support_ticket_replies FROM agecare_admin_jobs;

GRANT USAGE ON ALL SEQUENCES IN SCHEMA admin TO agecare_admin_api, agecare_admin_jobs;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA admin TO agecare_admin_api, agecare_admin_jobs, agecare_admin_ro;

ALTER DEFAULT PRIVILEGES IN SCHEMA admin GRANT SELECT ON TABLES TO agecare_admin_api, agecare_admin_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA admin GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agecare_admin_jobs;

-- ---------------------------------------------------------------------
-- 16 · Particiones y retención
--     Job diario partition_maintenance: crea particiones futuras y elimina
--     las que exceden la retención. En Azure puede sustituirse por pg_partman.
-- ---------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE admin.ensure_partitions(p_table text, p_granularity text, p_ahead integer)
LANGUAGE plpgsql AS $$
DECLARE
  v_step  interval := CASE p_granularity WHEN 'day' THEN interval '1 day' WHEN 'month' THEN interval '1 month' END;
  v_start timestamptz := date_trunc(p_granularity, now());
  v_from  timestamptz; v_to timestamptz; v_name text;
BEGIN
  IF v_step IS NULL THEN RAISE EXCEPTION 'granularidad % no soportada', p_granularity; END IF;
  FOR i IN -1 .. p_ahead LOOP
    v_from := v_start + i * v_step;
    v_to   := v_from + v_step;
    v_name := p_table || '_p' || to_char(v_from, CASE p_granularity WHEN 'day' THEN 'YYYYMMDD' ELSE 'YYYYMM' END);
    EXECUTE format('CREATE TABLE IF NOT EXISTS admin.%I PARTITION OF admin.%I FOR VALUES FROM (%L) TO (%L)',
                   v_name, p_table, v_from, v_to);
  END LOOP;
END $$;

CREATE OR REPLACE PROCEDURE admin.drop_expired_partitions(p_table text, p_retention interval)
LANGUAGE plpgsql AS $$
DECLARE r record; v_upper timestamptz;
BEGIN
  FOR r IN
    SELECT c.relname, pg_get_expr(c.relpartbound, c.oid) AS bound
      FROM pg_inherits i
      JOIN pg_class c ON c.oid = i.inhrelid
      JOIN pg_class p ON p.oid = i.inhparent
      JOIN pg_namespace n ON n.oid = p.relnamespace
     WHERE n.nspname = 'admin' AND p.relname = p_table
  LOOP
    -- límite superior: FOR VALUES FROM ('…') TO ('…')
    v_upper := (regexp_match(r.bound, $re$TO \('([^']+)'\)$re$))[1]::timestamptz;
    IF v_upper < now() - p_retention THEN
      EXECUTE format('DROP TABLE admin.%I', r.relname);
    END IF;
  END LOOP;
END $$;

CREATE OR REPLACE PROCEDURE admin.partition_maintenance()
LANGUAGE plpgsql AS $$
BEGIN
  CALL admin.ensure_partitions('audit_log',                 'month', 2);
  CALL admin.ensure_partitions('feature_usage_events',      'month', 2);
  CALL admin.ensure_partitions('ops_component_checks',      'day',   7);
  CALL admin.ensure_partitions('ops_request_stats',         'day',   7);
  CALL admin.ensure_partitions('ops_critical_process_runs', 'day',   7);
  CALL admin.drop_expired_partitions('audit_log',                 interval '24 months');
  CALL admin.drop_expired_partitions('feature_usage_events',      interval '13 months');
  CALL admin.drop_expired_partitions('ops_component_checks',      interval '90 days');
  CALL admin.drop_expired_partitions('ops_request_stats',         interval '90 days');
  CALL admin.drop_expired_partitions('ops_critical_process_runs', interval '90 days');
  -- Retención por DELETE en tablas pequeñas no particionadas
  DELETE FROM admin.metrics_hourly_users    WHERE ts_hour      < now() - interval '7 days';
  DELETE FROM admin.admin_login_attempts    WHERE attempted_at < now() - interval '90 days';
  DELETE FROM admin.admin_sessions          WHERE expires_at   < now() - interval '30 days';
  DELETE FROM admin.job_runs                WHERE started_at   < now() - interval '180 days';
END $$;
COMMENT ON PROCEDURE admin.partition_maintenance() IS 'Ejecutar a diario (job partition_maintenance): crea particiones futuras y aplica la política de retención.';

CALL admin.partition_maintenance();

-- ---------------------------------------------------------------------
-- 17 · Datos semilla de catálogos (idempotentes)
-- ---------------------------------------------------------------------
INSERT INTO admin.admin_roles (code, name, description, requires_mfa_default, sort_order) VALUES
  ('admin',     'Administrador', 'Acceso total a la consola, gestión de staff y auditoría', true,  1),
  ('analyst',   'Analista',      'Lectura de métricas, estado operativo y tickets',          false, 2),
  ('support',   'Soporte',       'Métricas, incidentes y gestión de tickets',                false, 3),
  ('editor',    'Editor',        'Curación de contenido y catálogos del marketplace',        false, 4),
  ('moderator', 'Moderador',     'Cola de moderación y lectura del marketplace',             false, 5)
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description,
  requires_mfa_default = EXCLUDED.requires_mfa_default, sort_order = EXCLUDED.sort_order;

INSERT INTO admin.admin_role_permissions (role_code, module, access) VALUES
  ('admin','metrics','read'), ('admin','ops','write'), ('admin','support','write'), ('admin','content','write'),
  ('admin','marketplace','write'), ('admin','moderation','write'), ('admin','settings','write'),
  ('admin','legal','write'), ('admin','staff','write'), ('admin','audit','write'),
  ('analyst','metrics','read'), ('analyst','ops','read'), ('analyst','support','read'),
  ('support','metrics','read'), ('support','ops','write'), ('support','support','write'),
  ('editor','content','write'), ('editor','marketplace','write'),
  ('moderator','marketplace','read'), ('moderator','moderation','write')
ON CONFLICT DO NOTHING;

INSERT INTO admin.app_roles (code, name, sort_order) VALUES
  ('family','Familiar',1), ('caregiver','Cuidadora',2), ('elder','Adulto mayor',3), ('doctor','Médico',4)
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, sort_order = EXCLUDED.sort_order;

INSERT INTO admin.plans (code, name, billing_unit, is_paid, sort_order) VALUES
  ('free','Gratuito','none',false,1), ('gold','Dorado','user',true,2),
  ('platinum','Platino','family',true,3), ('provider','Proveedor','provider',true,4)
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, billing_unit = EXCLUDED.billing_unit, is_paid = EXCLUDED.is_paid;

INSERT INTO admin.components (key, name, is_async, latency_note, sort_order) VALUES
  ('api_core','API central',false,NULL,1),
  ('database','Base de datos',false,NULL,2),
  ('auth','Autenticación',false,NULL,3),
  ('push','Notificaciones push',true,'Latencia de entrega extremo a extremo',4),
  ('alert_engine','Motor de alertas',false,NULL,5),
  ('wearable_ingest','Ingesta wearables',false,NULL,6),
  ('ai_assistant','Asistente IA',true,'Latencia de generación extremo a extremo',7),
  ('storage','Almacenamiento',false,NULL,8),
  ('music_sync','Sync Director Musical',false,NULL,9)
ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, is_async = EXCLUDED.is_async, latency_note = EXCLUDED.latency_note;

INSERT INTO admin.critical_processes (key, name, chain, sort_order) VALUES
  ('fall_alert','Alerta de caída','Wearable → ingesta → motor de alertas → push familiar',1),
  ('sos','Botón SOS','App → API → push a círculo de cuidado',2),
  ('missed_medication','Alerta de medicamento omitido','Ventana de toma → job programado → push',3),
  ('ai_query','Consulta al asistente IA','App → API → LLM → respuesta',4),
  ('music_sync','Sincronización Director Musical','Tablet → API → almacenamiento → confirmación',5)
ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, chain = EXCLUDED.chain;

INSERT INTO admin.ticket_categories (code, name, sort_order) VALUES
  ('account_access','Acceso y cuenta',1), ('wearable_sync','Wearable y sincronización',2),
  ('alerts_push','Alertas y push',3), ('billing_plans','Pagos y planes',4),
  ('medications','Medicamentos',5), ('other','Otros',6)
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name;

INSERT INTO admin.ticket_status_transitions (from_status, to_status) VALUES
  ('open','in_progress'), ('in_progress','waiting_user'), ('in_progress','resolved'),
  ('waiting_user','in_progress'), ('resolved','closed'), ('resolved','in_progress')
ON CONFLICT DO NOTHING;

INSERT INTO admin.incident_status_transitions (from_status, to_status, is_maintenance) VALUES
  ('investigating','observing',false), ('investigating','resolved',false), ('observing','resolved',false),
  ('observing','completed',true), ('investigating','observing',true)
ON CONFLICT DO NOTHING;

INSERT INTO admin.features (key, name, expected_low, note, sort_order) VALUES
  ('home_traffic_light','Inicio / semáforo',false,'La promesa central del producto',1),
  ('medications','Medicamentos',false,NULL,2),
  ('checkin','Check-in diario',false,NULL,3),
  ('photos','Fotos',false,NULL,4),
  ('entertainment','Entretenimiento curado',false,NULL,5),
  ('ai_assistant','Asistente IA',false,NULL,6),
  ('marketplace','Marketplace',false,'Vitrina Could de v1',7),
  ('premium_reports','Reportes premium',false,'Función de pago',8),
  ('sos','SOS',true,'Uso bajo por diseño: evento de emergencia',9),
  ('music_director','Director Musical',false,NULL,10)
ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, expected_low = EXCLUDED.expected_low, note = EXCLUDED.note;

INSERT INTO admin.feature_roles (feature_key, app_role_code) VALUES
  ('home_traffic_light','family'), ('home_traffic_light','caregiver'),
  ('medications','family'), ('medications','caregiver'), ('medications','elder'), ('medications','doctor'),
  ('checkin','caregiver'), ('checkin','elder'),
  ('photos','family'), ('photos','elder'),
  ('entertainment','elder'),
  ('ai_assistant','family'), ('ai_assistant','caregiver'), ('ai_assistant','doctor'),
  ('marketplace','family'), ('marketplace','caregiver'),
  ('premium_reports','family'),
  ('sos','elder'), ('sos','caregiver'),
  ('music_director','caregiver'), ('music_director','elder')
ON CONFLICT DO NOTHING;

INSERT INTO admin.setting_definitions (key, description, value_schema, default_value, category, is_secret) VALUES
  ('plan_prices', 'Precio mensual por plan en la moneda del tenant',
   '{"type":"object","properties":{"gold":{"type":"integer","minimum":0},"platinum":{"type":"integer","minimum":0},"provider":{"type":"integer","minimum":0}},"required":["gold","platinum","provider"],"additionalProperties":false}',
   '{"gold":1000,"platinum":10000,"provider":5000}', 'plans', false),
  ('churn_definition', 'Días de inactividad para considerar churn además de la baja explícita',
   '{"type":"object","properties":{"inactive_days":{"type":"integer","minimum":7,"maximum":365}},"required":["inactive_days"]}',
   '{"inactive_days":60}', 'plans', false),
  ('allowed_email_domains', 'Dominios permitidos para cuentas de staff',
   '{"type":"array","items":{"type":"string","pattern":"^[a-z0-9.-]+\\.[a-z]{2,}$"},"minItems":1}',
   '["wellq.co.uk"]', 'security', false),
  ('ops_thresholds.api_core', 'Umbrales de degradación de API central',
   '{"type":"object","properties":{"p95_ms":{"type":"integer"},"error_rate":{"type":"number","minimum":0,"maximum":1}},"required":["p95_ms","error_rate"]}',
   '{"p95_ms":800,"error_rate":0.02}', 'ops', false),
  ('feature_adoption_low_threshold', 'Umbral de adopción baja por defecto',
   '{"type":"number","minimum":0.01,"maximum":0.5}', '0.15', 'flags', false)
ON CONFLICT (key) DO UPDATE SET description = EXCLUDED.description, value_schema = EXCLUDED.value_schema,
  default_value = EXCLUDED.default_value, category = EXCLUDED.category;

-- Fin del DDL
