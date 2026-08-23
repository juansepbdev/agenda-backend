# Referencia técnica — CRM Conversacional WhatsApp

Especificación completa de **modelo de datos, endpoints HTTP, contrato WebSocket, capa de servicios e integraciones externas** del backend.

Está escrita para **portar el sistema a otro backend** (FastAPI, NestJS, Laravel, Go…): cada sección describe primero el **contrato** (lo que debes replicar) y después **cómo lo resuelve la implementación Django actual** (lo que puedes cambiar).

> Documentos hermanos: [README.md](../README.md) (instalación y uso) · [DESARROLLO_CONTEXTO.md](../DESARROLLO_CONTEXTO.md) (arquitectura y decisiones) · [docs/N8N_INTEGRACION.md](N8N_INTEGRACION.md) (montaje del flujo n8n).

---

## Índice

1. [Vista general del sistema](#1-vista-general-del-sistema)
2. [Modelo de datos](#2-modelo-de-datos)
3. [Reglas de negocio transversales](#3-reglas-de-negocio-transversales)
4. [Endpoints HTTP](#4-endpoints-http)
5. [Contrato WebSocket](#5-contrato-websocket)
6. [Capa de servicios](#6-capa-de-servicios)
7. [Serializadores (shapes JSON)](#7-serializadores-shapes-json)
8. [Integración YCloud](#8-integración-ycloud)
9. [Integración n8n](#9-integración-n8n)
10. [Flujos end-to-end](#10-flujos-end-to-end)
11. [Configuración](#11-configuración)
12. [Checklist de reimplementación](#12-checklist-de-reimplementación)

---

## 1. Vista general del sistema

Tres actores externos y un backend que arbitra entre ellos:

```
   WhatsApp (cliente)
          │  mensaje
          ▼
     ┌─────────┐   webhook inbound    ┌──────────────┐
     │ YCloud  │ ───────────────────► │              │
     │  (BSP)  │ ◄─────────────────── │   BACKEND    │
     └─────────┘   POST envío saliente│              │
                                      │  contacts    │
   ┌──────┐   forward evento crudo    │  conversations│
   │ n8n  │ ◄──────────────────────── │  messages    │
   │ (IA) │ ──────────────────────►   │              │
   └──────┘   callback bot-reply      └──────────────┘
                                            │ ▲
                                   notifica │ │ lee por REST
                                     (WS)   ▼ │
                                      ┌──────────┐
                                      │ Dashboard│
                                      └──────────┘
```

**Principio central:** la base de datos es la única fuente de verdad. El WebSocket **solo notifica** que algo cambió; nunca transporta el contenido del mensaje. El cliente siempre re-consulta por REST. Esto hace que el frontend sea idempotente y tolerante a desconexiones (tiene *polling* de respaldo cada 5 s).

**Segundo principio:** el backend **nunca genera respuestas automáticas por su cuenta**. Si el chatbot está ON, reenvía el evento a n8n y espera un callback. Sin n8n configurado o sin respuesta, simplemente no hay mensaje saliente.

---

## 2. Modelo de datos

Tres tablas. Los nombres físicos son `contacts`, `conversations`, `messages` (fijados con `db_table`, no siguen la convención `app_modelo` de Django — respétalos si vas a compartir la misma base).

```
contacts 1 ──── N conversations 1 ──── N messages
    │                                       │
    └───────────── N ───────────────────────┘
              (denormalización: messages.contact_id)
```

`messages.contact_id` es **redundante** (se podría derivar vía `conversation`). Existe a propósito para permitir analítica por contacto sin `JOIN`:

```sql
SELECT * FROM messages WHERE contact_id = ? AND sender_type = 'contact';
```

### 2.1 `contacts`

| Columna | Tipo | Nulo | Default | Notas |
| --- | --- | --- | --- | --- |
| `id` | PK autoincremental | no | — | |
| `phone_number` | varchar(20) | no | — | **UNIQUE**, indexado. Clave natural del contacto. Siempre normalizado `+<dígitos>` |
| `name` | varchar(100) | no | `""` | Del `customerProfile.name` de WhatsApp; si falta, se usa el teléfono |
| `email` | varchar(150) | no | `""` | Solo edición manual |
| `country` | varchar(64) | no | `"Colombia"` | Decorativo |
| `avatar_initial` | varchar(2) | no | `""` | Derivado en `save()` |
| `avatar_color` | varchar(7) | no | `"#7C3AED"` | Hex |
| `chatbot_enabled` | bool | no | `true` | **Interruptor de negocio más importante del sistema** |
| `nickname` | varchar(128) | no | `""` | |
| `owner` | varchar(128) | no | `"—"` | Asesor asignado (texto libre, no FK a usuarios) |
| `source` | varchar(128) | no | `"Inbound message"` | |
| `source_id` | varchar(128) | no | `""` | Derivado: `wa_<teléfono sin +>` |
| `source_url` | varchar(512) | no | `""` | |
| `tags` | JSON (lista) | no | `[]` | |
| `notes` | text | no | `""` | |
| `created_at` | datetime | no | auto | |
| `updated_at` | datetime | no | auto | |
| `last_contact_at` | datetime | **sí** | `null` | Se actualiza en cualquier mensaje (entrante o saliente) |

**Lógica derivada al guardar** (`Contact.save()`, `inbox/models.py:54`) — replícala en un *hook* equivalente:

1. `phone_number` → `normalize_phone()`: conserva solo dígitos y antepone `+`. `"57 300-111 22 33"` → `"+573001112233"`. Cadena vacía si no hay dígitos.
2. `avatar_initial` vacío → primer carácter en mayúscula de `name`, si no de `phone_number`, si no `"?"`.
3. `avatar_color` vacío → `avatar_color_for_phone()`: `md5(teléfono)`, los primeros 8 hex → entero → `% 360` = matiz, convertido desde HSV(h, 0.55, 0.75) a hex.
4. `source_id` vacío → `wa_<teléfono sin +>`.

> **Nota:** como `avatar_color` tiene default `"#7C3AED"`, en la práctica nunca llega vacío a `save()` y el color por hash solo se aplica si asignas explícitamente `avatar_color = ""`. Si al portar quieres colores variados por contacto, llama a la función de hash directamente en la creación.

### 2.2 `conversations`

| Columna | Tipo | Nulo | Default | Notas |
| --- | --- | --- | --- | --- |
| `id` | PK | no | — | Identificador **interno**, el que usan las URLs de la API |
| `contact_id` | FK → `contacts` | no | — | `ON DELETE CASCADE`, indexado |
| `display_id` | integer | sí | derivado | **UNIQUE**. Número visible en la UI (`#7`) |
| `inbox` | varchar(128) | no | `"Inmobiliar-ia"` | Nombre de bandeja (aún no multi-bandeja) |
| `channel` | varchar(32) | no | `"whatsapp"` | |
| `status` | varchar(20) | no | `"open"` | `open` \| `resolved` \| `pending` |
| `assignment` | varchar(16) | no | `"unassigned"` | `unassigned` \| `me` \| `bot` |
| `unread_count` | integer ≥ 0 | no | `0` | |
| `last_message_preview` | varchar(255) | no | `""` | Denormalizado para la lista |
| `last_activity_at` | datetime | sí | `null` | **Indexado.** Clave de ordenamiento de la lista |
| `created_at` / `updated_at` | datetime | no | auto | |

**`display_id`** se calcula en `save()` como `MAX(display_id) + 1` (`inbox/models.py:111`). Es un contador de presentación separado del PK.

> ⚠️ **Al portar:** ese `MAX+1` tiene una condición de carrera bajo escrituras concurrentes (dos webhooks simultáneos pueden leer el mismo máximo y chocar contra el `UNIQUE`). En un backend con carga real usa una **secuencia de base de datos** o un `INSERT ... RETURNING` atómico.

**Orden de la lista:** `ORDER BY last_activity_at DESC, id DESC`. Comportamiento tipo WhatsApp: el chat con actividad más reciente arriba.

### 2.3 `messages`

Tabla única al estilo Chatwoot: entrantes y salientes conviven, diferenciadas por `message_type`.

| Columna | Tipo | Nulo | Default | Notas |
| --- | --- | --- | --- | --- |
| `id` | PK | no | — | **El orden cronológico es `id ASC`**, no `created_at` |
| `conversation_id` | FK → `conversations` | no | — | `ON DELETE CASCADE` |
| `contact_id` | FK → `contacts` | **sí** | — | `ON DELETE SET NULL` (a diferencia del CASCADE de arriba) |
| `content` | text | no | — | Cuerpo del mensaje |
| `message_type` | smallint | no | — | `0` = entrante (contacto) · `1` = saliente (bot/asesor) |
| `sender_type` | varchar(20) | no | `"contact"` | `contact` \| `bot` \| `agent` |
| `status` | varchar(20) | no | `"sent"` | `sent` \| `delivered` \| `read` \| `failed` \| `received` |
| `content_type` | varchar(16) | no | `"text"` | `text` \| `system` |
| `wa_message_id` | varchar(128) | sí | `null` | **UNIQUE.** `wamid` de WhatsApp: base de la idempotencia |
| `created_at` | datetime | no | auto | Indexado. **Se sobrescribe** con la marca de tiempo real de WhatsApp |
| `updated_at` | datetime | no | auto | |

**Combinaciones válidas:**

| Origen | `message_type` | `sender_type` | `status` inicial |
| --- | --- | --- | --- |
| Cliente por WhatsApp | `0` | `contact` | `received` |
| Respuesta del bot (n8n) | `1` | `bot` | `sent` → `failed` si YCloud falla |
| Respuesta del asesor | `1` | `agent` | `sent` → `failed` si YCloud falla |

**Campo derivado `direction`** (`inbox/models.py:214`) — lo consume el frontend para elegir la burbuja:

```
content_type == "system"  → "system"
message_type == 0         → "inbound"
resto                     → "outbound"
```

**Índices** (críticos para el rendimiento de la paginación por cursor):

```sql
CREATE INDEX idx_messages_conversation_id     ON messages (conversation_id);
CREATE INDEX idx_messages_conv_created        ON messages (conversation_id, created_at);
CREATE INDEX idx_messages_conv_id_desc        ON messages (conversation_id, id DESC);  -- el más usado
CREATE INDEX idx_messages_contact_created     ON messages (contact_id, created_at);
CREATE INDEX idx_messages_sender_created      ON messages (sender_type, created_at);
```

`idx_messages_conv_id_desc` es el que sostiene la consulta caliente («últimos N mensajes de esta conversación»).

---

## 3. Reglas de negocio transversales

Estas siete reglas **son el sistema**. Todo lo demás es plomería.

### R1 — El interruptor `chatbot_enabled` es excluyente

Por contacto, `chatbot_enabled` decide quién responde. Nunca ambos:

| Estado | Entrante reenviado a n8n | Asesor puede enviar | Callback de n8n aceptado |
| --- | --- | --- | --- |
| **ON** | sí | **no** → `403` | sí |
| **OFF** | no | sí | **no** → `status: "skipped"` |

El bloqueo del asesor se hace en el servicio, no en la UI (`messaging.send_agent_message` lanza `PermissionError`). El frontend además deshabilita el *composer*, pero la API es la que manda.

### R2 — Idempotencia por `wa_message_id`

Antes de insertar un entrante, se busca por `wa_message_id`. Si ya existe: **no se inserta nada**, no se emite WebSocket, no se reenvía a n8n, y se responde con el mensaje existente y `duplicate: true`.

Esto es indispensable: los BSP reintentan los webhooks. Sin esto, un reintento duplica el mensaje en el chat y dispara al bot dos veces.

Los mensajes sin `wa_message_id` (los de la ingesta manual de pruebas) **no se deduplican**.

### R3 — El `assignment` se mueve solo

| Evento | `assignment` resultante |
| --- | --- |
| Entrante con chatbot ON | `bot` |
| Entrante con chatbot OFF | *sin cambio* |
| Envío del asesor | `me` |
| Respuesta del bot vía n8n | `bot` |

### R4 — `unread_count`

`+1` con cada entrante. Se pone a `0` únicamente en `GET /api/conversations/<id>/` (que abre el chat). Los envíos salientes no lo tocan.

Al limpiarlo se hace un `UPDATE` dirigido que **no toca `last_activity_at`**, para que abrir un chat no lo reordene en la lista.

### R5 — El WebSocket no transporta contenido

Los eventos llevan identificadores y punteros, nunca el texto del mensaje. El cliente re-consulta la API. Ver [§5](#5-contrato-websocket).

### R6 — El envío saliente es síncrono y su resultado se persiste

Tras crear un mensaje saliente, la llamada HTTP a YCloud ocurre **dentro del mismo request**, fuera de la transacción de base de datos. Después:

- éxito → `status = "sent"` y se guarda el `wamid` devuelto (si no colisiona con otro registro)
- fallo → `status = "failed"`, se registra en el log, **la petición del cliente igual responde 2xx** con `ycloud_ok: false`

El mensaje queda en la conversación aunque WhatsApp lo rechace. La UI puede mostrar el estado fallido.

### R7 — El reenvío a n8n ocurre una vez por payload de webhook

Aunque un payload traiga varios mensajes, solo se hace **un** `POST` a n8n, con el **JSON crudo de YCloud sin modificar**, y solo si: n8n está configurado **y** el contacto tiene el chatbot ON **y** no es un duplicado.

---

## 4. Endpoints HTTP

Base: `http://<host>:8000`. Todo el cuerpo es JSON (`Content-Type: application/json`).

### 4.1 Tabla resumen

| Método | Ruta | Propósito | CSRF | Auth |
| --- | --- | --- | --- | --- |
| `GET` | `/` | Dashboard HTML | — | ninguna |
| `GET` | `/api/conversations/` | Lista de conversaciones | — | ninguna |
| `GET` | `/api/conversations/<id>/` | Detalle + mensajes + contacto (marca leído) | — | ninguna |
| `GET` | `/api/conversations/<id>/messages/` | Mensajes paginados por cursor | — | ninguna |
| `POST` | `/api/conversations/<id>/messages/` | Envío del asesor | **requerido** | ninguna |
| `POST` | `/api/contacts/<id>/chatbot/` | Interruptor del chatbot | **requerido** | ninguna |
| `POST` | `/api/messages/incoming/` | Ingesta manual (pruebas) | exento | ninguna |
| `GET`/`POST` | `/api/webhook/whatsapp/` | Webhook de YCloud | exento | token en `GET` |
| `POST` | `/api/n8n/bot-reply/` | Callback del bot | exento | ninguna |
| `*` | `/admin/` | Admin de Django | — | sesión |

> 🔒 **Ninguna ruta de la API exige autenticación.** El sistema asume que corre en una red de confianza o detrás de un túnel. **Al portar, añade autenticación**: sesión/JWT para las rutas del dashboard, y firma de webhook o secreto compartido para `/api/webhook/whatsapp/` y `/api/n8n/bot-reply/` — hoy cualquiera que alcance ese último endpoint puede inyectar mensajes salientes y hacerte gastar créditos de WhatsApp.

**Sobre CSRF:** dos endpoints exigen el par cookie `csrftoken` + cabecera `X-CSRFToken` porque los llama el navegador desde la misma sesión; los tres endpoints máquina-a-máquina están exentos. Si tu nuevo backend usa tokens en cabecera (sin cookies), esta distinción desaparece: protege los webhooks con un secreto en su lugar.

Ejemplo con `curl` para un endpoint protegido por CSRF:

```bash
curl -s -c cj.txt http://127.0.0.1:8000/ -o /dev/null
TOKEN=$(grep csrftoken cj.txt | awk '{print $7}')
curl -b cj.txt -H "X-CSRFToken: $TOKEN" -H "Referer: http://127.0.0.1:8000/" \
     -X POST http://127.0.0.1:8000/api/contacts/13/chatbot/ \
     -H "Content-Type: application/json" -d '{"enabled":false}'
```

### 4.2 `GET /api/conversations/`

Lista completa, ordenada por actividad descendente. Sin paginación.

**Query params**

| Param | Valores | Default | Efecto |
| --- | --- | --- | --- |
| `filter` | `all` \| `me` \| `unassigned` \| `bot` | `all` | Filtra por `assignment` |

**200**

```json
{
  "conversations": [
    {
      "id": "12",
      "display_id": 6,
      "contact_id": "12",
      "inbox": "Inmobiliar-ia",
      "channel": "whatsapp",
      "status": "open",
      "assignment": "me",
      "unread_count": 0,
      "last_message_preview": "Ok, hablamos luego",
      "last_message_day": "15:13",
      "last_activity_at": "2026-08-20T15:13:31.287151+00:00",
      "last_message_at": "2026-08-20T15:13:31.287151+00:00",
      "contact": { "...": "objeto contacto completo, ver §7" }
    }
  ]
}
```

`last_message_at` es un alias heredado de `last_activity_at`; se mantiene por compatibilidad con JS antiguo. En un backend nuevo puedes emitir solo uno.

### 4.3 `GET /api/conversations/<id>/`

Carga inicial de un chat: conversación + últimos 100 mensajes + contacto. **Efecto secundario: pone `unread_count` a `0`.**

**200**

```json
{
  "conversation": { "...": "ver §7.3" },
  "messages":     [ "...", "hasta 100, orden ASC" ],
  "contact":      { "...": "ver §7.1" }
}
```

**404** — `{"error": "Conversation not found"}` (también si el `id` no es numérico).

### 4.4 `GET /api/conversations/<id>/messages/`

Paginación por cursor, **sin `OFFSET`**.

**Query params**

| Param | Tipo | Default | Comportamiento |
| --- | --- | --- | --- |
| `limit` | int | `50` | Acotado a `[1, 200]`. Un valor no numérico cae a `50` |
| `after_id` | int | — | `id > after_id`, orden `ASC`. **Anexar mensajes nuevos en vivo** |
| `before_id` | int | — | `id < before_id`, se consulta `DESC` y se invierte. **Scroll hacia arriba** |
| *(ninguno)* | | | Últimos `limit` mensajes en orden `ASC` |

`after_id` tiene prioridad sobre `before_id` si envías ambos. La respuesta **siempre** viene en orden cronológico ascendente.

**200** — `{"messages": [ ... ]}` · **404** si la conversación no existe.

> Por qué cursor y no `OFFSET`: con `OFFSET` el desplazamiento se corrompe cuando llegan mensajes nuevos mientras el usuario hace scroll, y el coste crece linealmente. El cursor por `id` es estable y usa el índice directamente.

### 4.5 `POST /api/conversations/<id>/messages/`

Envío del asesor. **Requiere CSRF.**

**Petición** — `{"content": "texto"}`

**201**

```json
{
  "message":      { "id": 38, "direction": "outbound", "sender_type": "agent", "status": "sent", "...": "..." },
  "conversation": { "...": "actualizada, assignment ahora 'me'" },
  "ycloud_ok":    true,
  "ycloud_error": null
}
```

**Errores**

| Código | Cuerpo | Causa |
| --- | --- | --- |
| `400` | `{"error": "Invalid JSON"}` | Cuerpo mal formado |
| `400` | `{"error": "content is required"}` | Vacío o solo espacios |
| `403` | `{"error": "Chatbot is ON — turn it off to reply manually."}` | **R1** |
| `404` | `{"error": "Conversation not found"}` | No existe |

> ⚠️ La vista captura `Exception` de forma amplia y la convierte en `404` (`inbox/views.py:81`). Cualquier fallo interno inesperado se disfraza de «no encontrado». Al portar, separa el «no existe» de los errores reales (`500`).

Nota: `ycloud_ok: false` **no** cambia el código de respuesta. El mensaje se guardó; solo no llegó a WhatsApp.

### 4.6 `POST /api/contacts/<id>/chatbot/`

Interruptor del bot. **Requiere CSRF.** Emite el evento WebSocket `contact.updated`.

**Petición** — `{"enabled": true|false}` (el valor pasa por `bool()`, así que `1`/`0`/`"x"` también funcionan)

**200** — `{"contact_id": "13", "chatbot_enabled": false}`

**400** si falta la clave `enabled` · **404** si el contacto no existe.

### 4.7 `POST /api/messages/incoming/`

Ingesta manual para probar sin WhatsApp. Exento de CSRF.

**Petición** (los alias existen por compatibilidad)

```json
{
  "phone":   "573001234567",     // o "phone_number" — obligatorio
  "content": "Hola",             // o "message" — obligatorio
  "name":    "Nombre opcional",
  "wa_message_id": "wamid.opcional"
}
```

**200**

```json
{
  "status": "ok",
  "reply": null,
  "conversation_id": 13,
  "display_id": 7,
  "chatbot_enabled": true,
  "contact_id": 13,
  "inbound":  { "...": "el mensaje persistido" },
  "outbound": null,
  "duplicate": false
}
```

> **Este endpoint NO reenvía a n8n.** Solo persiste y notifica por WebSocket. El reenvío al bot vive únicamente en el webhook de YCloud. Para probar el bot de punta a punta usa `/api/webhook/whatsapp/` con un payload simulado.
>
> `reply` y `outbound` son siempre `null`: son restos de una versión anterior con auto-respuesta local. Elimínalos al portar.

### 4.8 `GET|POST /api/webhook/whatsapp/`

#### `GET` — verificación estilo Meta

| Query param | Valor esperado |
| --- | --- |
| `hub.mode` | `subscribe` |
| `hub.verify_token` | debe coincidir con `WHATSAPP_VERIFY_TOKEN` |
| `hub.challenge` | se devuelve tal cual |

**200** con el `challenge` en `text/plain`, o **403** `{"error": "Verification failed"}`.

YCloud normalmente no usa este *handshake*; está para compatibilidad con la Cloud API de Meta.

#### `POST` — recepción de eventos

Acepta **dos formatos** (ver el parser en [§6.1](#61-messagingpy--el-núcleo)) y **siempre responde 200**, incluso si nada resultó procesable: un webhook que devuelve error provoca reintentos innecesarios del BSP.

**200**

```json
{
  "status": "ok",
  "event_type": "whatsapp.inbound_message.received",
  "processed": [
    {
      "contact_id": 13,
      "conversation_id": 13,
      "display_id": 7,
      "chatbot_enabled": true,
      "reply": null,
      "inbound": { "...": "mensaje persistido" },
      "outbound": null,
      "duplicate": false,
      "ycloud_ok": null,
      "n8n_forward": { "ok": false, "error": "..." }
    }
  ],
  "n8n_forward": { "ok": false, "error": "...", "status_code": null }
}
```

- `processed` es una lista (un payload puede traer varios mensajes); queda vacía si el evento se ignoró.
- `n8n_forward` aparece a nivel raíz **solo si hubo intento de reenvío** (R7).
- `ycloud_ok: null` significa «no se intentó ningún envío», que es lo normal en un entrante.

### 4.9 `POST /api/n8n/bot-reply/`

Callback del agente de IA. Persiste un mensaje `sender_type: "bot"` y lo despacha por YCloud. Exento de CSRF.

**Petición**

| Campo | Obligatorio | Notas |
| --- | --- | --- |
| `phone` | sí | Se normaliza. Es la **clave de correlación**: el contacto se busca (o crea) por teléfono |
| `text` | sí | Alias aceptado: `content` |
| `message_id` | no | Solo para logs |
| `update_id` | no | Solo para logs |

**201 — enviado**

```json
{
  "status": "ok",
  "message":      { "id": 39, "sender_type": "bot", "status": "sent", "...": "..." },
  "conversation": { "...": "..." },
  "contact_id": 13,
  "conversation_id": 13,
  "ycloud_ok": true,
  "ycloud_error": null
}
```

**200 — omitido** (chatbot OFF, R1)

```json
{
  "status": "skipped",
  "reason": "chatbot_disabled",
  "conversation": { "...": "..." },
  "contact_id": 13,
  "conversation_id": 13
}
```

**400 — error**

```json
{ "status": "error", "reason": "phone_required" }   // o "text_required"
```

Si falta cualquiera de los dos campos, la vista corta antes con `{"error": "phone and text are required"}`.

> ⚠️ La correlación es **solo por número de teléfono**, no por identificador de mensaje. Si el mismo contacto escribe dos veces rápido, las respuestas del bot pueden llegar desordenadas y no hay forma de emparejar cada respuesta con su pregunta. `message_id` viaja pero solo se escribe en el log. Si tu caso lo necesita, persiste ese campo y añade una columna `in_reply_to`.

---

## 5. Contrato WebSocket

**URL:** `ws://<host>/ws/inbox/` · **Grupo de difusión:** `inbox_updates` (uno global, no por conversación).

Todos los clientes conectados reciben todos los eventos y filtran del lado del cliente. Simple, pero **no escala ni aísla datos entre asesores**: si añades multi-usuario, usa un grupo por conversación o por bandeja.

**Formato del sobre**

```json
{ "event": "<nombre>", "payload": { } }
```

| Evento | Payload | Qué debe hacer el cliente |
| --- | --- | --- |
| `connected` | `{"group": "inbox_updates"}` | Confirmación al conectar |
| `messages.updated` | `{"conversation_id": "13", "after_id": 37, "display_id": 7}` | `GET .../messages/?after_id=<after_id>` y anexar |
| `conversations.changed` | `{}` | Re-consultar `GET /api/conversations/` |
| `contact.updated` | `{"contact": { ...contacto completo... }}` | Refrescar el panel si es el contacto activo |

`contact.updated` es la única excepción a R5: sí lleva el objeto completo, porque es pequeño y no requiere paginación.

Cada persistencia de mensaje emite **dos** eventos: `messages.updated` y `conversations.changed`.

**Sobre `after_id` en los entrantes:** se calcula como `id_del_nuevo_mensaje - 1` (`inbox/services/messaging.py:254`). Asume que los IDs son contiguos. Si otra escritura se intercala, el cliente puede volver a pedir un mensaje que ya tenía — inofensivo aquí porque el frontend deduplica por `data-message-id`, pero **al portar es más robusto enviar el ID del último mensaje conocido *anterior* al insertado**, obtenido con `MAX(id)` antes de insertar (que es justo lo que sí hacen las rutas salientes).

**Respaldo por polling:** si el WebSocket cae, el frontend consulta cada `pollIntervalMs` (5000 ms) con el mismo `after_id`. El WebSocket es una optimización, no un requisito: **un backend nuevo puede omitirlo por completo** y el dashboard seguirá funcionando con polling.

---

## 6. Capa de servicios

Las vistas solo hacen *parsing* de HTTP y traducción de errores. Toda la lógica vive en `inbox/services/`.

```
views.py
   │
   ▼
messaging.py ──┬──► models (ORM)
               ├──► ycloud.py    (HTTP saliente a WhatsApp)
               ├──► n8n.py       (HTTP saliente al bot)
               ├──► realtime.py  (difusión WebSocket)
               └──► serializers.py
```

Las dependencias fluyen en una sola dirección: `ycloud`, `n8n`, `realtime` y `serializers` **no se importan entre sí** ni conocen a `messaging`. Esta es la parte que más conviene conservar al portar.

### 6.1 `messaging.py` — el núcleo

566 líneas. Funciones públicas:

| Función | Responsabilidad |
| --- | --- |
| `parse_whatsapp_webhook(payload)` | Normaliza el JSON del BSP a una lista de eventos planos |
| `ingest_inbound_text(...)` | Persiste un entrante (R2, R3, R4) |
| `send_agent_message(conv_id, content)` | Envío del asesor (R1, R6) |
| `send_bot_reply_from_n8n(...)` | Callback del bot (R1, R6) |
| `set_chatbot_enabled(contact_id, bool)` | Interruptor + difusión |
| `list_conversations(filter_id)` | Lista filtrada y ordenada |
| `list_messages(conv_id, ...)` | Paginación por cursor |
| `get_conversation_payload(conv_id, mark_read)` | Carga de un chat (R4) |
| `dashboard_context(active_id, filter_id)` | Ensambla el render inicial del HTML |
| `get_or_create_contact_conversation(phone, name)` | Resolución de contacto+conversación por teléfono |
| `get_or_create_open_conversation(contact)` | Última conversación `open`, o crea una |

#### `parse_whatsapp_webhook` — normalización de entrada

Salida: lista de dicts con `phone`, `name`, `text`, `wa_message_id`, `timestamp` (+ `business_phone` y `ycloud_message_id` en la rama YCloud).

Ramas de decisión:

| `payload.type` | Acción |
| --- | --- |
| `whatsapp.inbound_message.received` | **Ruta de producción.** Lee `whatsappInboundMessage` |
| `whatsapp.smb.message.echoes` | Se ignora explícitamente (registra log). Son los ecos de mensajes salientes; procesarlos duplicaría el hilo |
| otro valor de `type` | Se ignora con log |
| *sin `type`* | **Respaldo Meta Cloud API:** recorre `entry[].changes[].value.messages[]` |

En ambas ramas, **solo se aceptan mensajes `type: "text"`**. Imágenes, audio, ubicación y documentos se descartan silenciosamente (con log). Ampliar a multimedia es el punto de extensión más obvio: exige una columna de adjuntos y un `content_type` nuevo.

Del payload de YCloud se leen: `from`, `text.body`, `wamid` (con respaldo a `id`), `customerProfile.name`, `sendTime`, `to`. Se parsean pero **no se persisten**: `business_phone` y `ycloud_message_id`.

#### `_parse_wa_timestamp` — marcas de tiempo

Acepta tres formatos, en este orden: ISO-8601 con `Z` (se convierte a `+00:00`), *epoch* en segundos como entero o cadena, o `None`/vacío → ahora. Cualquier cosa naíf se asume UTC.

Se usa para que el mensaje quede fechado con **la hora real de WhatsApp**, no la de recepción del webhook. Como Django fija `created_at` con `auto_now_add`, el código inserta y después hace un `UPDATE` dirigido para sobrescribirlo (`inbox/services/messaging.py:251`). En otro backend basta con asignar la columna directamente en el `INSERT`.

#### `ingest_inbound_text` — el camino caliente

```
1. Si hay wa_message_id y ya existe → devolver el existente, duplicate: true, FIN   (R2)
2. Abrir transacción
3. Resolver contacto por teléfono (crear si no existe, chatbot_enabled = true)
4. Resolver conversación 'open' del contacto (crear si no hay)
5. Insertar mensaje (type 0, sender contact, status received)
6. Sobrescribir created_at con la marca de WhatsApp
7. Si chatbot ON → assignment = 'bot'                                              (R3)
8. Actualizar conversación: preview, last_activity_at, unread_count + 1            (R4)
9. Actualizar contact.last_contact_at
10. Cerrar transacción
11. Difundir messages.updated + conversations.changed                              (R5)
```

Los pasos 3–9 son atómicos. La difusión ocurre **fuera** de la transacción, deliberadamente: si se emitiera dentro, un cliente podría re-consultar antes del *commit* y no encontrar el mensaje.

Un contacto nuevo nace con `chatbot_enabled = true`: por defecto, el bot atiende a los desconocidos.

#### `send_agent_message` / `send_bot_reply_from_n8n`

Comparten estructura:

```
1. Transacción: verificar el interruptor del chatbot (R1)
2. Capturar after_id = MAX(id) actual de la conversación   ← antes de insertar
3. Insertar mensaje saliente (type 1)
4. Actualizar preview / last_activity_at / assignment
5. Cerrar transacción
6. POST a YCloud (fuera de la transacción)                  (R6)
7. Aplicar el resultado: status sent|failed, guardar wamid
8. Difundir
```

Diferencias: `send_agent_message` recibe un `conversation_id` y **lanza** `PermissionError` si el chatbot está ON; `send_bot_reply_from_n8n` recibe un **teléfono**, resuelve/crea el contacto, y **devuelve** `status: "skipped"` en lugar de lanzar excepción (un webhook no debe recibir un error por una regla de negocio esperada).

La llamada HTTP fuera de la transacción es intencional: mantener una transacción abierta durante una petición de red de hasta 30 s bloquearía la base de datos (en SQLite, a todo el proceso).

> ⚠️ **Al portar:** ese `POST` síncrono ata la latencia de tu API a la de WhatsApp. Con volumen real, encola el envío (Celery, BullMQ, una tabla *outbox*) y reconcilia el estado con los webhooks de entrega de YCloud.

#### `_apply_ycloud_send_result`

Traduce el resultado del envío al registro persistido. El detalle no obvio: antes de guardar el `wamid` comprueba que **ningún otro** registro lo tenga ya, porque la columna es `UNIQUE` y un choque abortaría la escritura.

#### `list_messages` — paginación por cursor

```python
limit = max(1, min(limit or 50, 200))

if after_id:   WHERE id > ? ORDER BY id ASC  LIMIT ?          # en vivo
elif before_id: WHERE id < ? ORDER BY id DESC LIMIT ?  → invertir  # scroll arriba
else:           ORDER BY id DESC LIMIT ?               → invertir  # carga inicial
```

La inversión en memoria es lo que permite pedir «los últimos N» usando el índice descendente y aun así devolverlos en orden cronológico.

#### `dashboard_context`

Solo para el render del HTML inicial. Selecciona la conversación activa con este orden de preferencia: el `?conversation=` de la URL → la primera de la lista filtrada → ninguna. Si un filtro deja la lista vacía, **retrocede automáticamente a `all`**. Si portas a una SPA, esta función desaparece.

### 6.2 `ycloud.py` — salida a WhatsApp

Una sola función pública. Ver [§8](#8-integración-ycloud).

```python
send_whatsapp_text(*, to: str, body: str, from_number: str | None = None) -> dict
```

**Nunca lanza excepciones.** Devuelve siempre la misma forma:

```python
{"ok": bool, "error": str | None, "wamid": str | None, "raw": Any, "status_code": int | None}
```

Cortocircuita con `ok: false` (sin gastar una petición) cuando: falta `YCLOUD_API_KEY`, o falta el remitente, el destinatario o el cuerpo. Timeout de 30 s.

### 6.3 `n8n.py` — puente al bot

```python
is_configured() -> bool                       # ¿hay N8N_WEBHOOK_URL?
forward_ycloud_event(payload: dict) -> dict   # {"ok", "error", "status_code"}
```

Reenvía el JSON **crudo, sin transformar**, tal como llegó de YCloud. Decisión deliberada: n8n hace su propio *parsing* y así el backend no impone un esquema al flujo del bot.

*Fire-and-forget* respecto a la lógica de negocio: cualquier fallo se registra y se devuelve como `ok: false`, pero el webhook responde 200 igual. Timeout configurable (5 s por defecto) — corto a propósito, porque bloquea la respuesta al webhook de YCloud.

### 6.4 `realtime.py` — difusión

```python
broadcast_event(event_type: str, payload: dict)
broadcast_messages_updated(*, conversation_id, after_id, display_id=None)
broadcast_conversations_changed()
broadcast_contact_updated(contact_data: dict)
```

Si no hay capa de canales configurada, `broadcast_event` retorna en silencio: el sistema **degrada sin romper**.

> ⚠️ La capa de canales es `InMemoryChannelLayer` (`dashboard/settings.py:72`). Funciona **solo con un proceso**. Con varios *workers*, un mensaje recibido por el worker A no llega a los clientes conectados al worker B (el polling lo tapa, con retraso). Para producción multiproceso: `channels_redis`, o el equivalente en tu stack (Redis pub/sub, NATS, Postgres `LISTEN/NOTIFY`).

### 6.5 `serializers.py`

Funciones puras ORM → `dict`. Ver [§7](#7-serializadores-shapes-json).

---

## 7. Serializadores (shapes JSON)

Reglas generales: **los IDs se emiten como cadenas** (`"13"`), salvo `message.id`, que es entero — el frontend hace aritmética con él para los cursores. Las fechas ISO llevan zona; las «de presentación» ya vienen formateadas en la zona horaria del servidor (`TIME_ZONE`, hoy `UTC`).

> Emitir campos ya formateados acopla el backend a la localización del cliente. **Al portar, considera enviar solo ISO-8601 y formatear en el frontend.**

### 7.1 Contacto

```json
{
  "id": "12",
  "name": "Diego Ramirez",
  "phone": "+573169998877",
  "phone_number": "+573169998877",
  "email": "",
  "country": "Colombia",
  "avatar_initial": "D",
  "avatar_color": "#D97706",
  "chatbot_enabled": false,
  "nickname": "Dieguito",
  "owner": "Andres",
  "source": "Inbound message",
  "source_id": "wa_573169998877",
  "source_url": "",
  "create_time": "2026-08-20 16:14",
  "last_contact": "2026-08-20 15:13",
  "tags": [],
  "notes": "Follow up next week."
}
```

`phone` y `phone_number` son el mismo valor duplicado (compatibilidad). `last_contact` es `"—"` cuando es nulo.

### 7.2 Mensaje

```json
{
  "id": 39,
  "direction": "outbound",
  "message_type": 1,
  "sender_type": "bot",
  "content": "respuesta bot",
  "timestamp": "16:25",
  "status": "failed",
  "type": "text",
  "created_at": "2026-08-20T16:25:42.037287+00:00",
  "updated_at": "2026-08-20T16:25:42.042389+00:00",
  "wa_message_id": null,
  "contact_id": "13",
  "conversation_id": "13"
}
```

`direction` es derivado (§2.3) · `type` es `content_type` renombrado · `timestamp` es `HH:MM` local.

### 7.3 Conversación

Ver el ejemplo de [§4.2](#42-get-apiconversations). `include_contact=False` omite la clave `contact` anidada.

`last_message_day` usa formato relativo: `HH:MM` si es de hoy, si no el día abreviado (`Mon`, `Tue`…). No distingue una semana atrás de un año atrás — corregible al portar.

---

## 8. Integración YCloud

### 8.1 Entrada — webhook

Configuración en el panel de YCloud:

- **URL:** `https://<tu-host>/api/webhook/whatsapp/`
- **Evento suscrito:** `whatsapp.inbound_message.received`

Payload real (recortado a lo que se usa):

```json
{
  "id": "evt_1",
  "type": "whatsapp.inbound_message.received",
  "createTime": "2026-08-20T16:00:00.000Z",
  "whatsappInboundMessage": {
    "id": "msg_abc",
    "wamid": "wamid.HBgMNTczMDAxMjM0NTY3",
    "from": "573001234567",
    "to": "+573001111111",
    "type": "text",
    "text": { "body": "Mensaje via webhook" },
    "customerProfile": { "name": "Doc Tester" },
    "sendTime": "2026-08-20T16:00:00.000Z"
  }
}
```

| Campo | Destino |
| --- | --- |
| `whatsappInboundMessage.from` | `contacts.phone_number` (normalizado) |
| `whatsappInboundMessage.text.body` | `messages.content` |
| `whatsappInboundMessage.wamid` | `messages.wa_message_id` (**clave de idempotencia**) |
| `customerProfile.name` | `contacts.name` (solo si el contacto aún no tiene nombre propio) |
| `sendTime` | `messages.created_at` |
| `to` | *parseado, no persistido* |
| `id` | *parseado, no persistido* |

**Regla sobre el nombre:** solo se sobrescribe si el contacto no tiene nombre o si su nombre es igual a su teléfono. Un nombre editado a mano en el CRM **no se pisa** con el del perfil de WhatsApp.

### 8.2 Salida — API de mensajes

```http
POST https://api.ycloud.com/v2/whatsapp/messages
X-API-Key: <YCLOUD_API_KEY>
Content-Type: application/json

{ "from": "+57...", "to": "+57...", "type": "text", "text": { "body": "..." } }
```

El identificador devuelto se lee de `wamid`, con respaldo a `id`, y se guarda en el mensaje.

> ⚠️ **Solo texto plano.** No hay soporte de plantillas (*templates*). Fuera de la ventana de servicio de 24 h de WhatsApp, los mensajes de texto libre **son rechazados** por la plataforma: el mensaje quedará como `failed`. Un backend de producción necesita soporte de plantillas para reactivar conversaciones.

### 8.3 Ventanas de fallo a manejar

| Situación | Comportamiento actual |
| --- | --- |
| `YCLOUD_API_KEY` vacía | No se llama a la API; mensaje queda `failed`; `ycloud_error` lo explica |
| YCloud devuelve ≥400 | `failed`, se registra el cuerpo, la API responde 2xx igual |
| Timeout de red (30 s) | `failed` |
| `wamid` ya existente | El mensaje se guarda `sent` pero **sin** `wa_message_id` |

---

## 9. Integración n8n

Contrato de dos tramos. El detalle operativo (nodos, Docker, capturas) está en [docs/N8N_INTEGRACION.md](N8N_INTEGRACION.md).

### Tramo 1 — Backend → n8n

`POST {N8N_WEBHOOK_URL}` con el **JSON crudo de YCloud** y timeout `N8N_WEBHOOK_TIMEOUT` (5 s).

Condiciones para disparar (las tres, R7): n8n configurado **y** contacto con chatbot ON **y** el mensaje no es duplicado. Una vez por payload.

### Tramo 2 — n8n → Backend

`POST /api/n8n/bot-reply/` con:

```json
{
  "phone": "573001234567",
  "text": "Respuesta generada por el agente",
  "message_id": "opcional, solo logs",
  "update_id": "opcional, solo logs"
}
```

**Reglas del flujo n8n** (importantes si lo reconstruyes):

1. **Quitar** cualquier nodo que envíe el mensaje directamente a YCloud desde n8n. El envío es responsabilidad exclusiva del backend; si ambos envían, el cliente recibe el mensaje duplicado.
2. **Conservar** el nodo Typing Indicator (efecto visual, no envía contenido).
3. Con n8n en Docker y el backend en el host, la URL del callback es `http://host.docker.internal:8000/api/n8n/bot-reply/` y **`host.docker.internal` debe estar en `DJANGO_ALLOWED_HOSTS`**.

**El bot no tiene memoria del lado del backend.** No se envía historial de conversación en el reenvío: n8n recibe únicamente el mensaje actual. Si el agente necesita contexto, debe mantenerlo él (nodo de memoria) o consultar el backend por su cuenta.

---

## 10. Flujos end-to-end

### A) Cliente escribe · chatbot ON · n8n operativo

```
Cliente → WhatsApp → YCloud
  → POST /api/webhook/whatsapp/
      ├─ parse → 1 evento de texto
      ├─ ¿wamid duplicado? no
      ├─ INSERT message (type 0, contact, received)
      ├─ conversation: assignment='bot', unread+1, preview, last_activity
      ├─ WS: messages.updated + conversations.changed  →  el dashboard lo pinta
      └─ POST → n8n (payload crudo)                    →  responde 200 a YCloud
                    │
                    ▼  (asíncrono, el agente piensa)
              n8n → POST /api/n8n/bot-reply/ {phone, text}
                       ├─ ¿chatbot ON? sí
                       ├─ INSERT message (type 1, bot, sent)
                       ├─ conversation: assignment='bot', preview, last_activity
                       ├─ POST YCloud /whatsapp/messages → wamid
                       ├─ UPDATE status + wa_message_id
                       └─ WS: messages.updated          →  el dashboard lo pinta
                                                        →  YCloud entrega al cliente
```

### B) Cliente escribe · chatbot ON · n8n caído

Idéntico a (A) hasta el reenvío, que falla. El webhook **responde 200** con `n8n_forward.ok: false`. El mensaje del cliente queda guardado y visible en el dashboard; **no se envía ninguna respuesta automática**. El asesor no puede responder hasta apagar el interruptor (R1).

### C) Asesor responde · chatbot OFF

```
Dashboard → POST /api/conversations/13/messages/ {content} + CSRF
  ├─ ¿chatbot ON? no → continúa
  ├─ after_id = MAX(id)
  ├─ INSERT message (type 1, agent, sent)
  ├─ conversation: assignment='me', preview, last_activity
  ├─ POST YCloud → wamid
  ├─ UPDATE status + wa_message_id
  ├─ WS: messages.updated
  └─ 201 {message, conversation, ycloud_ok}
```

### D) El asesor toma el control a mitad de conversación

```
Dashboard → POST /api/contacts/13/chatbot/ {"enabled": false}
  ├─ UPDATE contacts.chatbot_enabled = false
  ├─ WS: contact.updated  → se desbloquea el composer en todos los clientes
  └─ 200
```

**Condición de carrera real:** si n8n ya estaba procesando cuando se apaga el interruptor, su callback llegará después y será **rechazado** con `status: "skipped"`, `reason: "chatbot_disabled"`. Es el comportamiento deseado — el asesor tomó el control — pero implica que **la respuesta del bot se pierde sin aviso**. Si te importa, persístela como borrador en lugar de descartarla.

---

## 11. Configuración

Todo por variables de entorno. `settings.py` carga `.env` desde **dos** ubicaciones, en este orden: la raíz del repo y la carpeta del proyecto Django. Ninguna sobrescribe variables ya presentes en el entorno real del proceso, así que puedes anular cualquier valor exportándolo antes de arrancar:

```bash
YCLOUD_API_KEY="" N8N_WEBHOOK_URL="" python manage.py runserver   # modo seguro para pruebas
```

| Variable | Default | Obligatoria | Uso |
| --- | --- | --- | --- |
| `YCLOUD_API_KEY` | `""` | para enviar | Cabecera `X-API-Key`. Vacía ⇒ todo saliente queda `failed` |
| `YCLOUD_WHATSAPP_FROM` | `""` | para enviar | Número de negocio remitente |
| `YCLOUD_API_BASE` | `https://api.ycloud.com/v2` | no | Base de la API |
| `N8N_WEBHOOK_URL` | `""` | para el bot | Production URL del nodo Webhook. Vacía ⇒ sin reenvío |
| `N8N_WEBHOOK_TIMEOUT` | `5` | no | Segundos. Valor no numérico ⇒ `5.0` |
| `WHATSAPP_VERIFY_TOKEN` | `dashboard-verify-token` | no | Solo para el `GET` de verificación |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,testserver` | sí en producción | CSV. Debe incluir el host de ngrok y `host.docker.internal` si n8n corre en Docker |
| `DJANGO_DEBUG` | `true` | — | `1`/`true`/`yes` activan modo depuración |
| `DJANGO_SECRET_KEY` | clave insegura embebida | **sí en producción** | Hay un default de desarrollo en el código |

> 🔒 Antes de exponer esto a internet: `DJANGO_DEBUG=false`, `DJANGO_SECRET_KEY` propia, `DJANGO_ALLOWED_HOSTS` acotado, y autenticación en la API (§4.1).

`ALLOWED_HOSTS` también gobierna el WebSocket, vía `AllowedHostsOriginValidator` en `asgi.py`: un host ausente de la lista produce un WebSocket que se conecta y se cierra de inmediato, y el dashboard cae silenciosamente a polling. Es la causa número uno de «el chat no actualiza en vivo».

---

## 12. Checklist de reimplementación

Qué conservar, qué cambiar y qué puedes tirar a la basura.

### Imprescindible (es el producto)

- [ ] Tres tablas con las columnas y los tipos de [§2](#2-modelo-de-datos), incluido el `messages.contact_id` denormalizado
- [ ] `UNIQUE` en `contacts.phone_number` y en `messages.wa_message_id`
- [ ] Índice `(conversation_id, id DESC)` sobre `messages`
- [ ] Normalización de teléfono `+<dígitos>` aplicada **en todas** las rutas de entrada
- [ ] Las siete reglas de [§3](#3-reglas-de-negocio-transversales) — especialmente R1 (exclusión bot/asesor) y R2 (idempotencia)
- [ ] Paginación por cursor con `after_id`/`before_id`; nada de `OFFSET`
- [ ] Los cinco contratos JSON de [§4](#4-endpoints-http) si vas a reutilizar el frontend tal cual
- [ ] Envío a YCloud fuera de la transacción, con persistencia del resultado (R6)
- [ ] Webhook que responde 200 pase lo que pase

### Corregir al portar (defectos conocidos)

- [ ] **Autenticación en toda la API** — hoy no hay ninguna (§4.1)
- [ ] Secreto compartido o firma en `/api/n8n/bot-reply/` y en el webhook
- [ ] `display_id` con secuencia atómica, no `MAX+1` (§2.2)
- [ ] `after_id` del WebSocket con `MAX(id)` previo, no `id - 1` (§5)
- [ ] Separar `404` de `500` en el envío del asesor (§4.5)
- [ ] Encolar los envíos a WhatsApp en vez de bloquear el request (§6.1)
- [ ] Capa de tiempo real distribuida (Redis) si corres más de un proceso (§6.4)
- [ ] Soporte de plantillas de WhatsApp para la ventana de 24 h (§8.2)
- [ ] Persistir `message_id` del bot y correlacionar por algo más que el teléfono (§4.9)

### Descartable (ruido heredado)

- [ ] `reply` y `outbound` en las respuestas: siempre `null`
- [ ] `last_message_at`: alias duplicado de `last_activity_at`
- [ ] `phone` / `phone_number` duplicados en el contacto
- [ ] Campos formateados (`timestamp`, `last_message_day`, `create_time`): formatea en el cliente
- [ ] `dashboard_context()` completo si vas a una SPA
- [ ] El `GET` de verificación estilo Meta si tu BSP no lo usa

### Puntos de extensión previstos

- **Multimedia:** hoy solo `type: "text"`; todo lo demás se descarta en el parser (§6.1)
- **Multiusuario:** `owner` y `assignment` son texto libre, sin FK a una tabla de usuarios
- **Multi-bandeja:** `inbox` y `channel` existen como columnas pero no se filtra por ellas
- **Estados de entrega:** `delivered` y `read` están en el *enum* pero nada los escribe — falta suscribirse a los webhooks de estado de YCloud
