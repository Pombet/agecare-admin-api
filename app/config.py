"""Configuración de la API de administración de AgeCare."""
import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ADMIN_", extra="ignore")

    app_name: str = "AgeCare Admin API"
    environment: str = "dev"  # dev | staging | prod
    database_url: str = "postgresql+asyncpg://agecare:agecare@localhost:5432/agecare_admin"
    database_ssl: bool = False
    cors_origins: str = ""
    # Solo juegos: origen HTTPS público, sin /static ni barra final.
    game_public_base_url: str = ""

    # JWT
    jwt_secret: str = "cambia-esto-en-produccion"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_hours: int = 12

    # Login
    max_login_attempts: int = 5
    lockout_minutes: int = 15
    allowed_email_domains: str = "wellq.co.uk,wellq.co"  # separados por coma

    # Sincronización servidor a servidor con la app (sitio de familias y cuidadoras). Vacía = desactivada.
    # La app se identifica con la cabecera X-Internal-Key; sus datos se guardan en el tenant indicado.
    internal_sync_key: str = ""
    internal_sync_tenant: str = "local"

    # Negocio
    business_timezone: str = "America/Santiago"

    # ---- Pasarela de pago y suscripciones (app/billing, docs/PASARELA-PAGO.md) ----
    log_level: str = "INFO"     # logs JSON de la pasarela (logger "agecare")
    log_format: str = "json"    # json (Vercel) | text (lectura en local)
    # Secreto del cron de Vercel (variable CRON_SECRET, sin prefijo: así la envía Vercel). Vacío = cron desactivado.
    cron_secret: str = os.environ.get("CRON_SECRET", "")
    # fake = simulador local de Webpay (dev/tests) · transbank = Oneclick Mall real
    billing_gateway: str = "fake"
    # 2FA obligatorio para operar pagos (escrituras). Por defecto: solo en prod
    billing_require_mfa: bool | None = None
    billing_currency: str = "CLP"
    # Tenant donde quedan los pagos de la app (usuarios finales). Vacío = internal_sync_tenant
    billing_app_tenant: str = ""
    # Llave Fernet para cifrar tbk_user. Obligatoria en prod. Fuera de prod, si falta, se deriva de jwt_secret
    billing_encryption_key: str = ""
    # Llaves anteriores (separadas por coma) solo para descifrar: permiten rotar la llave
    billing_encryption_keys_old: str = ""
    # URL pública de esta API (Transbank redirige aquí tras la inscripción)
    public_base_url: str = "http://localhost:8000"
    # Adónde se envía al usuario tras inscribir la tarjeta (web o deep link de la app)
    billing_return_url: str = "http://localhost:5173/checkout/resultado"
    # true = el ciclo de facturación escribe el MRR y las unidades que pagan reales en metrics_plan_snapshot y
    # metrics_daily_users (dashboard comercial). Desactivado por defecto: no pisa los datos demo del dashboard.
    billing_sync_metrics: bool = False
    billing_incomplete_ttl_hours: int = 24      # suscripción sin pagar → expired
    billing_reconcile_after_seconds: int = 120  # cobros pending/unknown a conciliar
    # Transbank Oneclick Mall. Valores por defecto = ambiente de integración público de Transbank.
    tbk_environment: str = "integration"  # integration | production
    tbk_commerce_code: str = "597055555541"        # código del mall
    tbk_child_commerce_code: str = "597055555542"  # tienda hija que cobra las suscripciones
    tbk_api_secret: str = "579B532A7440BB0C9079DED94D31EA1615BACEB56610332264630D42D0A36B1C"
    tbk_timeout_seconds: float = 30.0
    # JWT de usuarios de la app (emitido por la API principal de AgeCare)
    app_jwt_secret: str = "cambia-esto-jwt-app"  # HS256: secreto compartido · RS256: llave pública PEM
    app_jwt_algorithm: str = "HS256"
    app_jwt_audience: str | None = None

    @property
    def tbk_base_url(self) -> str:
        return ("https://webpay3g.transbank.cl" if self.tbk_environment == "production"
                else "https://webpay3gint.transbank.cl")

    @property
    def billing_mfa_required(self) -> bool:
        return self.environment == "prod" if self.billing_require_mfa is None else self.billing_require_mfa

    def validate_billing(self) -> None:
        """Evita desplegar a producción con credenciales de integración o sin cifrado."""
        if self.environment != "prod":
            return
        problems = []
        if self.billing_gateway != "transbank":
            problems.append("ADMIN_BILLING_GATEWAY debe ser 'transbank'")
        if self.tbk_environment != "production":
            problems.append("ADMIN_TBK_ENVIRONMENT debe ser 'production'")
        if self.tbk_commerce_code.startswith("5970555555"):
            problems.append("ADMIN_TBK_COMMERCE_CODE es el de integración")
        if not self.billing_encryption_key:
            problems.append("falta ADMIN_BILLING_ENCRYPTION_KEY")
        if problems:
            raise RuntimeError("Configuración de pagos inválida para producción: " + "; ".join(problems))

    @property
    def allowed_domains(self) -> list[str]:
        return [d.strip().lower() for d in self.allowed_email_domains.split(",") if d.strip()]

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.cors_origins.split(",")
                if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
