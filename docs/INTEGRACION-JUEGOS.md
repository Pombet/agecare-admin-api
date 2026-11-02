# Integración exclusiva de administración de juegos

## Alcance

Se incorpora únicamente el catálogo administrativo y los 30 juegos del backend
AgeCare.

Esta API usa el esquema PostgreSQL `admin` y organizaciones (`tenant_id`).
Por eso `GameApp` se adapta a esa estructura.
## Archivos incorporados 

- `app/models.py`: clase `GameApp` y admisión del módulo `games` en la restricción
  de permisos. Los modelos anteriores se conservan.
- `app/schemas/games_apps.py`: contratos de creación, edición y respuesta.
- `app/routers/games_apps.py`: seis operaciones, ámbito explícito por organización,
  bloqueo de fila al modificar y auditoría mediante el servicio existente.
- `app/game_resources.py`: valida recursos locales y contexto RLS público.
- `app/game_static.py`: comprueba publicación antes de servir archivos; conserva
  `HEAD` público para la comprobación de disponibilidad del iframe aislado.
- `app/static/juegos/`: los 30 juegos, sus archivos compartidos y metadatos.
- `app/main.py`: registra el router y monta `/static` mediante `GameStaticFiles`.
  Se conserva `/demo` y su redirección inicial.
- `app/enums.py`: lectura de juegos para los cinco roles internos; modificación
  exclusivamente para `admin` y `editor`. Otros módulos no cambian sus permisos.
- `app/config.py`: parámetro opcional `ADMIN_GAME_PUBLIC_BASE_URL`.
- `vercel.json`: incluye los archivos de juegos en el paquete del servidor.
- `alembic/versions/0008_games.py`: migración de juegos,
  sin modificar las migraciones ni snapshots anteriores.
- `tests/games/`: pruebas aisladas del módulo y lógica de juegos.

## Aplicación al entorno de los colegas

1. Respaldar su base y comprobar que corresponde a esta API y su cadena Alembic.
2. Aplicar desde la raíz de este backend:

   ```powershell
   python -m alembic upgrade head
   ```

   Las migraciones crean `admin.games_apps`, su índice, aislamiento RLS con
   `FORCE ROW LEVEL SECURITY`, trigger de fecha y permisos SQL. Añaden únicamente
   los permisos de `games` a la matriz existente. Completan el catálogo de 30 juegos para
   cada organización existente: las fichas nuevas quedan **inactivas** y las
   fichas anteriores conservan sus datos y estados.
   No ejecutan `scripts.seed`, no vacían tablas ni modifican cuentas o contraseñas.
   Para organizaciones creadas después, registrar sus juegos mediante la API.

3. Cuando exista una dirección pública, configurar:

   ```ini
   ADMIN_GAME_PUBLIC_BASE_URL=https://DOMINIO-DEL-BACKEND
   ```

   Sin `/static` ni barra final. Vacío usa el origen de la petición y depende
   de que el proxy comunique correctamente HTTPS y dominio. CORS conserva la
   configuración propia de esta API: `ADMIN_CORS_ORIGINS`.

4. Reconstruir/desplegar este backend conforme a su operación habitual. No
   copiar variables, claves, cuentas ni conexiones de nuestro backend original.
5. Iniciar sesión con una cuenta de esta API con rol `admin` o `editor` y activar
   los juegos que se deseen publicar. No es necesario cambiar su autenticación.

**Esta entrega no ha aplicado migraciones a bases existentes, ejecutado seeds,
reiniciado servicios ni publicado cambios.**

## Rutas y consumo

Prefijo: `/api/v1/admin/games-apps`.

| Método y ruta | Acceso y uso |
|---|---|
| `GET /` | Cuenta interna autenticada: catálogo de su organización. Filtros `item_type` e `is_active`. |
| `POST /` | Admin/editor: crea una ficha inactiva. |
| `PUT /{id}` | Admin/editor: edita campos enviados. No cambia el estado de publicación. |
| `POST /{id}/toggle` | Admin/editor: activa o desactiva. |
| `DELETE /{id}` | Admin/editor: elimina físicamente la ficha y audita su estado previo. |
| `GET /active?tenant_id=UUID` | Público: solo juegos activos de la organización indicada. |

La organización administrativa se obtiene de la sesión autenticada; no se acepta
en el cuerpo de creación o edición. Los UUID de registros ajenos producen 404.
Las lecturas rechazan falta de sesión con 401 y las modificaciones sin permiso
con 403. Las escrituras y su auditoría se confirman o revierten juntas.

El catálogo público requiere explícitamente `tenant_id`; no elige la primera
organización ni mezcla sus juegos. Ese identificador de catálogo no es una
credencial. No pasar tokens del personal a la app móvil.

Un recurso local se registra en formato canónico:

```text
/static/juegos/memorama/index.html
```

La API devuelve una dirección con organización para abrirlo:

```text
https://DOMINIO-DEL-BACKEND/static/UUID-ORGANIZACION/juegos/memorama/index.html
```

La consola/app debe usar `target_url` tal como se recibe, sin reconstruirla ni
quitar el identificador de organización. La URL devuelta también se admite al
editar su propia ficha y se normaliza al guardar. Los estilos, scripts y recursos
compartidos relativos siguen funcionando bajo esa ruta.

Crear una ficha no crea archivos: un juego local debe existir previamente en
`app/static/juegos/<slug>/index.html`. También se admiten juegos externos con
HTTPS; se identifican con `resource_kind=external` y conservan su dirección.
AgeCare no puede bloquear la web externa ni garantizar que permita iframe.

La desactivación/eliminación bloquea la apertura de juegos locales y su acceso
directo sin organización. Una partida abierta comprueba su disponibilidad cada
cinco segundos y al recuperar visibilidad/foco; cuando detecta el bloqueo cambia
de documento y detiene el juego. No se añade captura de clics ni navegación.
`HEAD` de un recurso protegido admite `Origin: null` sin credenciales; no se
habilita ese origen para operaciones administrativas. Los juegos locales no
usan validación de suscripción: `min_tier` es un dato del catálogo, no un cobro.

