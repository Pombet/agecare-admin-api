# Pasarela de pago y suscripciones (Transbank Oneclick Mall)

Módulo del equipo **DACA_09**. Cobra los planes de pago de AgeCare (**Dorado** por usuario,
**Platino** por familia, **Proveedor** por proveedor) con **Transbank Oneclick Mall** y gestiona el
ciclo de vida de las suscripciones mensuales. La consola lo opera desde `/api/v1/admin/billing`, y la
app de familias y cuidadoras lo consume desde `/api/v1/billing`.

## Qué agrega

| Pieza | Archivos |
|---|---|
| Migración `0011_billing` (snapshot SQL inmutable) | `alembic/versions/0011_billing.py`, `alembic/sql/0011_billing.sql` |
| Modelos, reglas de negocio, cliente Transbank y simulador | `app/billing/` |
| Contexto de tenant de la pasarela | `app/billing/tenancy.py` |
| Logs JSON del módulo (logger `agecare`) | `app/logs.py` |
| Permisos `billing` y `billing_refunds` | `app/enums.py` (matriz `READ`/`WRITE`) |
| Variables de configuración | `app/config.py` (bloque «Pasarela de pago») |
| Cron diario de Vercel | `vercel.json` (`crons`) |
| Scripts | `scripts/billing_cycle.py`, `scripts/app_token.py` |
| Pruebas (PostgreSQL) | `tests/billing/` |

No modifica tablas, rutas ni datos de los demás módulos. Cambios compartidos, todos aditivos:
`app/main.py` registra los dos routers y completa el OpenAPI solo en las rutas `/billing`;
`requirements.txt` agrega `httpx` y `cryptography`. La configuración de logs solo afecta al
logger `agecare`, así que los demás módulos y uvicorn conservan su formato.

### Base de datos (0011_billing)

- **Tablas**: `admin.billing_payment_methods`, `billing_subscriptions`, `billing_charges`,
  `billing_refunds`, `billing_events` y el catálogo `billing_subscription_transitions`.
  Todas tienen `tenant_id` con **RLS forzado** (`FORCE ROW LEVEL SECURITY`), igual que juegos y
  marketplace. Las tres primeras tienen historial inmutable (`*_history`).
- **Reglas que hace cumplir la base**, aunque falle el código:
  - la máquina de estados de la suscripción (tabla de transiciones + trigger);
  - una suscripción viva por beneficiario y una tarjeta activa por usuario;
  - como máximo un cobro en vuelo por suscripción;
  - orden de compra y monto inmutables;
  - reembolsos que no superan lo cobrado;
  - el precio congelado solo cambia junto con el plan.
- **Secretos**: el `tbk_user` de Transbank se guarda cifrado (Fernet) en `tbk_user_enc`. La función
  común `admin.tg_write_history()` se reemplaza por la misma de `0002_core` con dos claves más en
  su lista de secretos (`tbk_user_enc` y `inscription_token_hash`), así nunca llegan al historial.
  Para las demás tablas no cambia nada. El rol `agecare_admin_ro` no puede leer esas columnas.
- **Catálogos**:
  - agrega `billing` y `billing_refunds` al CHECK de `admin_role_permissions.module`, conservando
    `games`;
  - inserta los permisos: admin escribe; analista y soporte leen;
  - crea la definición del parámetro `billing_dunning`, que por defecto reintenta a +1, +3 y
    +5 días;
  - agrega `UNIQUE (code, billing_unit)` en `admin.plans` (la FK compuesta de la suscripción
    asegura que el plan y el tipo de beneficiario coincidan).
- **Precios**: se leen del parámetro existente `plan_prices` del tenant (Configuración).
  Si un plan no tiene precio, la API responde `PLAN_PRICE_MISSING`.
- **Retención**: cobros, reembolsos y eventos se conservan por la obligación tributaria de 6
  años. `admin.billing_maintenance()` solo purga inscripciones abandonadas.
- **Downgrade**: elimina el módulo solo si no hay cobros registrados. Si los hay, se detiene y
  pide respaldar la base.

### Tenant

- **Consola**: usa el tenant de la sesión del staff, igual que el resto de la API.
- **App** (usuarios finales y el retorno de Webpay): los pagos quedan en el tenant
  `ADMIN_BILLING_APP_TENANT`. Vacío usa el mismo de `ADMIN_INTERNAL_SYNC_TENANT` (`local`).
- **Cron**: recorre los tenants activos y corre el ciclo dentro de cada uno.

Como el servicio hace COMMIT antes de llamar a Transbank (para no cobrar dos veces), la sesión
vuelve a fijar `app.tenant_id` al comenzar cada transacción. Además, toda consulta del módulo lleva
`tenant_id = <tenant>` explícito, así el aislamiento no depende solo de RLS: el dueño del esquema
queda exento en las tablas `metrics_*`, que no tienen `FORCE`.

## Aplicación en el entorno del equipo

1. Respaldar la base y comprobar que está en `0010_marketplace_loyalty`.
2. Desde la raíz de este backend:

   ```powershell
   python -m alembic upgrade head
   ```

   Crea las tablas y permisos descritos arriba. **No ejecuta seeds, no vacía tablas ni modifica
   cuentas.** Vercel no ejecuta migraciones: hay que correrla contra la base de Neon con
   `ADMIN_DATABASE_URL` apuntando a ella.
3. Variables en Vercel (Settings → Environment Variables). Para el ambiente de pruebas bastan:

   | Variable | Valor sugerido | Para qué |
   |---|---|---|
   | `ADMIN_BILLING_GATEWAY` | `fake` (simulador) o `transbank` | `transbank` usa el ambiente de **integración** público de Transbank: tarjetas de prueba, sin cobros reales |
   | `ADMIN_PUBLIC_BASE_URL` | `https://<dominio-de-esta-api>` | Transbank redirige aquí tras inscribir la tarjeta |
   | `ADMIN_BILLING_RETURN_URL` | URL del front que muestra el resultado | adónde vuelve el usuario |
   | `ADMIN_BILLING_ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | cifra `tbk_user`. Fuera de prod, si falta, se deriva de `ADMIN_JWT_SECRET` |
   | `ADMIN_APP_JWT_SECRET` | secreto compartido con la API principal de la app | verifica el JWT de los usuarios finales |
   | `CRON_SECRET` | cadena aleatoria larga | protege el cron. Vacío desactiva el cron (404) |

   Opcionales:
   - `ADMIN_BILLING_APP_TENANT`;
   - `ADMIN_BILLING_ENCRYPTION_KEYS_OLD`, para rotar la llave;
   - `ADMIN_BILLING_REQUIRE_MFA` (por defecto, 2FA solo en prod);
   - `ADMIN_BILLING_SYNC_METRICS=true`, para que el dashboard comercial muestre la facturación real
     en lugar de los datos demo;
   - `ADMIN_LOG_LEVEL` y `ADMIN_LOG_FORMAT`.

   **Producción** (`ADMIN_ENVIRONMENT=prod`) exige:
   - `ADMIN_BILLING_GATEWAY=transbank`;
   - `ADMIN_TBK_ENVIRONMENT=production`;
   - `ADMIN_TBK_COMMERCE_CODE`, `ADMIN_TBK_CHILD_COMMERCE_CODE` y `ADMIN_TBK_API_SECRET` del
     contrato con Transbank;
   - `ADMIN_BILLING_ENCRYPTION_KEY`.

   Sin esto la API no arranca. Así se evita cobrar con credenciales de integración.
4. El cron (`vercel.json`) llama a diario a `GET /api/v1/billing/internal/cron/billing-cycle`, a
   las 12:00 UTC, con `Authorization: Bearer $CRON_SECRET`. El plan Hobby de Vercel permite cron
   diario. El staff también puede ejecutarlo con **Ejecutar ahora**
   (`POST /api/v1/admin/billing/jobs/billing-cycle`).

## Rutas

### Consola: `/api/v1/admin/billing` (JWT de staff)

| Método y ruta | Permiso |
|---|---|
| `GET /summary` | `billing` lectura: MRR, activas, en mora, aprobación 30 días, ingresos netos, renovaciones próximas |
| `GET /subscriptions` · `GET /subscriptions/{id}` | `billing` lectura. Filtros: `status`, `plan`, `q`, `cancel_scheduled`, `user_id` |
| `POST /subscriptions/{id}/cancel` · `/extend` · `/retry-charge` | `billing` escritura (admin) |
| `GET /charges` · `GET /charges/{id}` | `billing` lectura |
| `POST /charges/{id}/refunds` · `POST /charges/{id}/chargeback` | `billing_refunds` (solo admin) |
| `POST /jobs/billing-cycle` | `billing_refunds` (solo admin) |
| `POST /dev/app-token` | admin, fuera de prod: emite un JWT de usuario de prueba para el *checkout de prueba* |

Todas las escrituras quedan en `audit_log` (`billing.*`). Con `ADMIN_ENVIRONMENT=prod` exigen
además 2FA activo (`MFA_REQUIRED`).

### App: `/api/v1/billing` (JWT del usuario final)

| Método y ruta | Uso |
|---|---|
| `GET /plans` | Planes de pago con su precio vigente (público) |
| `GET /me` · `GET /me/charges` | Plan efectivo, suscripciones, tarjeta e historial de cobros |
| `POST /subscriptions` | Contratar `{plan_code, beneficiary_id?}`: cobra con la tarjeta guardada o devuelve `url_webpay` + `token` |
| `POST /subscriptions/{id}/cancel` · `/resume` · `/change-plan` | Gestionar la suscripción |
| `POST /payment-methods` · `DELETE /payment-methods/{id}` | Inscribir, cambiar o eliminar la tarjeta |
| `GET·POST /oneclick/return` | `response_url` de Webpay (lo llama Transbank, no la app) |

Para redirigir a Webpay, la app envía un **formulario POST** a `url_webpay` con el campo
`TBK_TOKEN`. Tras la inscripción, la API cobra el primer mes y redirige a
`ADMIN_BILLING_RETURN_URL?result=success|charge_failed|rejected|aborted|charge_pending|error`.

El JWT de la app lo emite la API principal. Este módulo solo lo verifica con
`ADMIN_APP_JWT_SECRET` (HS256, o RS256 con llave pública) y lee estos *claims*:
- `sub`;
- `email`;
- `name`;
- `family_ids` (para Platino);
- `provider_id` (para Proveedor).

## Ciclo de vida y garantías

```
incomplete ──pago OK──▶ active ──rechazo en renovación──▶ past_due ──pago OK──▶ active
    │ 24 h sin pago        │ cancelación                     │ reintentos agotados / cancelación
    ▼                      ▼                                 ▼
 expired                canceled ◀───────────────────────────┘
```

- `past_due` mantiene el acceso premium (gracia) mientras dura la política `billing_dunning`.
- El ciclo de cada tenant:
  1. concilia los cobros sin respuesta de Transbank;
  2. expira lo que no se pagó;
  3. cierra las cancelaciones programadas;
  4. cobra las renovaciones y los reintentos vencidos;
  5. solo con `ADMIN_BILLING_SYNC_METRICS=true`, escribe el MRR y las unidades que pagan reales en
     `metrics_plan_snapshot` y `metrics_daily_users`, que alimentan el dashboard comercial.
     Viene desactivado para no pisar los datos demo del dashboard.
- **No hay doble cobro**:
  - cada cobro se guarda en `pending` y se hace COMMIT antes de llamar a Transbank;
  - si Transbank no responde, el cobro queda `unknown` y se concilia con
    `GET /transactions/{buy_order}`;
  - un índice único impide dos cobros en vuelo para la misma suscripción;
  - el job toma cada suscripción con `FOR UPDATE SKIP LOCKED`.

## Probar en Swagger

1. Con `ADMIN_BILLING_GATEWAY=fake`, iniciar sesión como admin y ejecutar
   `POST /api/v1/admin/billing/dev/app-token`. Devuelve un `token` de usuario de la app.
2. Con ese token en **Authorize**, ejecutar `POST /api/v1/billing/subscriptions` con
   `{"plan_code": "gold"}`.
3. Abrir en el navegador `<inscription.url_webpay>?TBK_TOKEN=<inscription.token>` y elegir
   **Inscribir Visa**. Es la página del simulador de Webpay. La API cobra el primer mes y redirige.
4. Comprobar en Neon:

   ```sql
   SELECT s.user_email, s.plan_code, s.status, c.amount, c.status AS cobro
   FROM admin.billing_subscriptions s JOIN admin.billing_charges c ON c.subscription_id = s.id
   ORDER BY s.created_at DESC LIMIT 5;
   ```

   Si el editor de Neon no muestra filas, se debe al RLS forzado: primero hay que ejecutar
   `SELECT set_config('app.tenant_id', (SELECT id::text FROM admin.tenants WHERE code = 'local'), false);`.

## Ejecutar las pruebas

Las pruebas de la pasarela necesitan PostgreSQL (RLS, triggers y tipos propios), como las
demás del esquema canónico:

```powershell
python -m alembic upgrade head
python -m scripts.seed
$env:TEST_DATABASE_URL = "postgresql+asyncpg://usuario:clave@localhost:5432/agecare_admin"
python -m pytest tests/billing
```

Sin `TEST_DATABASE_URL`, `tests/billing` se omite. Se verificaron en PostgreSQL 16:
- con un usuario superusuario;
- con un dueño del esquema sin superusuario ni BYPASSRLS, como en Neon, donde aplica el RLS
  forzado.

Incluyen una prueba de aislamiento entre dos tenants y del cron por tenant.

**Esta entrega no aplicó migraciones a bases existentes, no ejecutó seeds ni publicó cambios.**
