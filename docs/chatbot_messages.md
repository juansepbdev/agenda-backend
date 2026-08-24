# API de mensajes para el chatbot

Base URL: `https://api.tu-dominio.com/api/v1/inbox`. Este documento cubre todo lo que el flujo de n8n necesita para **escribir y leer mensajes** del CRM conversacional. Para agendar visitas, ver [chatbot_endpoints.md](chatbot_endpoints.md).

## Autenticación

Los endpoints de este documento no usan sesión: la empresa se resuelve **exclusivamente** desde la credencial del canal, nunca desde el cuerpo de la petición. Se envía en la cabecera `X-API-Key`:

```
X-API-Key: wak_a1b2c3d4e5f6...
```

Como alternativa para paneles de BSP que solo permiten personalizar la URL, se acepta `?api_key=`. **Usa siempre la cabecera si puedes**: un secreto en la URL acaba en los logs del proxy.

La credencial se guarda hasheada (SHA-256) y el valor en claro se muestra una sola vez, al generarla:

```bash
python manage.py inbox_channel --company <slug> --rotate-key \
    --ycloud-api-key <key> --ycloud-from +573001112233 \
    --n8n-url https://n8n.example/webhook/xxx
```

También se rota desde el admin de Django, con la acción «Generar credencial de webhook nueva». Una credencial inválida o de un canal inactivo devuelve `401 INVALID_WEBHOOK_CREDENTIAL`.

## Los tres caminos de entrada de un mensaje

| Endpoint | Quién lo llama | Qué hace de más |
|---|---|---|
| `POST /webhook/whatsapp/` | YCloud | Parsea el payload del BSP y **reenvía a n8n** si el chatbot está encendido |
| `POST /n8n/bot-reply/` | El agente de n8n | Persiste la respuesta **y la despacha por WhatsApp** vía YCloud |
| `POST /messages/` | Cualquiera con credencial | **Nada.** Solo escribe en el historial |

Elige según lo que necesites: si quieres que el backend envíe el mensaje por ti, usa `n8n/bot-reply/`. Si tu flujo ya lo envió por su cuenta y solo quieres que quede registrado, usa `messages/`.

---

## `POST /messages/` — guardar mensajes

El único endpoint que **solo escribe**: no llama a YCloud ni reenvía a n8n. Sirve para registrar lo que ya ocurrió en otro sitio — una respuesta que tu flujo despachó por su cuenta, una migración desde otro CRM, un histórico que rellenar.

Si no existen, crea el contacto y la conversación a partir del teléfono.

### Campos

| Campo | Alias aceptados | Por defecto |
|---|---|---|
| `content` | `text`, `body` | **obligatorio** |
| `phone` | `phone_number` | **obligatorio** si no hay `conversation_id` |
| `conversation_id` | — | alternativa a `phone` |
| `sender_type` | `sender` | `contact` — valores: `contact`, `bot`, `agent` |
| `direction` | — | se deduce del remitente |
| `status` | — | `received` si entra, `sent` si sale |
| `content_type` | `type` | `text` — valores: `text`, `system` |
| `wa_message_id` | `wamid` | — |
| `timestamp` | `created_at` | ahora |
| `name` | — | — (nombre del contacto, solo se usa al crearlo) |
| `mark_unread` | — | `true` en entrantes, `false` en salientes |

El teléfono se normaliza a `+` más dígitos: `"57 300-123 4567"` y `"+573001234567"` son el mismo contacto.

### Ejemplo

```bash
curl -X POST \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: wak_xxxxxxxx' \
  -d '{
    "phone": "+573001234567",
    "content": "Te confirmo la visita para el martes a las 3pm",
    "sender_type": "bot",
    "wa_message_id": "wamid.HBgMNTczMDA...",
    "timestamp": "2026-08-23T14:05:00Z"
  }' \
  https://api.tu-dominio.com/api/v1/inbox/messages/
```

Respuesta `201`:

```json
{
  "duplicate": false,
  "contact_id": "uuid",
  "conversation_id": "uuid",
  "display_id": 42,
  "chatbot_enabled": true,
  "message": {
    "id": 1837,
    "direction": "outbound",
    "message_type": 1,
    "sender_type": "bot",
    "content": "Te confirmo la visita para el martes a las 3pm",
    "status": "sent",
    "type": "text",
    "wa_message_id": "wamid.HBgMNTczMDA...",
    "contact_id": "uuid",
    "conversation_id": "uuid",
    "created_at": "2026-08-23T09:05:00-05:00",
    "updated_at": "2026-08-23T09:05:00-05:00"
  },
  "conversation": { "...": "..." }
}
```

### Guardar un mensaje entrante

Basta con omitir `sender_type` (o poner `contact`). Sube el contador de no leídos y actualiza `last_contact_at`:

```bash
curl -X POST -H 'Content-Type: application/json' -H 'X-API-Key: wak_xxxxxxxx' \
  -d '{"phone":"+573001234567","content":"Hola, quiero información","name":"Laura"}' \
  https://api.tu-dominio.com/api/v1/inbox/messages/
```

### Continuar una conversación concreta

Si ya tienes el `conversation_id` de una llamada anterior, úsalo en vez del teléfono:

```json
{"conversation_id": "uuid", "content": "Perfecto, quedamos así", "sender_type": "bot"}
```

### Lotes

`{"messages": [...]}` guarda hasta **200** de una vez. Un elemento inválido no tumba a los demás:

```bash
curl -X POST -H 'Content-Type: application/json' -H 'X-API-Key: wak_xxxxxxxx' \
  -d '{"messages":[
    {"phone":"+573001234567","content":"Hola","timestamp":"2026-08-23T14:00:00Z"},
    {"phone":"+573001234567","content":"¿En qué te ayudo?","sender_type":"bot","timestamp":"2026-08-23T14:00:20Z"}
  ]}' \
  https://api.tu-dominio.com/api/v1/inbox/messages/
```

Respuesta `201` si todo entró, o `207` si hubo fallos parciales:

```json
{
  "stored": [{"index": 0, "duplicate": false, "message": {"...": "..."}}],
  "errors": [{
    "index": 1,
    "error": {"code": "INBOX_VALIDATION_ERROR", "message": "El contenido del mensaje es obligatorio.", "details": {}}
  }],
  "counts": {"received": 2, "created": 1, "duplicated": 0, "failed": 1}
}
```

Los fallos vienen localizados por `index` para que puedas reintentar **solo** lo que falló.

### Comportamiento que conviene conocer

* **Idempotente por `wa_message_id`** dentro de la empresa: repetir la llamada devuelve `200` con `duplicate: true` y no inserta nada. Manda siempre el `wamid` si lo tienes — es lo que hace seguro reintentar cuando n8n falla a mitad.
* **Una `direction` incoherente con `sender_type` se rechaza** (`contact` + `outbound` → `400`) en vez de corregirse. Un histórico con la dirección mal puesta arruina la analítica del dashboard, así que es preferible que falle al escribir.
* **La conversación se reasigna** igual que en los flujos normales: `agent` la pasa a `me`, `bot` a `bot`, y un entrante con el chatbot encendido a `bot`. Los `content_type: "system"` son anotaciones y no reasignan nada.
* **No aplica la regla del interruptor.** El `chatbot_enabled` del contacto regula quién *puede enviar*, no quién puede *registrar lo ya enviado*: este endpoint escribe siempre.
* **Manda `timestamp` cuando importes histórico.** Sin él se usa la hora de la petición, y las series temporales del dashboard quedarán agrupadas en el día equivocado.

---

## `POST /n8n/bot-reply/` — responder y enviar

Persiste la respuesta del agente **y la despacha por WhatsApp**. Es el endpoint normal del bot en producción.

```bash
curl -X POST -H 'Content-Type: application/json' -H 'X-API-Key: wak_xxxxxxxx' \
  -d '{"phone":"+573001234567","text":"Claro, ¿qué día te viene bien?"}' \
  https://api.tu-dominio.com/api/v1/inbox/n8n/bot-reply/
```

| Campo | Alias | Obligatorio |
|---|---|---|
| `phone` | `phone_number` | sí |
| `text` | `content` | sí |
| `message_id`, `update_id` | — | no, solo se registran en log |

Respuesta `201` con `{"status": "ok", "message": {...}, "conversation": {...}, "ycloud_ok": true, "ycloud_error": null}`.

Si el contacto tiene el chatbot **apagado**, devuelve `200` con `{"status": "skipped", "reason": "chatbot_disabled"}` y no escribe nada: un asesor humano tomó la conversación. No es un error, no lo reintentes.

`ycloud_ok: false` significa que el mensaje **quedó guardado** pero WhatsApp no lo aceptó; el motivo viene en `ycloud_error` y el mensaje queda con `status: "failed"`.

## `POST /webhook/whatsapp/` — entrada desde YCloud

Lo configura el BSP, no tu flujo. Con la credencial válida **siempre responde 200**, incluso si el payload no era procesable (`{"status": "ignored", "reason": "..."}`): un error provocaría reintentos innecesarios de YCloud.

Solo procesa mensajes `type: "text"`; el resto se descarta con log. Reenvía a n8n una única vez por payload, y solo si algún mensaje es nuevo y su contacto tiene el chatbot encendido.

## Leer el historial

| Método y ruta | Uso |
|---|---|
| `GET /conversations/` | Lista ordenada por actividad descendente. `?filter=all\|me\|unassigned\|bot` |
| `GET /conversations/{uuid}/` | Conversación + últimos 100 mensajes + contacto |
| `GET /conversations/{uuid}/messages/` | Paginación por cursor: `limit` (1..200, def. 50), `after_id`, `before_id` |

Estos tres **sí exigen sesión de usuario**, no credencial de canal. Si tu flujo necesita releer el hilo, la vía es guardar el `conversation_id` que devuelve `POST /messages/`.

## Errores

Todas las respuestas de error usan el mismo envoltorio:

```json
{"error": {"code": "INBOX_VALIDATION_ERROR", "message": "El contenido del mensaje es obligatorio.", "details": {}}}
```

| Código | HTTP | Cuándo |
|---|---|---|
| `INVALID_WEBHOOK_CREDENTIAL` | 401 | `X-API-Key` ausente, inválida o de un canal inactivo |
| `COMPANY_INACTIVE` | 403 | La empresa está suspendida o cancelada |
| `INBOX_VALIDATION_ERROR` | 400 | Falta `content`, falta teléfono, `direction` incoherente, lote mayor de 200 |
| `CONVERSATION_NOT_FOUND` | 404 | El `conversation_id` no existe en esa empresa |
| `CHATBOT_ENABLED` | 403 | Solo en el envío del asesor desde el panel; no afecta a estos endpoints |

`details` lleva contexto accionable cuando lo hay, por ejemplo `{"received": "outbound", "allowed": ["inbound", "outbound"]}`.
