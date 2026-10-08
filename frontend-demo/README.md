# Demo de administración

Interfaz simple en HTML, CSS y JavaScript, sin dependencias ni compilación.
Sirve para presentar el avance del backend; no reemplaza el frontend del equipo.

- Resumen comercial, estado operativo y adopción.
- Productos: listado, filtros, creación, publicación y archivo.
- Incidentes: listado, filtros, creación y resolución.
- Soporte: indicadores, listado y detalle de tickets.
- Personal: consulta de cuentas y roles.
- Actividad: registro de auditoría.
- Detalles: UUID de la fila y consulta SQL para verificarla en Neon.

Las secciones y acciones se muestran según los permisos del usuario. El backend
aplica la autorización a cada solicitud. El selector de acceso ofrece las cuatro
cuentas ficticias documentadas en el README del proyecto.

## Ejecución

La interfaz se conserva para uso local. La API no sirve estos archivos en
/demo/ y .vercelignore los excluye del despliegue. No se agregan variables ni
claves de Neon al frontend.

Para servirlos localmente:

~~~powershell
python -m http.server 3000 --bind 127.0.0.1 --directory frontend-demo
~~~

Abrir http://localhost:3000. Los orígenes locales localhost y 127.0.0.1 se
reconocen como desarrollo, pero el permiso CORS actualmente está configurado
para **localhost:3000** y **localhost:5173**: abrir la URL con localhost.
En localhost se usa la API de prueba publicada.

## Sesiones y datos

Los tokens se mantienen únicamente en memoria, no en localStorage. El cliente
renueva el acceso al recibir 401 y conserva el refresh token rotado. Al recargar,
se solicita un nuevo login. «Cerrar sesión» intenta revocar la sesión en la API
y limpia la vista local.

No hay datos simulados en JavaScript: las tablas se consultan en el backend.
Las cuentas, métricas y demás datos de la base son de demostración. Crear,
publicar, archivar o resolver modifica esa base; usar nombres de prueba.

Los UUID los genera PostgreSQL. Los formularios de creación no piden ID.
La consulta SQL del detalle apunta al esquema admin. Los incidentes no
actualizan por sí solos las tablas del monitor operativo.
