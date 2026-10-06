-- AgeCare canonical DDL, source sections 4
-- Keep this migration SQL immutable after deployment.

-- 4 · Staff, sesiones y auditoría
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.admin_users (
  id              uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid         NOT NULL REFERENCES admin.tenants(id),
  full_name       varchar(120) NOT NULL CHECK (length(full_name) BETWEEN 2 AND 120),
  email           citext       NOT NULL CHECK (email ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
  role_code       varchar(20)  NOT NULL REFERENCES admin.admin_roles(code),
  password_hash   varchar(255),
  is_active       boolean      NOT NULL DEFAULT true,
  activated_at    timestamptz,
  mfa_required    boolean      NOT NULL DEFAULT false,
  mfa_enabled     boolean      NOT NULL DEFAULT false,
  mfa_secret_enc  bytea,
  failed_attempts smallint     NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0),
  locked_until    timestamptz,
  last_login_at   timestamptz,
  created_by      uuid         REFERENCES admin.admin_users(id),
  created_at      timestamptz  NOT NULL DEFAULT now(),
  updated_at      timestamptz  NOT NULL DEFAULT now(),
  CONSTRAINT admin_users_email_tenant_uq UNIQUE (tenant_id, email),
  CONSTRAINT admin_users_mfa_secret_ck CHECK (NOT mfa_enabled OR mfa_secret_enc IS NOT NULL),
  CONSTRAINT admin_users_activated_ck CHECK (activated_at IS NULL OR password_hash IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS admin_users_tenant_role_idx ON admin.admin_users (tenant_id, role_code, is_active);
CREATE INDEX IF NOT EXISTS admin_users_search_idx ON admin.admin_users USING gin ((full_name || ' ' || email::text) gin_trgm_ops);
COMMENT ON TABLE admin.admin_users IS 'Cuentas del personal de Wellq Co (spec 3). Separadas de los usuarios de la app.';
COMMENT ON COLUMN admin.admin_users.password_hash IS 'Argon2id. NULL = cuenta pendiente de activación (login → ADMIN_DISABLED).';
COMMENT ON COLUMN admin.admin_users.mfa_secret_enc IS 'Secreto TOTP cifrado por la aplicación (Key Vault). Nunca en claro ni en audit_log.';
COMMENT ON COLUMN admin.admin_users.locked_until IS 'Bloqueo de 15 min tras 5 intentos fallidos en 10 min (spec 3.1).';

CREATE OR REPLACE TRIGGER trg_updated_at BEFORE UPDATE ON admin.admin_users
  FOR EACH ROW EXECUTE FUNCTION admin.tg_set_updated_at();

-- Debe existir al menos un admin activo por tenant (spec 3.7 LAST_ADMIN)
CREATE OR REPLACE FUNCTION admin.tg_admin_users_last_admin() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'UPDATE'
     AND OLD.role_code = 'admin' AND OLD.is_active
     AND (NEW.role_code <> 'admin' OR NOT NEW.is_active) THEN
    IF NOT EXISTS (SELECT 1 FROM admin.admin_users u
                    WHERE u.tenant_id = OLD.tenant_id AND u.id <> OLD.id
                      AND u.role_code = 'admin' AND u.is_active AND u.activated_at IS NOT NULL) THEN
      RAISE EXCEPTION 'LAST_ADMIN: debe existir al menos una cuenta activa con rol admin'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER trg_last_admin BEFORE UPDATE OF role_code, is_active ON admin.admin_users
  FOR EACH ROW EXECUTE FUNCTION admin.tg_admin_users_last_admin();

CREATE TABLE IF NOT EXISTS admin.admin_invitations (
  id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  uuid        NOT NULL REFERENCES admin.tenants(id),
  admin_id   uuid        NOT NULL REFERENCES admin.admin_users(id) ON DELETE CASCADE,
  token_hash char(64)    NOT NULL UNIQUE,
  expires_at timestamptz NOT NULL,
  used_at    timestamptz,
  created_by uuid        REFERENCES admin.admin_users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT admin_invitations_expiry_ck CHECK (expires_at > created_at)
);
CREATE INDEX IF NOT EXISTS admin_invitations_admin_idx ON admin.admin_invitations (admin_id);
COMMENT ON TABLE admin.admin_invitations IS 'Enlaces de activación de un solo uso, 24 h (spec 3.5). token_hash = SHA-256 del token enviado por correo.';

CREATE TABLE IF NOT EXISTS admin.admin_sessions (
  id                 uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          uuid        NOT NULL REFERENCES admin.tenants(id),
  admin_id           uuid        NOT NULL REFERENCES admin.admin_users(id) ON DELETE CASCADE,
  refresh_token_hash char(64)    NOT NULL UNIQUE,
  family_id          uuid        NOT NULL,
  rotated_from       uuid        REFERENCES admin.admin_sessions(id),
  expires_at         timestamptz NOT NULL,
  revoked_at         timestamptz,
  revoked_reason     varchar(30) CHECK (revoked_reason IN ('logout','rotated','reuse_detected','admin_disabled','expired')),
  ip                 inet,
  user_agent         varchar(300),
  created_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT admin_sessions_revoked_ck CHECK ((revoked_at IS NULL) = (revoked_reason IS NULL))
);
CREATE INDEX IF NOT EXISTS admin_sessions_admin_idx  ON admin.admin_sessions (admin_id, revoked_at);
CREATE INDEX IF NOT EXISTS admin_sessions_family_idx ON admin.admin_sessions (family_id);
CREATE INDEX IF NOT EXISTS admin_sessions_expiry_idx ON admin.admin_sessions (expires_at);
COMMENT ON TABLE admin.admin_sessions IS 'Refresh tokens rotatorios (12 h). Reusar un token rotado revoca toda la familia (spec 3.2).';
COMMENT ON COLUMN admin.admin_sessions.family_id IS 'Todas las rotaciones derivadas de un mismo login comparten family_id.';

CREATE TABLE IF NOT EXISTS admin.admin_login_attempts (
  id           bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id    uuid        NOT NULL REFERENCES admin.tenants(id),
  email        citext      NOT NULL,
  admin_id     uuid        REFERENCES admin.admin_users(id) ON DELETE SET NULL,
  succeeded    boolean     NOT NULL,
  failure_code varchar(30) CHECK (failure_code IN ('INVALID_CREDENTIALS','OTP_REQUIRED','OTP_INVALID','ADMIN_DISABLED','ACCOUNT_LOCKED')),
  ip           inet,
  user_agent   varchar(300),
  attempted_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT admin_login_attempts_failure_ck CHECK (succeeded OR failure_code IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS admin_login_attempts_lookup_idx ON admin.admin_login_attempts (tenant_id, email, attempted_at DESC);
COMMENT ON TABLE admin.admin_login_attempts IS 'Intentos de login persistidos para el bloqueo progresivo (spec 3.1). Retención 90 días (job).';

-- Auditoría: particionada por mes, inmutable
CREATE TABLE IF NOT EXISTS admin.audit_log (
  id          uuid        NOT NULL DEFAULT gen_random_uuid(),
  tenant_id   uuid        NOT NULL REFERENCES admin.tenants(id),
  actor_id    uuid        REFERENCES admin.admin_users(id),
  actor_name  varchar(120),
  actor_role  varchar(20),
  action      varchar(60) NOT NULL CHECK (action ~ '^[a-z_]+\.[a-z_]+$'),
  entity_type varchar(40),
  entity_id   uuid,
  before      jsonb,
  after       jsonb,
  ip          inet,
  user_agent  varchar(300),
  request_id  uuid,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);
CREATE INDEX IF NOT EXISTS audit_log_tenant_time_idx   ON admin.audit_log (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS audit_log_actor_idx         ON admin.audit_log (tenant_id, actor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS audit_log_entity_idx        ON admin.audit_log (tenant_id, entity_type, entity_id);
CREATE INDEX IF NOT EXISTS audit_log_action_idx        ON admin.audit_log (tenant_id, action, created_at DESC);
COMMENT ON TABLE admin.audit_log IS 'Registro inmutable de acciones del staff (spec 14). Solo INSERT; particionada por mes; retención 24 meses.';
COMMENT ON COLUMN admin.audit_log.action IS 'Acción tipificada modulo.verbo: ticket.update, settings.update, auth.login_failed…';
COMMENT ON COLUMN admin.audit_log.before IS 'Estado previo (diff) con campos sensibles enmascarados por la aplicación.';

CREATE OR REPLACE TRIGGER trg_immutable BEFORE UPDATE OR DELETE ON admin.audit_log
  FOR EACH ROW EXECUTE FUNCTION admin.tg_forbid_change();

-- ---------------------------------------------------------------------
