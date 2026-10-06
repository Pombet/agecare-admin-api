-- AgeCare canonical DDL, source sections 9, 10, 11, 12
-- Keep this migration SQL immutable after deployment.

-- 9 · Curación de contenido y marketplace
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.content_items (
  id            uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid         NOT NULL REFERENCES admin.tenants(id),
  type          varchar(10)  NOT NULL CHECK (type IN ('joke','news')),
  title         varchar(160) NOT NULL CHECK (length(title) BETWEEN 3 AND 160),
  body          text         NOT NULL CHECK (length(body) BETWEEN 10 AND 4000),
  tags          text[]       NOT NULL DEFAULT '{}' CHECK (cardinality(tags) <= 10),
  status        varchar(12)  NOT NULL DEFAULT 'draft'   CHECK (status IN ('draft','published','archived')),
  tts_status    varchar(10)  NOT NULL DEFAULT 'pending' CHECK (tts_status IN ('pending','ready','failed')),
  tts_audio_url varchar(500),
  publish_at    timestamptz,
  published_at  timestamptz,
  archived_at   timestamptz,
  deleted_at    timestamptz,
  created_by    uuid         NOT NULL REFERENCES admin.admin_users(id),
  updated_by    uuid         REFERENCES admin.admin_users(id),
  created_at    timestamptz  NOT NULL DEFAULT now(),
  updated_at    timestamptz  NOT NULL DEFAULT now(),
  CONSTRAINT content_items_published_ck CHECK (status <> 'published' OR (published_at IS NOT NULL AND tts_status = 'ready')),
  CONSTRAINT content_items_archived_ck  CHECK (status <> 'archived'  OR archived_at IS NOT NULL),
  CONSTRAINT content_items_deleted_ck   CHECK (deleted_at IS NULL OR status <> 'published')
);
CREATE INDEX IF NOT EXISTS content_items_type_status_idx ON admin.content_items (tenant_id, type, status) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS content_items_schedule_idx    ON admin.content_items (tenant_id, publish_at) WHERE status = 'draft' AND publish_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS content_items_tags_idx        ON admin.content_items USING gin (tags);
CREATE INDEX IF NOT EXISTS content_items_search_idx      ON admin.content_items USING gin ((title || ' ' || body) gin_trgm_ops);
COMMENT ON TABLE admin.content_items IS 'Chistes y noticias curados (spec 9). El feed de la app lee status = published AND deleted_at IS NULL. Borrado lógico. Historial en content_items_history.';
CREATE OR REPLACE TRIGGER trg_updated_at BEFORE UPDATE ON admin.content_items
  FOR EACH ROW EXECUTE FUNCTION admin.tg_set_updated_at();

CREATE TABLE IF NOT EXISTS admin.marketplace_caregivers (
  caregiver_id                  uuid         PRIMARY KEY,
  tenant_id                     uuid         NOT NULL REFERENCES admin.tenants(id),
  display_name                  varchar(120) NOT NULL,
  email                         citext       NOT NULL,
  zone                          varchar(80)  NOT NULL,
  specialties                   text[]       NOT NULL DEFAULT '{}',
  languages                     text[]       NOT NULL DEFAULT '{}',
  certifications_count          smallint     NOT NULL DEFAULT 0 CHECK (certifications_count >= 0),
  certifications_verified_count smallint     NOT NULL DEFAULT 0 CHECK (certifications_verified_count BETWEEN 0 AND certifications_count),
  rating_avg                    numeric(3,2) CHECK (rating_avg BETWEEN 1 AND 5),
  reviews_count                 integer      NOT NULL DEFAULT 0 CHECK (reviews_count >= 0),
  status                        varchar(12)  NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','suspended')),
  status_reason                 varchar(1000) CHECK (status_reason IS NULL OR length(status_reason) >= 10),
  internal_note                 text         CHECK (length(internal_note) <= 2000),
  submitted_at                  timestamptz  NOT NULL,
  reviewed_by                   uuid         REFERENCES admin.admin_users(id),
  reviewed_at                   timestamptz,
  synced_at                     timestamptz  NOT NULL DEFAULT now(),
  updated_at                    timestamptz  NOT NULL DEFAULT now(),
  CONSTRAINT marketplace_caregivers_approved_ck  CHECK (status <> 'approved'  OR certifications_verified_count >= 1),
  CONSTRAINT marketplace_caregivers_suspended_ck CHECK (status <> 'suspended' OR status_reason IS NOT NULL),
  CONSTRAINT marketplace_caregivers_reviewed_ck  CHECK (status = 'pending' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS marketplace_caregivers_status_idx ON admin.marketplace_caregivers (tenant_id, status, submitted_at DESC);
CREATE INDEX IF NOT EXISTS marketplace_caregivers_zone_idx   ON admin.marketplace_caregivers (tenant_id, zone);
CREATE INDEX IF NOT EXISTS marketplace_caregivers_spec_idx   ON admin.marketplace_caregivers USING gin (specialties);
CREATE INDEX IF NOT EXISTS marketplace_caregivers_search_idx ON admin.marketplace_caregivers USING gin ((display_name || ' ' || email::text) gin_trgm_ops);
COMMENT ON TABLE admin.marketplace_caregivers IS 'Estado de publicación en la vitrina de cada perfil de cuidadora (spec 10.1/10.2). caregiver_id = app.caregivers.id (referencia lógica). Campos descriptivos sincronizados desde la app. Historial en marketplace_caregivers_history.';
CREATE OR REPLACE TRIGGER trg_updated_at BEFORE UPDATE ON admin.marketplace_caregivers
  FOR EACH ROW EXECUTE FUNCTION admin.tg_set_updated_at();

CREATE TABLE IF NOT EXISTS admin.marketplace_products (
  id           uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid         NOT NULL REFERENCES admin.tenants(id),
  name         varchar(160) NOT NULL CHECK (length(name) BETWEEN 3 AND 160),
  category     varchar(60)  NOT NULL CHECK (length(category) BETWEEN 2 AND 60),
  vendor       varchar(120) NOT NULL CHECK (length(vendor) BETWEEN 2 AND 120),
  price_amount integer      CHECK (price_amount >= 0),
  external_url varchar(500) NOT NULL CHECK (external_url ~* '^https://'),
  image_url    varchar(500) CHECK (image_url IS NULL OR image_url ~* '^https?://'),
  status       varchar(12)  NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','archived')),
  published_at timestamptz,
  archived_at  timestamptz,
  created_by   uuid         NOT NULL REFERENCES admin.admin_users(id),
  updated_by   uuid         REFERENCES admin.admin_users(id),
  created_at   timestamptz  NOT NULL DEFAULT now(),
  updated_at   timestamptz  NOT NULL DEFAULT now(),
  CONSTRAINT marketplace_products_published_ck CHECK (status <> 'published' OR published_at IS NOT NULL),
  CONSTRAINT marketplace_products_archived_ck  CHECK (status <> 'archived'  OR archived_at  IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS marketplace_products_status_idx ON admin.marketplace_products (tenant_id, status, category);
CREATE INDEX IF NOT EXISTS marketplace_products_search_idx ON admin.marketplace_products USING gin ((name || ' ' || vendor) gin_trgm_ops);
COMMENT ON TABLE admin.marketplace_products IS 'Catálogo de artículos de apoyo sin transacciones en v1 (spec 10.3–10.5). Historial en marketplace_products_history.';
CREATE OR REPLACE TRIGGER trg_updated_at BEFORE UPDATE ON admin.marketplace_products
  FOR EACH ROW EXECUTE FUNCTION admin.tg_set_updated_at();

-- ---------------------------------------------------------------------
-- 10 · Moderación
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.moderation_items (
  id                  uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid         NOT NULL REFERENCES admin.tenants(id),
  item_type           varchar(20)  NOT NULL CHECK (item_type IN ('review','caregiver_profile','photo','chat_message')),
  source_entity_id    uuid         NOT NULL,
  content_snapshot    jsonb        NOT NULL,
  author_user_id      uuid         NOT NULL,
  author_name         varchar(120) NOT NULL,
  author_role_code    varchar(12)  REFERENCES admin.app_roles(code),
  reported_by_user_id uuid,
  reported_by_name    varchar(120),
  report_reason       varchar(300),
  is_safety_report    boolean      NOT NULL DEFAULT false,
  status              varchar(10)  NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  reject_reason_code  varchar(15)  CHECK (reject_reason_code IN ('offensive','spam','privacy','off_topic','other')),
  decision_note       varchar(1000),
  decided_by          uuid         REFERENCES admin.admin_users(id),
  decided_at          timestamptz,
  created_at          timestamptz  NOT NULL DEFAULT now(),
  updated_at          timestamptz  NOT NULL DEFAULT now(),
  CONSTRAINT moderation_items_source_uq    UNIQUE (tenant_id, item_type, source_entity_id),
  CONSTRAINT moderation_items_decided_ck   CHECK ((status = 'pending') = (decided_by IS NULL AND decided_at IS NULL)),
  CONSTRAINT moderation_items_reject_ck    CHECK ((status = 'rejected') = (reject_reason_code IS NOT NULL)),
  CONSTRAINT moderation_items_other_ck     CHECK (reject_reason_code IS DISTINCT FROM 'other' OR decision_note IS NOT NULL),
  CONSTRAINT moderation_items_report_ck    CHECK (reported_by_user_id IS NULL OR report_reason IS NOT NULL),
  CONSTRAINT moderation_items_safety_ck    CHECK (NOT is_safety_report OR reported_by_user_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS moderation_items_queue_idx  ON admin.moderation_items (tenant_id, is_safety_report DESC, created_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS moderation_items_status_idx ON admin.moderation_items (tenant_id, status, decided_at DESC);
CREATE INDEX IF NOT EXISTS moderation_items_author_idx ON admin.moderation_items (tenant_id, author_user_id, decided_at) WHERE status = 'rejected';
COMMENT ON TABLE admin.moderation_items IS 'Cola única de moderación (spec 11): reseñas (previa) y contenido reportado. content_snapshot nunca guarda el binario ni URLs firmadas. Historial en moderation_items_history.';
COMMENT ON COLUMN admin.moderation_items.source_entity_id IS 'Referencia lógica al elemento de la app según item_type (app.reviews, app.photos, app.chat_messages, app.caregivers).';
CREATE OR REPLACE TRIGGER trg_updated_at BEFORE UPDATE ON admin.moderation_items
  FOR EACH ROW EXECUTE FUNCTION admin.tg_set_updated_at();

-- Una decisión es definitiva (ALREADY_MODERATED)
CREATE OR REPLACE FUNCTION admin.tg_moderation_once() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status <> 'pending' AND NEW.status <> OLD.status THEN
    RAISE EXCEPTION 'ALREADY_MODERATED: el elemento ya fue moderado' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER trg_moderation_once BEFORE UPDATE OF status ON admin.moderation_items
  FOR EACH ROW EXECUTE FUNCTION admin.tg_moderation_once();

CREATE TABLE IF NOT EXISTS admin.moderation_escalations (
  id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid        NOT NULL REFERENCES admin.tenants(id),
  author_user_id   uuid        NOT NULL,
  rejections_count smallint    NOT NULL CHECK (rejections_count >= 1),
  window_days      smallint    NOT NULL DEFAULT 90,
  triggered_at     timestamptz NOT NULL DEFAULT now(),
  is_open          boolean     NOT NULL DEFAULT true,
  reviewed_by      uuid        REFERENCES admin.admin_users(id),
  reviewed_at      timestamptz,
  review_note      text,
  CONSTRAINT moderation_escalations_review_ck CHECK (is_open OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS moderation_escalations_open_idx ON admin.moderation_escalations (tenant_id, is_open, triggered_at DESC);
COMMENT ON TABLE admin.moderation_escalations IS 'Casos escalados al admin por el job de reglas (3 rechazos ofensivos del mismo autor en 90 días, spec 11.3).';

-- ---------------------------------------------------------------------
-- 11 · Configuración y legales
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.system_settings (
  tenant_id   uuid         NOT NULL REFERENCES admin.tenants(id),
  key         varchar(80)  NOT NULL REFERENCES admin.setting_definitions(key),
  value       jsonb        NOT NULL,
  version     integer      NOT NULL DEFAULT 1 CHECK (version >= 1),
  change_note varchar(500) CHECK (change_note IS NULL OR length(change_note) >= 5),
  updated_by  uuid         REFERENCES admin.admin_users(id),
  updated_at  timestamptz,
  PRIMARY KEY (tenant_id, key)
);
COMMENT ON TABLE admin.system_settings IS 'Valor vigente de cada parámetro por tenant (spec 12). version = bloqueo optimista; cada cambio queda en system_settings_history.';

-- La versión avanza de uno en uno y el valor se valida en la API contra value_schema
CREATE OR REPLACE FUNCTION admin.tg_settings_version() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.value IS DISTINCT FROM OLD.value THEN
    IF NEW.version <> OLD.version + 1 THEN
      RAISE EXCEPTION 'VERSION_CONFLICT: versión esperada %, recibida %', OLD.version + 1, NEW.version
        USING ERRCODE = 'serialization_failure';
    END IF;
    NEW.updated_at := now();
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER trg_settings_version BEFORE UPDATE ON admin.system_settings
  FOR EACH ROW EXECUTE FUNCTION admin.tg_settings_version();

CREATE TABLE IF NOT EXISTS admin.legal_versions (
  id                    uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id             uuid          NOT NULL REFERENCES admin.tenants(id),
  doc_type              varchar(10)   NOT NULL CHECK (doc_type IN ('terms','privacy')),
  semver_major          smallint      NOT NULL CHECK (semver_major >= 0),
  semver_minor          smallint      NOT NULL CHECK (semver_minor >= 0),
  semver                varchar(10)   GENERATED ALWAYS AS (semver_major || '.' || semver_minor) STORED,
  content_md            text          NOT NULL CHECK (length(content_md) >= 100),
  changelog             varchar(2000) NOT NULL CHECK (length(changelog) >= 10),
  effective_date        date          NOT NULL,
  requires_reacceptance boolean       NOT NULL,
  status                varchar(10)   NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','superseded')),
  published_by          uuid          REFERENCES admin.admin_users(id),
  published_at          timestamptz,
  created_by            uuid          NOT NULL REFERENCES admin.admin_users(id),
  created_at            timestamptz   NOT NULL DEFAULT now(),
  updated_at            timestamptz   NOT NULL DEFAULT now(),
  CONSTRAINT legal_versions_semver_uq    UNIQUE (tenant_id, doc_type, semver_major, semver_minor),
  CONSTRAINT legal_versions_published_ck CHECK (status = 'draft' OR (published_by IS NOT NULL AND published_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS legal_versions_current_idx ON admin.legal_versions (tenant_id, doc_type, effective_date DESC) WHERE status = 'published';
COMMENT ON TABLE admin.legal_versions IS 'Términos y política de privacidad versionados (spec 13). Nunca se borran: app.legal_acceptances referencia legal_versions.id. Historial en legal_versions_history.';
COMMENT ON COLUMN admin.legal_versions.status IS 'draft → published (vigente desde effective_date) → superseded cuando otra versión posterior entra en vigor.';
CREATE OR REPLACE TRIGGER trg_updated_at BEFORE UPDATE ON admin.legal_versions
  FOR EACH ROW EXECUTE FUNCTION admin.tg_set_updated_at();

-- Solo los borradores se editan; lo publicado únicamente puede pasar a superseded
CREATE OR REPLACE FUNCTION admin.tg_legal_versions_lock() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status <> 'draft' THEN
    IF NEW.content_md <> OLD.content_md OR NEW.changelog <> OLD.changelog
       OR NEW.effective_date <> OLD.effective_date OR NEW.semver_major <> OLD.semver_major
       OR NEW.semver_minor <> OLD.semver_minor OR NEW.requires_reacceptance <> OLD.requires_reacceptance THEN
      RAISE EXCEPTION 'Una versión legal publicada es inmutable' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'draft' THEN
      RAISE EXCEPTION 'Una versión publicada no puede volver a borrador' USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER trg_legal_lock BEFORE UPDATE ON admin.legal_versions
  FOR EACH ROW EXECUTE FUNCTION admin.tg_legal_versions_lock();

-- ---------------------------------------------------------------------
-- 12 · Jobs
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.job_runs (
  id            bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id     uuid        REFERENCES admin.tenants(id),
  job_name      varchar(60) NOT NULL,
  status        varchar(10) NOT NULL CHECK (status IN ('running','succeeded','failed')),
  started_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz,
  watermark     timestamptz,
  rows_affected integer,
  error         text,
  CONSTRAINT job_runs_finished_ck CHECK ((status = 'running') = (finished_at IS NULL))
);
CREATE INDEX IF NOT EXISTS job_runs_name_idx   ON admin.job_runs (job_name, started_at DESC);
CREATE INDEX IF NOT EXISTS job_runs_tenant_idx ON admin.job_runs (tenant_id, job_name, status);
COMMENT ON TABLE admin.job_runs IS 'Bitácora de jobs de agregación, monitor y mantenimiento: frescura de los datos y alerta de conectores caídos (spec 4.4).';

-- ---------------------------------------------------------------------
