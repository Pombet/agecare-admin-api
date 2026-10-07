# Comprobar cambios de Swagger en Neon

API de prueba: https://agecare-admin-pruebas.vercel.app/api/v1/admin/docs

## Preparación

1. Iniciar sesión mediante `POST /auth/login` con la cuenta demo del README.
2. Pegar el `access_token` en **Authorize → BearerAuth → Value**, sin comillas
   ni prefijo `Bearer`, y confirmar.
3. En Neon, seleccionar el proyecto/base conectada a esta API. La base demo es
   `neondb`; usar el esquema **admin**. El prototipo de `public` conserva tablas
   con nombres parecidos y no es donde la API guarda estos cambios.
4. Después de ejecutar una escritura, actualizar la vista de tablas o volver
   a ejecutar la consulta en el editor SQL. Una consulta GET lee información;
   POST crea y PATCH modifica en estas operaciones.

## Crear un producto

Elegir **POST /api/v1/admin/marketplace/products** y **Try it out**. No requiere
ID: PostgreSQL genera un UUID, que aparece en la respuesta 201.

```json
{
  "name": "Prueba frontend Neon",
  "category": "Apoyo",
  "vendor": "Equipo AgeCare",
  "price_clp": 1500,
  "external_url": "https://example.com/producto",
  "image_url": null
}
```

Comprobar en Neon:

```sql
SELECT id, name, status, created_by, created_at, updated_at
FROM admin.marketplace_products
WHERE name = 'Prueba frontend Neon'
ORDER BY created_at DESC;
```

Para modificarlo, elegir **PATCH /marketplace/products/{product_id}** y pegar
el `id` de la respuesta de creación en el campo de parámetro `product_id`.
Un nombre o número inventado no sustituye al UUID. Ejemplo para publicar:

```json
{"status": "published"}
```

Para finalizar una prueba, se puede archivarlo con `{"status":"archived"}`.
Archivar conserva el registro y su historial; no elimina la fila.

## Crear y resolver un incidente

Elegir **POST /ops/incidents**. Tampoco requiere ID. Por ejemplo:

```json
{
  "title": "Incidente ficticio de prueba",
  "component_key": "database",
  "severity": "degraded",
  "description": "Prueba de persistencia en Neon; no representa una caída real.",
  "started_at": "2026-10-01T12:00:00Z",
  "is_maintenance": false
}
```

Para un incidente nuevo, usar una fecha de inicio reciente y no futura.
`GET /ops/incidents` filtra por los últimos 30 días por defecto: ajustar `days`
si se está buscando un registro más antiguo. Ese GET lista; no crea incidentes.

Comprobar en Neon:

```sql
SELECT id, title, status, started_at, resolution, resolved_at
FROM admin.ops_incidents
WHERE title = 'Incidente ficticio de prueba'
ORDER BY created_at DESC;
```

Después usar **PATCH /ops/incidents/{incident_id}**, con el UUID recibido:

```json
{
  "status": "resolved",
  "resolution": "Prueba completada; no corresponde a una falla real."
}
```

## Cómo interpretar errores

| Respuesta | Qué revisar |
| --- | --- |
| 200/201 | Operación correcta; verificar la fila por su ID en el esquema admin. |
| 401 | Iniciar sesión de nuevo y autorizar con el access token. |
| 403 | El rol no tiene permiso para esa operación. |
| 404 | El ID no existe en la base consultada. |
| 422 | Leer `error.details`: campo obligatorio, UUID o valor inválido. |
| 500 | Fallo del backend; conservar el `request_id` para diagnosticarlo. |

Crear un incidente registra el incidente. El estado global de `/ops/status`
se obtiene de las tablas de estado operativo y no cambia automáticamente por
crear una fila de incidente; el monitor es una integración distinta.
