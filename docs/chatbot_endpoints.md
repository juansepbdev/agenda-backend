# API para chatbot

Base URL: `https://api.tu-dominio.com/api/v1/integrations/chatbot`. Este documento cubre **agendar visitas**; para leer y escribir mensajes del CRM conversacional, ver [chatbot_messages.md](chatbot_messages.md). La empresa se determina exclusivamente con la identidad autenticada; **no se acepta `company_id`** en ningún payload. Actualmente el proyecto usa Basic Auth como mecanismo disponible; antes de conectar un proveedor externo, sustituirlo por una API key, firma HMAC o token de integración por empresa.

Todas las fechas deben incluir zona horaria ISO-8601, por ejemplo `2026-08-10T15:00:00-05:00`.

## 1. Consultar disponibilidad y prioridad

`POST /availability/` devuelve exclusivamente asesores activos de la empresa autenticada que aceptan asignación automática, respetan bloque de disponibilidad, buffers, límite diario y conflictos. Se ordenan por `priority` ascendente: un valor menor equivale a mayor prioridad.

```bash
curl -u "bot@inmobiliaria.co:password" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"start_at":"2026-08-10T15:00:00-05:00","duration_minutes":60}' \
  https://api.tu-dominio.com/api/v1/integrations/chatbot/availability/
```

Respuesta `200`:

```json
{
  "start_at": "2026-08-10T15:00:00-05:00",
  "end_at": "2026-08-10T16:00:00-05:00",
  "assignment_strategy": "PRIORITY",
  "available": true,
  "advisors": [{"id": "uuid", "name": "Carlos Pérez", "priority": 10}]
}
```

Si no hay disponibilidad, devuelve `200` con `available: false` y `advisors: []`; esto permite que el bot ofrezca otro horario.

## 2. Agendar visita automática

`POST /events/` reutiliza o crea el cliente por teléfono dentro de la empresa, selecciona un asesor usando la estrategia configurada (`FIRST_AVAILABLE`, `LEAST_EVENTS`, `ROUND_ROBIN`, `PRIORITY` o `RANDOM`) y crea el evento. `idempotency_key` es obligatorio: reintentar la misma solicitud devuelve el evento original, sin duplicar la reserva.

```bash
curl -u "bot@inmobiliaria.co:password" -X POST \
  -H 'Content-Type: application/json' \
  -d '{
    "idempotency_key":"conversation-123-message-456",
    "client":{"first_name":"Laura","last_name":"Gómez","phone":"+573001234567","email":"laura@example.com"},
    "event":{"start_at":"2026-08-10T15:00:00-05:00","duration_minutes":60,"title":"Visita apartamento","description":"Interesada en el inmueble","property_external_id":"PROP-123","property_code":"APT-902","property_title":"Apartamento Chapinero","property_address":"Bogotá","property_url":"https://example.com/properties/PROP-123"},
    "chatbot_conversation_id":"conversation-123",
    "chatbot_message_id":"message-456"
  }' \
  https://api.tu-dominio.com/api/v1/integrations/chatbot/events/
```

Respuesta nueva `201` (o `200` en reintento idempotente):

```json
{
  "id": "uuid",
  "status": "PENDING",
  "assigned_automatically": true,
  "advisor": {"id": "uuid", "name": "Carlos Pérez"},
  "client": {"id": "uuid", "name": "Laura Gómez"},
  "start_at": "2026-08-10T15:00:00-05:00",
  "end_at": "2026-08-10T16:00:00-05:00",
  "property_external_id": "PROP-123"
}
```

Si no hay asesor disponible devuelve `400` con `error.code: "ADVISOR_UNAVAILABLE"`. Una empresa suspendida recibe `403` con `COMPANY_INACTIVE`.

## 3. Cancelar una cita

`POST /events/{event_id}/cancel/` cancela un evento de la empresa autenticada. Un UUID de otra empresa responde `404`; no revela su existencia. No se puede cancelar un evento completado o ya reprogramado.

```bash
curl -u "bot@inmobiliaria.co:password" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"cancellation_reason":"El cliente solicitó cancelar por WhatsApp"}' \
  https://api.tu-dominio.com/api/v1/integrations/chatbot/events/EVENT_UUID/cancel/
```

Respuesta `200`: el mismo formato de evento con `status: "CANCELLED"`. El backend registra la acción en `EventHistory` con fuente `CHATBOT`.
