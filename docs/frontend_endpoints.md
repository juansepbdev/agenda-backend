# Endpoints para frontend

Base URL: `https://api.tu-dominio.com/api/v1`. Los endpoints internos requieren una sesión Django o Basic Auth. No envíes `company_id`: el backend obtiene la empresa de la persona autenticada. Las respuestas paginadas de listados siguen el formato DRF (`count`, `next`, `previous`, `results`). Este documento cubre **agenda y CRM**; para el dashboard de métricas y el inbox en vivo, ver [frontend_dashboard.md](frontend_dashboard.md).

## Autenticación

La instalación actual admite Basic Auth para desarrollo e integraciones de confianza. Una SPA en producción debe usar autenticación por sesión con CSRF o añadir JWT/OIDC antes de almacenar credenciales en el navegador.

```bash
curl -u "admin@inmobiliaria.co:password" \
  https://api.tu-dominio.com/api/v1/companies/current/
```

Devuelve la empresa actual, por ejemplo `{"id":"uuid","name":"Inmobiliaria Norte","timezone":"America/Bogota","status":"ACTIVE"}`.

## Permisos del usuario actual

`GET /users/me/permissions/` devuelve las capacidades efectivas para construir el menú y habilitar acciones de la interfaz. El frontend debe usar este resultado para UX, pero el backend continúa validando los permisos en cada operación.

```bash
curl -u "asesor@inmobiliaria.co:password" \
  https://api.tu-dominio.com/api/v1/users/me/permissions/
```

Respuesta `200`:

```json
{
  "user": {
    "id": "uuid",
    "email": "asesor@inmobiliaria.co",
    "full_name": "Carlos Pérez",
    "role": "ADVISOR",
    "company_id": "uuid"
  },
  "permissions": {
    "manage_users": false,
    "manage_advisors": false,
    "manage_supervisions": false,
    "manage_clients": true,
    "manage_scheduling_configuration": false,
    "view_company_indicators": false,
    "view_supervisor_indicators": false,
    "view_own_indicators": true,
    "view_all_company_events": false,
    "view_supervised_advisor_events": false,
    "view_own_events": true,
    "create_events": true,
    "reassign_events": false,
    "edit_advisor_availability": true,
    "cancel_events": true,
    "complete_events": true
  }
}
```

Las capacidades condicionadas por configuración (`create_events`, `reassign_events` y `edit_advisor_availability`) reflejan la configuración predeterminada activa de la empresa.

## Recursos administrativos

| Recurso | Ruta | Uso |
|---|---|---|
| Empresa actual | `GET`, `PATCH /companies/current/` | Perfil del tenant; no permite cambiar estado o plan. |
| Usuarios | `GET`, `POST /users/`; `GET`, `PATCH /users/{id}/`; `POST /users/{id}/activate/`, `deactivate/` | Solo ADMIN. |
| Asesores | `GET`, `POST /advisors/`; `GET`, `PATCH /advisors/{id}/`; acciones `activate`, `deactivate`, `availability-status` | Gestión de asesores. |
| Supervisiones | `GET`, `POST /supervisions/`; `GET`, `PATCH /supervisions/{id}/`; `POST /supervisions/{id}/deactivate/` | Solo ADMIN. |
| Clientes | `GET`, `POST /clients/`; `GET`, `PATCH /clients/{id}/`; `POST /clients/{id}/deactivate/`; `GET /clients/{id}/events/` | CRM de tenant. |
| Disponibilidad | `GET`, `POST /advisor-availabilities/`; `GET`, `PATCH`, `DELETE /advisor-availabilities/{id}/` | DELETE desactiva el bloque; no lo elimina físicamente. |
| Configuración | `GET`, `POST /scheduling-configurations/`; `GET`, `PATCH /scheduling-configurations/{id}/`; `GET /scheduling-configurations/default/` | Solo ADMIN. |

Ejemplo de creación de cliente:

```bash
curl -u "admin@inmobiliaria.co:password" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"first_name":"Laura","last_name":"Gómez","phone":"+573001234567","email":"laura@example.com","source":"MANUAL"}' \
  https://api.tu-dominio.com/api/v1/clients/
```

La respuesta es un cliente con UUID y `normalized_phone`; un teléfono solo es único dentro de su empresa.

## Eventos y agenda

| Método y ruta | Uso |
|---|---|
| `GET /events/?advisor={uuid}&status=PENDING&start_at__gte=...` | Listado visible al rol actual. |
| `POST /events/` | Crea evento manual y valida horario/conflicto. |
| `GET`, `PATCH /events/{id}/` | Consulta o edición permitida. |
| `POST /events/{id}/confirm/`, `/start/`, `/complete/`, `/cancel/`, `/no-show/` | Transiciones de estado. |
| `POST /events/{id}/reschedule/`, `/reassign/` | Reprograma (crea nuevo evento) o reasigna. |
| `GET /events/{id}/history/` | Auditoría inmutable. |
| `GET /calendar/day/?date=2026-08-10` | Eventos del día. |
| `GET /calendar/week/?start_date=2026-08-10` | Eventos de siete días. |
| `GET /calendar/month/?year=2026&month=8` | Eventos del mes. |

Crear evento manual:

```bash
curl -u "admin@inmobiliaria.co:password" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"advisor":"ADVISOR_UUID","client":"CLIENT_UUID","event_type":"PROPERTY_VISIT","title":"Visita apartamento","start_at":"2026-08-10T15:00:00-05:00","end_at":"2026-08-10T16:00:00-05:00","timezone":"America/Bogota"}' \
  https://api.tu-dominio.com/api/v1/events/
```

Respuesta: `201` con el evento, incluyendo `id`, `status: "PENDING"`, asesor, cliente y fechas. Si existe conflicto responde `400` con `{"error":{"code":"EVENT_CONFLICT",...}}`.

Cancelar:

```bash
curl -u "admin@inmobiliaria.co:password" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"reason":"El cliente no puede asistir","cancellation_source":"CLIENT"}' \
  https://api.tu-dominio.com/api/v1/events/EVENT_UUID/cancel/
```

Reprogramar:

```bash
curl -u "admin@inmobiliaria.co:password" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"start_at":"2026-08-11T15:00:00-05:00","end_at":"2026-08-11T16:00:00-05:00"}' \
  https://api.tu-dominio.com/api/v1/events/EVENT_UUID/reschedule/
```

La respuesta contiene el nuevo evento. El original queda `RESCHEDULED` y queda enlazado en historial.

El detalle completo y esquema OpenAPI está disponible en `/api/docs/` y `/api/schema/`.
