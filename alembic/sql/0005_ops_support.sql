-- AgeCare canonical DDL, source sections 7, 8
-- Keep this migration SQL immutable after deployment.

-- 7 · Estado operativo
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.ops_component_checks (
  tenant_id     uuid         NOT NULL REFERENCES admin.tenants(id),
  component_key varchar(30)  NOT NULL REFERENCES admin.components(key),
  checked_at    timestamptz  NOT NULL,
  ok            boolean      NOT NULL,
  latency_ms    integer      CHECK (latency_ms >= 0),
  error_rate    numeric(6,5) CHECK (error_rate BETWEEN 0 AND 1),
  detail        varchar(300),
  PRIMARY KEY (tenant_id, component_key, checked_at)
) PARTITION BY RANGE (checked_at);
COMMENT ON TABLE admin.ops_component_checks IS 'Health checks cada 60 s (spec 2.8). Particionada por día; retención 90 días.';

CREATE TABLE IF NOT EXISTS admin.ops_request_stats (
  tenant_id     uuid        NOT NULL REFERENCES admin.tenants(id),
  component_key varchar(30) NOT NULL REFERENCES admin.components(key),
  minute        timestamptz NOT NULL CHECK (date_trunc('minute', minute) = minute),
  p50_ms        integer     NOT NULL CHECK (p50_ms >= 0),
  p95_ms        integer     NOT NULL CHECK (p95_ms >= p50_ms),
  p99_ms        integer     CHECK (p99_ms >= p95_ms),
  sample_count  integer     NOT NULL CHECK (sample_count > 0),
  error_count   integer     NOT NULL DEFAULT 0 CHECK (error_count BETWEEN 0 AND sample_count),
  PRIMARY KEY (tenant_id, component_key, minute)
) PARTITION BY RANGE (minute);
COMMENT ON TABLE admin.ops_request_stats IS 'Percentiles de latencia por componente y minuto desde OpenTelemetry (spec 2.8). Particionada por día; retención 90 días.';

CREATE TABLE IF NOT EXISTS admin.ops_component_state (
  tenant_id            uuid         NOT NULL REFERENCES admin.tenants(id),
  component_key        varchar(30)  NOT NULL REFERENCES admin.components(key),
  status               varchar(15)  NOT NULL CHECK (status IN ('operational','degraded','outage')),
  status_since         timestamptz  NOT NULL,
  consecutive_failures smallint     NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
  consecutive_breaches smallint     NOT NULL DEFAULT 0 CHECK (consecutive_breaches >= 0),
  uptime_30d           numeric(6,5) NOT NULL DEFAULT 1 CHECK (uptime_30d BETWEEN 0 AND 1),
  latency_p50_ms       integer      CHECK (latency_p50_ms >= 0),
  latency_p95_ms       integer      CHECK (latency_p95_ms >= 0),
  note                 varchar(200),
  checked_at           timestamptz  NOT NULL,
  PRIMARY KEY (tenant_id, component_key)
);
COMMENT ON TABLE admin.ops_component_state IS 'Última foto de cada componente mantenida por el monitor (spec 5.1). degraded tras 3 ciclos sobre umbral; outage tras 3 fallos.';

CREATE TABLE IF NOT EXISTS admin.ops_incidents (
  id             uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid         NOT NULL REFERENCES admin.tenants(id),
  title          varchar(160) NOT NULL CHECK (length(title) BETWEEN 5 AND 160),
  component_key  varchar(30)  REFERENCES admin.components(key),
  severity       varchar(15)  NOT NULL CHECK (severity IN ('degraded','outage')),
  status         varchar(15)  NOT NULL CHECK (status IN ('investigating','observing','resolved','completed')),
  is_maintenance boolean      NOT NULL DEFAULT false,
  auto_created   boolean      NOT NULL DEFAULT false,
  description    text         NOT NULL CHECK (length(description) <= 4000),
  resolution     text         CHECK (length(resolution) <= 2000),
  started_at     timestamptz  NOT NULL,
  resolved_at    timestamptz,
  created_by     uuid         REFERENCES admin.admin_users(id),
  updated_by     uuid         REFERENCES admin.admin_users(id),
  created_at     timestamptz  NOT NULL DEFAULT now(),
  updated_at     timestamptz  NOT NULL DEFAULT now(),
  CONSTRAINT ops_incidents_resolution_ck CHECK (status NOT IN ('resolved','completed') OR (resolution IS NOT NULL AND resolved_at IS NOT NULL)),
  CONSTRAINT ops_incidents_completed_ck  CHECK (status <> 'completed' OR is_maintenance),
  CONSTRAINT ops_incidents_creator_ck    CHECK (auto_created OR created_by IS NOT NULL),
  CONSTRAINT ops_incidents_dates_ck      CHECK (resolved_at IS NULL OR resolved_at >= started_at)
);
CREATE INDEX IF NOT EXISTS ops_incidents_tenant_started_idx ON admin.ops_incidents (tenant_id, started_at DESC);
CREATE INDEX IF NOT EXISTS ops_incidents_tenant_status_idx  ON admin.ops_incidents (tenant_id, status);
CREATE INDEX IF NOT EXISTS ops_incidents_component_idx      ON admin.ops_incidents (tenant_id, component_key);
COMMENT ON TABLE admin.ops_incidents IS 'Incidentes y mantenimientos (spec 5.4–5.6). Historial en ops_incidents_history.';
CREATE OR REPLACE TRIGGER trg_updated_at BEFORE UPDATE ON admin.ops_incidents
  FOR EACH ROW EXECUTE FUNCTION admin.tg_set_updated_at();

CREATE OR REPLACE FUNCTION admin.tg_incident_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status <> OLD.status AND NOT EXISTS (
       SELECT 1 FROM admin.incident_status_transitions t
        WHERE t.from_status = OLD.status AND t.to_status = NEW.status AND t.is_maintenance = NEW.is_maintenance) THEN
    RAISE EXCEPTION 'INVALID_TRANSITION: incidente % → % no permitido', OLD.status, NEW.status
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status IN ('resolved','completed') THEN
    NEW.resolved_at := COALESCE(NEW.resolved_at, now());
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER trg_transition BEFORE UPDATE OF status ON admin.ops_incidents
  FOR EACH ROW EXECUTE FUNCTION admin.tg_incident_transition();

CREATE TABLE IF NOT EXISTS admin.ops_component_status_history (
  id            bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id     uuid        NOT NULL REFERENCES admin.tenants(id),
  component_key varchar(30) NOT NULL REFERENCES admin.components(key),
  from_status   varchar(15) CHECK (from_status IN ('operational','degraded','outage')),
  to_status     varchar(15) NOT NULL CHECK (to_status IN ('operational','degraded','outage')),
  changed_at    timestamptz NOT NULL DEFAULT now(),
  reason        varchar(300),
  incident_id   uuid        REFERENCES admin.ops_incidents(id)
);
CREATE INDEX IF NOT EXISTS ops_component_status_history_idx ON admin.ops_component_status_history (tenant_id, component_key, changed_at DESC);
COMMENT ON TABLE admin.ops_component_status_history IS 'Transiciones de estado por componente: base del uptime histórico y de los incidentes abiertos automáticamente.';

CREATE TABLE IF NOT EXISTS admin.ops_latency_window (
  tenant_id     uuid        NOT NULL REFERENCES admin.tenants(id),
  "window"      varchar(4)  NOT NULL CHECK ("window" IN ('1h','24h','7d')),
  component_key varchar(30) NOT NULL REFERENCES admin.components(key),
  p50_ms        integer     NOT NULL CHECK (p50_ms >= 0),
  p95_ms        integer     NOT NULL CHECK (p95_ms >= p50_ms),
  sample_count  integer     NOT NULL CHECK (sample_count >= 0),
  computed_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, "window", component_key)
);
COMMENT ON TABLE admin.ops_latency_window IS 'Percentiles agregados por ventana 1h/24h/7d desde ops_request_stats (spec 5.2). Refresco por minuto.';

CREATE TABLE IF NOT EXISTS admin.ops_critical_process_runs (
  tenant_id        uuid         NOT NULL REFERENCES admin.tenants(id),
  process_key      varchar(40)  NOT NULL REFERENCES admin.critical_processes(key),
  started_at       timestamptz  NOT NULL,
  duration_seconds numeric(9,3) NOT NULL CHECK (duration_seconds >= 0),
  succeeded        boolean      NOT NULL,
  is_synthetic     boolean      NOT NULL DEFAULT false,
  trace_id         varchar(64)
) PARTITION BY RANGE (started_at);
CREATE INDEX IF NOT EXISTS ops_critical_process_runs_idx ON admin.ops_critical_process_runs (tenant_id, process_key, started_at DESC);
COMMENT ON TABLE admin.ops_critical_process_runs IS 'Ejecuciones reales y sintéticas de procesos críticos (spec 5.3). Particionada por día; retención 90 días.';

CREATE TABLE IF NOT EXISTS admin.ops_critical_process_state (
  tenant_id          uuid         NOT NULL REFERENCES admin.tenants(id),
  process_key        varchar(40)  NOT NULL REFERENCES admin.critical_processes(key),
  p95_seconds        numeric(9,3) NOT NULL CHECK (p95_seconds >= 0),
  success_24h        numeric(6,5) NOT NULL CHECK (success_24h BETWEEN 0 AND 1),
  status             varchar(15)  NOT NULL CHECK (status IN ('operational','degraded','outage')),
  based_on_synthetic boolean      NOT NULL DEFAULT false,
  last_run_at        timestamptz,
  computed_at        timestamptz  NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, process_key)
);
COMMENT ON TABLE admin.ops_critical_process_state IS 'Estado calculado de cada proceso crítico (spec 5.3).';

-- ---------------------------------------------------------------------
-- 8 · Soporte
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.support_tickets (
  id                  uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid         NOT NULL REFERENCES admin.tenants(id),
  number              bigint       NOT NULL,
  subject             varchar(160) NOT NULL CHECK (length(subject) BETWEEN 5 AND 160),
  description         text         NOT NULL CHECK (length(description) <= 8000),
  requester_user_id   uuid,
  requester_name      varchar(120) NOT NULL,
  requester_email     citext       NOT NULL,
  requester_role_code varchar(12)  REFERENCES admin.app_roles(code),
  requester_plan_code varchar(12)  REFERENCES admin.plans(code),
  requester_context   jsonb,
  category_code       varchar(20)  NOT NULL REFERENCES admin.ticket_categories(code),
  priority            varchar(10)  NOT NULL DEFAULT 'medium' CHECK (priority IN ('low','medium','high','critical')),
  status              varchar(15)  NOT NULL DEFAULT 'open'   CHECK (status IN ('open','in_progress','waiting_user','resolved','closed')),
  channel             varchar(10)  NOT NULL DEFAULT 'app'    CHECK (channel IN ('app','email','phone','console')),
  assigned_to         uuid         REFERENCES admin.admin_users(id),
  first_response_at   timestamptz,
  resolved_at         timestamptz,
  closed_at           timestamptz,
  reopen_count        smallint     NOT NULL DEFAULT 0 CHECK (reopen_count >= 0),
  created_by          uuid         REFERENCES admin.admin_users(id),
  created_at          timestamptz  NOT NULL DEFAULT now(),
  updated_at          timestamptz  NOT NULL DEFAULT now(),
  CONSTRAINT support_tickets_number_uq   UNIQUE (tenant_id, number),
  CONSTRAINT support_tickets_resolved_ck CHECK (status NOT IN ('resolved','closed') OR resolved_at IS NOT NULL),
  CONSTRAINT support_tickets_closed_ck   CHECK ((status = 'closed') = (closed_at IS NOT NULL)),
  CONSTRAINT support_tickets_console_ck  CHECK (channel <> 'console' OR created_by IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS support_tickets_status_idx    ON admin.support_tickets (tenant_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS support_tickets_assignee_idx  ON admin.support_tickets (tenant_id, assigned_to) WHERE assigned_to IS NOT NULL;
CREATE INDEX IF NOT EXISTS support_tickets_category_idx  ON admin.support_tickets (tenant_id, category_code, created_at DESC);
CREATE INDEX IF NOT EXISTS support_tickets_requester_idx ON admin.support_tickets (tenant_id, requester_user_id) WHERE requester_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS support_tickets_created_idx   ON admin.support_tickets (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS support_tickets_priority_idx  ON admin.support_tickets (tenant_id, priority, created_at DESC);
CREATE INDEX IF NOT EXISTS support_tickets_search_idx    ON admin.support_tickets USING gin ((number::text || ' ' || subject || ' ' || requester_email::text) gin_trgm_ops);
COMMENT ON TABLE admin.support_tickets IS 'Cola de soporte (spec 8). number = correlativo visible por tenant (#1482). Historial en support_tickets_history.';
COMMENT ON COLUMN admin.support_tickets.requester_user_id IS 'Referencia lógica a app.users.id; NULL si el ticket se creó sin cuenta enlazada (confirm_unlinked).';
COMMENT ON COLUMN admin.support_tickets.requester_context IS 'Snapshot del contexto del solicitante: {patients:[{patient_id,display_name}], devices:[{platform,app_version,last_seen_at}]}.';
COMMENT ON COLUMN admin.support_tickets.first_response_at IS 'Fijado por trigger con la primera respuesta no interna de un admin; base del KPI de primera respuesta.';

CREATE OR REPLACE TRIGGER trg_updated_at BEFORE UPDATE ON admin.support_tickets
  FOR EACH ROW EXECUTE FUNCTION admin.tg_set_updated_at();

-- Número correlativo por tenant
CREATE OR REPLACE FUNCTION admin.tg_ticket_number() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.number IS NULL THEN
    NEW.number := admin.next_counter(NEW.tenant_id, 'ticket_number');
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER trg_ticket_number BEFORE INSERT ON admin.support_tickets
  FOR EACH ROW EXECUTE FUNCTION admin.tg_ticket_number();

-- Máquina de estados + marcas temporales derivadas
CREATE OR REPLACE FUNCTION admin.tg_ticket_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status <> OLD.status THEN
    IF NOT EXISTS (SELECT 1 FROM admin.ticket_status_transitions t
                    WHERE t.from_status = OLD.status AND t.to_status = NEW.status) THEN
      RAISE EXCEPTION 'INVALID_TRANSITION: ticket % → % no permitido', OLD.status, NEW.status
        USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'resolved' THEN NEW.resolved_at := COALESCE(NEW.resolved_at, now()); END IF;
    IF NEW.status = 'closed'   THEN NEW.closed_at   := COALESCE(NEW.closed_at, now());   END IF;
    IF OLD.status = 'resolved' AND NEW.status = 'in_progress' THEN
      NEW.reopen_count := OLD.reopen_count + 1;
      NEW.resolved_at  := NULL;
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER trg_transition BEFORE UPDATE OF status ON admin.support_tickets
  FOR EACH ROW EXECUTE FUNCTION admin.tg_ticket_transition();

CREATE TABLE IF NOT EXISTS admin.support_ticket_replies (
  id              uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid         NOT NULL REFERENCES admin.tenants(id),
  ticket_id       uuid         NOT NULL REFERENCES admin.support_tickets(id) ON DELETE CASCADE,
  author_type     varchar(10)  NOT NULL CHECK (author_type IN ('admin','user','system')),
  author_admin_id uuid         REFERENCES admin.admin_users(id),
  author_user_id  uuid,
  author_name     varchar(120) NOT NULL,
  body            text         NOT NULL CHECK (length(body) BETWEEN 1 AND 8000),
  is_internal     boolean      NOT NULL DEFAULT false,
  created_at      timestamptz  NOT NULL DEFAULT now(),
  CONSTRAINT support_ticket_replies_author_ck CHECK (
    (author_type = 'admin'  AND author_admin_id IS NOT NULL) OR
    (author_type = 'user'   AND author_user_id  IS NOT NULL) OR
    (author_type = 'system')),
  CONSTRAINT support_ticket_replies_internal_ck CHECK (NOT is_internal OR author_type = 'admin')
);
CREATE INDEX IF NOT EXISTS support_ticket_replies_ticket_idx ON admin.support_ticket_replies (ticket_id, created_at);
COMMENT ON TABLE admin.support_ticket_replies IS 'Conversación del ticket (spec 8.5/8.6): respuestas de admin, mensajes del usuario y del sistema. Inmutable.';
CREATE OR REPLACE TRIGGER trg_immutable BEFORE UPDATE OR DELETE ON admin.support_ticket_replies
  FOR EACH ROW EXECUTE FUNCTION admin.tg_forbid_change();

-- Primera respuesta pública de un admin fija first_response_at; no se responde a tickets cerrados
CREATE OR REPLACE FUNCTION admin.tg_ticket_reply_after_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_status text;
BEGIN
  SELECT status INTO v_status FROM admin.support_tickets WHERE id = NEW.ticket_id FOR UPDATE;
  IF v_status = 'closed' THEN
    RAISE EXCEPTION 'TICKET_CLOSED: el ticket está cerrado y no admite nuevas respuestas' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.author_type = 'admin' AND NOT NEW.is_internal THEN
    UPDATE admin.support_tickets SET first_response_at = NEW.created_at
     WHERE id = NEW.ticket_id AND first_response_at IS NULL;
  END IF;
  RETURN NULL;
END $$;
CREATE OR REPLACE TRIGGER trg_reply_after_insert AFTER INSERT ON admin.support_ticket_replies
  FOR EACH ROW EXECUTE FUNCTION admin.tg_ticket_reply_after_insert();

CREATE TABLE IF NOT EXISTS admin.support_csat_surveys (
  id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid        NOT NULL REFERENCES admin.tenants(id),
  ticket_id    uuid        NOT NULL UNIQUE REFERENCES admin.support_tickets(id) ON DELETE CASCADE,
  sent_at      timestamptz NOT NULL DEFAULT now(),
  responded_at timestamptz,
  score        smallint    CHECK (score BETWEEN 1 AND 5),
  comment      text,
  CONSTRAINT support_csat_surveys_response_ck CHECK ((responded_at IS NULL) = (score IS NULL))
);
CREATE INDEX IF NOT EXISTS support_csat_surveys_responded_idx ON admin.support_csat_surveys (tenant_id, responded_at) WHERE responded_at IS NOT NULL;
COMMENT ON TABLE admin.support_csat_surveys IS 'Encuesta CSAT enviada al resolver (spec 8.4). Una por ticket.';

CREATE TABLE IF NOT EXISTS admin.support_metrics_daily (
  tenant_id                  uuid        NOT NULL REFERENCES admin.tenants(id),
  day                        date        NOT NULL,
  tickets_created            integer     NOT NULL DEFAULT 0 CHECK (tickets_created >= 0),
  tickets_resolved           integer     NOT NULL DEFAULT 0 CHECK (tickets_resolved >= 0),
  tickets_closed             integer     NOT NULL DEFAULT 0 CHECK (tickets_closed >= 0),
  first_response_seconds_avg integer     CHECK (first_response_seconds_avg >= 0),
  resolution_seconds_avg     integer     CHECK (resolution_seconds_avg >= 0),
  csat_sum                   integer     NOT NULL DEFAULT 0 CHECK (csat_sum >= 0),
  csat_count                 integer     NOT NULL DEFAULT 0 CHECK (csat_count >= 0),
  computed_at                timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, day)
);
COMMENT ON TABLE admin.support_metrics_daily IS 'Agregado diario de soporte (spec 2.8 / 6.3). Los contadores por estado se leen en vivo de support_tickets.';

-- ---------------------------------------------------------------------
