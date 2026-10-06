# Guía para el equipo frontend

## Qué se entrega

- **API de prueba compartida:** https://agecare-admin-pruebas.vercel.app
- **Swagger:** https://agecare-admin-pruebas.vercel.app/api/v1/admin/docs
- **Contrato OpenAPI:** https://agecare-admin-pruebas.vercel.app/api/v1/admin/openapi.json
- **DDL SQL:** [`../sql/agecare_admin_ddl.sql`](../sql/agecare_admin_ddl.sql).

DDL significa *Data Definition Language*: crea la estructura de PostgreSQL,
sus relaciones, índices, funciones, políticas y catálogos. Es un archivo `.sql`.
El frontend consume la API mediante HTTP. La conexión y las credenciales de
PostgreSQL pertenecen al backend y no deben incluirse en el código frontend.
Los campos, respuestas y permisos disponibles pueden consultarse en Swagger.
Si la interfaz necesita algo diferente, el equipo debe acordar el cambio de contrato.

## 1. Conectarse a la API de prueba

No hace falta instalar Docker ni PostgreSQL para usar esta API. Tiene datos
ficticios y cuatro cuentas demo; no debe usarse para información real.
La raíz de los endpoints es:

```text
https://agecare-admin-pruebas.vercel.app/api/v1/admin
```

1. Abrir Swagger y buscar `POST /auth/login`.
2. Pulsar **Try it out**, enviar el JSON siguiente y pulsar **Execute**:

```json
{"email":"admin@wellq.co.uk","password":"Admin123!"}
```

3. Copiar el valor de `access_token` sin comillas. Pulsar **Authorize** arriba,
   pegar solamente ese token en **Value** de `BearerAuth`, pulsar **Authorize**
   y después **Close**. Swagger agrega automáticamente el prefijo `Bearer`.
4. Probar `GET /auth/me`, `/support/summary` y `/ops/status`.

Para métricas comerciales, enviar el período requerido, por ejemplo:
`GET /metrics/commercial/summary?period=current_month`. Swagger muestra los
parámetros obligatorios; omitirlos devuelve HTTP 422.

También se puede integrar desde JavaScript:

```javascript
const base = "https://agecare-admin-pruebas.vercel.app/api/v1/admin";
const login = await fetch(`${base}/auth/login`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email: "admin@wellq.co.uk", password: "Admin123!" }),
});
if (!login.ok) throw new Error(`Login: ${login.status}`);
const session = await login.json();
const response = await fetch(`${base}/auth/me`, {
  headers: { Authorization: `Bearer ${session.access_token}` },
});
if (!response.ok) throw new Error(`Perfil: ${response.status}`);
const profile = await response.json();
```

Las contraseñas anteriores son exclusivamente de demostración. La aplicación
real debe solicitar las credenciales al usuario. El access token dura 15 minutos;
`POST /auth/refresh` recibe `{"refresh_token":"..."}` y devuelve tokens nuevos.
Los refresh tokens rotan: conservar el nuevo y dejar de usar el anterior.
`POST /auth/logout` recibe el refresh token y el encabezado Bearer.

CORS permite `http://localhost:3000` y `http://localhost:5173`. Deben coincidir
protocolo, hostname y puerto: `127.0.0.1` es un origen diferente. Cuando el equipo
tenga una URL publicada, el responsable del backend debe agregarla a
`ADMIN_CORS_ORIGINS` y volver a desplegar.

## 2. Reproducir la API y la base localmente

Requisitos: Git y Docker Desktop iniciado. En PowerShell:

```powershell
git clone https://github.com/Pombet/agecare-admin-api.git
cd agecare-admin-api
# Hasta que esta entrega se mezcle en main:
git switch fix/despliegue-postgres-remoto
Copy-Item .env.example .env
docker compose up --build
```

Esto levanta PostgreSQL 16, ejecuta `alembic upgrade head`, carga datos demo y
sirve la API en `http://localhost:8000/api/v1/admin/docs`. Usar las mismas cuentas
demo del README. Se requiere acceso al repositorio si es privado.

El arranque de Compose ejecuta el seed cada vez: usarlo con una base de
desarrollo dedicada. El seed modifica datos de demostración y no se debe
ejecutar sobre una base con información que necesiten conservar.

Para comprobar la base desde otra terminal, dentro del repositorio:

```powershell
docker compose exec db psql -U agecare -d agecare_admin -c "SELECT version_num FROM public.alembic_version;"
docker compose exec db psql -U agecare -d agecare_admin -c "SELECT count(*) FROM admin.tenants;"
docker compose exec db psql -U agecare -d agecare_admin -c "SELECT count(*) FROM admin.admin_users;"
docker compose exec db psql -U agecare -d agecare_admin -c "\dt admin.*"
```

La revisión esperada es `0007_security_retention`; el seed crea un tenant y
cuatro cuentas de staff. El esquema canónico tiene 58 tablas lógicas. El total
de tablas incluye particiones que dependen
de la fecha. Las tablas del prototipo en `public` se conservan; la API utiliza
las tablas canónicas de `admin`.

## 3. Instalar solamente el DDL

Este camino permite revisar el esquema por SQL en una **base nueva y separada**.
No ejecutar además el DDL manual sobre la base creada por Alembic. El archivo
no registra revisiones Alembic ni crea las cuentas y métricas del seed demo.
Incluye los catálogos iniciales que necesita el esquema.

Requisitos: PostgreSQL 16 y un usuario con permisos para crear extensiones,
roles, funciones y políticas. Para probar usando el servicio PostgreSQL local,
desde el repositorio en PowerShell:

```powershell
docker compose up -d db
docker compose exec db createdb -U agecare agecare_ddl_demo
docker compose cp sql/agecare_admin_ddl.sql db:/tmp/agecare_admin_ddl.sql
docker compose exec db psql -U agecare -d agecare_ddl_demo -v ON_ERROR_STOP=1 -f /tmp/agecare_admin_ddl.sql
docker compose exec db psql -U agecare -d agecare_ddl_demo -c "\dt admin.*"
```

Esperar que PostgreSQL esté listo antes de `createdb`. Si esa base ya existe,
elegir otro nombre vacío. El script agrupa las seis migraciones SQL inmutables
`0002_core` a `0007_security_retention` dentro de una transacción. Los cambios
futuros se incorporan mediante nuevas migraciones; regenerar modelos Python
no sustituye una migración ni modifica por sí solo la API.

Para regenerar el archivo entregable desde los snapshots o comprobar que coincide:

```powershell
python -m scripts.export_ddl
python -m scripts.export_ddl --check
```

## 4. Configuración del backend remoto

El proyecto de prueba usa PostgreSQL de Neon y Vercel. Para reproducir un
despliegue, el responsable del backend configura estas variables privadas:

| Variable | Valor o finalidad |
| --- | --- |
| `ADMIN_DATABASE_URL` | Conexión PostgreSQL con driver `postgresql+asyncpg`, sin parámetros `sslmode`/`channel_binding` |
| `ADMIN_DATABASE_SSL` | `true`: TLS con verificación del certificado y hostname |
| `ADMIN_JWT_SECRET` | Secreto aleatorio propio; nunca el valor demo del Compose |
| `ADMIN_ENVIRONMENT` | `staging` para esta API de prueba |
| `ADMIN_CORS_ORIGINS` | `http://localhost:3000,http://localhost:5173` |

Aplicar `alembic upgrade head` desde un entorno administrativo con esas variables
antes de usar la API. Ejecutar `python -m scripts.seed` únicamente al preparar
una base demo. Las funciones de Vercel no ejecutan migraciones ni seed al recibir
peticiones. Configurar variables y código requiere un nuevo despliegue para
aplicar los cambios.

Esta demo se conecta con el propietario de su base Neon, que permite el inicio
de sesión y omite RLS. Para producción se debe diseñar el acceso de autenticación
y usar roles con los permisos mínimos; no copiar esta configuración de demo
para almacenar datos reales. El proyecto mantiene políticas RLS en el DDL.

Subir SQL y código a GitHub comparte esos archivos. Actualizar los datos o la
estructura de una base remota requiere ejecutar las migraciones sobre esa base.
La publicación actual se hace desde CLI; todavía no hay sincronización automática
de GitHub con este proyecto personal de Vercel.
