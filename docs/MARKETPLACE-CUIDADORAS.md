# Marketplace de cuidadoras: documentos, sincronización con la app y fidelización

Módulo aportado por el equipo del sitio público de AgeCare (Bryan Piña y Julio Muñoz, Duoc UC). Sigue las convenciones
del esquema `admin`: `tenant_id` en cada tabla, seguridad por filas forzada, historial inmutable y permisos por rol.
Migraciones: `0009_marketplace_documents` y `0010_marketplace_loyalty` (no están en `sql/agecare_admin_ddl.sql`, igual que
la de juegos: ese archivo reúne solo las migraciones base).

## Qué agrega

| Necesidad | Qué hace | Endpoints |
|---|---|---|
| La app envía el perfil que publica la cuidadora | Llega «pendiente» para revisión; reenviarlo actualiza datos descriptivos pero no pisa la decisión del staff | `POST /marketplace/caregivers/sync` |
| La cuidadora sube cédula, antecedentes o certificados | Quedan «pendientes»; un documento aprobado que se reemplaza queda como cambio pendiente sin pisar el vigente | `POST /marketplace/caregivers/documents/sync` |
| El staff revisa documentos | Aprueba o rechaza (rechazar exige motivo); revisar algo ya resuelto da 409 | `GET /marketplace/caregivers/{id}`, `POST .../documents/{tipo}/review` |
| Aprobar un perfil | Exige cédula y antecedentes aprobados (los perfiles sin documentos conservan la regla anterior) | `PATCH /marketplace/caregivers/{id}` |
| Fidelización | Reseña de 4–5★ suma 15 (10 por el trabajo + 5 de bono); 3★ suma 10; 2★ suma 5; 1★ resta 5. El total es la suma del historial | `POST /marketplace/reviews`, `POST .../reviews/sync`, `POST .../caregivers/{id}/points`, `GET .../caregivers/ranking` |
| Moderación | Cada reseña se ve por separado; se puede archivar (deja de contar, reversible) o eliminar (se borra con sus puntos) | `GET .../caregivers/{id}/reviews`, `PATCH`/`DELETE /marketplace/reviews/{id}` |
| Eliminar un perfil | Borra a la cuidadora con sus documentos, reseñas y puntos (cascada; el historial conserva quién y cuándo). Si la cuidadora borra su cuenta en la app, esta avisa con la clave interna | `DELETE /marketplace/caregivers/{id}`, `POST .../caregivers/{id}/remove` |

Las reglas de puntos están en `app/loyalty.py`.

## Sincronización con la app (servidor a servidor)

Los endpoints etiquetados «Sincronización con la app» no usan el JWT del staff: los llama el backend de la app con la
cabecera `X-Internal-Key`. Se configuran con:

| Variable | Por defecto | Para qué |
|---|---|---|
| `ADMIN_INTERNAL_SYNC_KEY` | vacía (desactivada: responde 503) | Clave compartida con la app |
| `ADMIN_INTERNAL_SYNC_TENANT` | `local` | `code` del tenant donde se guardan los datos de la app |

La app consulta de vuelta, con la misma clave: `GET /marketplace/caregivers/statuses` (estado de cada perfil),
`GET /marketplace/caregivers/documents?caregiver_id=` (decisiones sobre documentos) y `GET /marketplace/reviews/moderation`
(reseñas que el staff archivó o eliminó, para ocultarlas y recalcular la nota). `POST /marketplace/reviews/sync` es
idempotente por `review_id`: reintentar no vuelve a sumar puntos.

`caregiver_id` es el id del perfil en la app (referencia lógica, como ya indica la tabla `marketplace_caregivers`).

## Ejecutar las pruebas

Las tablas canónicas usan tipos de PostgreSQL (esquema `admin`, `ARRAY`, `CITEXT`), por lo que las pruebas del módulo
necesitan una base real, migrada y sembrada. Sin `TEST_DATABASE_URL` se saltan y el resto corre en SQLite como antes.

```bash
docker run -d --name agecare_pg -e POSTGRES_USER=agecare -e POSTGRES_PASSWORD=agecare -e POSTGRES_DB=agecare_admin -p 5441:5432 postgres:16-alpine
export ADMIN_DATABASE_URL=postgresql+asyncpg://agecare:agecare@localhost:5441/agecare_admin
alembic upgrade head && python -m scripts.seed
TEST_DATABASE_URL=$ADMIN_DATABASE_URL python -m pytest -q
```

Nota: `tests/test_support.py::test_create_unknown_user_needs_confirm` guarda estado y falla si se repite sobre la misma
base; usa una base nueva por ejecución completa.
