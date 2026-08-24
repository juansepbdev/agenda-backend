# Inbox WhatsApp (CRM conversacional)

App `apps.inbox`. Implementa el CRM conversacional descrito en `REFERENCIA_TECNICA.md`, adaptado al modelo multiempresa del proyecto. Tres actores externos: WhatsApp vía **YCloud** (BSP), el agente de IA en **n8n** y el dashboard.

La base de datos es la única fuente de verdad. El backend **nunca genera respuestas automáticas por su cuenta**: si el chatbot está encendido reenvía el evento crudo a n8n y espera el callback; sin n8n configurado no hay mensaje saliente.

## Modelo de datos

`Contact 1—N Conversation 1—N Message`, todo colgando de `Company`. `Message.contact_id` está denormalizado a propósito: permite analítica por contacto sin `JOIN`.

- **`WhatsAppChannel`** — configuración por empresa (API key y número de YCloud, URL de n8n, `verify_token`) y credencial del webhook. Reemplaza las variables de entorno globales de la referencia.
- **`ConversationSequence`** — contador atómico de `display_id` por empresa, bloqueado con `SELECT ... FOR UPDATE`. Sustituye al `MAX(display_id) + 1` de la referencia, que compite consigo mismo bajo webhooks concurrentes.
- **`Contact`** — clave natural `(company, phone_number)` con el teléfono normalizado a `+<dígitos>`. `chatbot_enabled` es el interruptor de negocio central. `client` es un FK opcional a `clients.Client`: se enlaza automáticamente cuando el teléfono normalizado coincide, de modo que una conversación de WhatsApp puede derivar en un evento de agenda.
- **`Conversation`** — `display_id` único por empresa, `status` (`open`/`resolved`/`pending`), `assignment` (`unassigned`/`me`/`bot`), `unread_count`, y `last_activity_at` como clave de ordenamiento.
- **`Message`** — entrantes y salientes en una sola tabla, diferenciados por `message_type` (`0` entrante, `1` saliente). Es el **único modelo con PK entera**: el contrato de paginación por cursor exige identificadores monótonos y el índice caliente es `(conversation, id DESC)`. `wa_message_id` es único por empresa y sostiene la idempotencia.

## Reglas de negocio

| | Regla |
| --- | --- |
| **R1** | `chatbot_enabled` es excluyente. Encendido: el entrante se reenvía a n8n y el asesor recibe `403 CHATBOT_ENABLED`. Apagado: el asesor responde y el callback de n8n se omite con `status: "skipped"`. |
| **R2** | Idempotencia por `wa_message_id`: un reintento del BSP no inserta, no difunde y no reenvía a n8n; responde con el mensaje existente y `duplicate: true`. Los mensajes sin `wa_message_id` (ingesta manual) no se deduplican. |
| **R3** | El `assignment` se mueve solo: entrante con chatbot ON → `bot`; entrante con chatbot OFF → sin cambio; envío del asesor → `me`; respuesta del bot → `bot`. |
| **R4** | `unread_count` sube uno por entrante y solo se limpia al abrir el chat (`GET .../conversations/<id>/`), con un `UPDATE` dirigido que no toca `last_activity_at`: abrir un chat no lo reordena. |
| **R5** | Los eventos en vivo llevan punteros, nunca el contenido del mensaje; el cliente re-consulta por REST. |
| **R6** | El envío a YCloud ocurre **fuera** de la transacción y su resultado se persiste: éxito → `sent` + `wamid`; fallo → `failed`. La petición del cliente responde 2xx igual, con `ycloud_ok: false`. |
| **R7** | Un único `POST` a n8n por payload de webhook, con el JSON crudo sin transformar, y solo si n8n está configurado **y** el contacto tiene el chatbot encendido **y** el mensaje no es duplicado. |

## Endpoints

Base `/api/v1/inbox/`. Los internos exigen sesión y resuelven la empresa desde `request.user.company`.

| Método | Ruta | Notas |
| --- | --- | --- |
| `GET` | `conversations/` | `?filter=all\|me\|unassigned\|bot`. Orden por actividad descendente |
| `GET` | `conversations/<uuid>/` | Conversación + últimos 100 mensajes + contacto. Limpia `unread_count` |
| `GET` | `conversations/<uuid>/messages/` | Cursor: `limit` (1..200, def. 50), `after_id`, `before_id`. Siempre devuelve orden ascendente |
| `POST` | `conversations/<uuid>/messages/` | Envío del asesor. `{"content": "..."}` → `201` |
| `POST` | `contacts/<uuid>/chatbot/` | `{"enabled": bool}` |
| `POST` | `messages/` | **Guardar mensajes** en el historial. Sesión o `X-API-Key`. No envía por WhatsApp |
| `POST` | `messages/incoming/` | Ingesta manual de pruebas. **No reenvía a n8n** |
| `GET`/`POST` | `webhook/whatsapp/` | Webhook de YCloud. Sin sesión, credencial de canal |
| `POST` | `n8n/bot-reply/` | Callback del bot. Sin sesión, credencial de canal |

Los errores usan el envoltorio del resto de la API: `{"error": {"code", "message", "details"}}`. Códigos propios: `CHATBOT_ENABLED` (403), `CONVERSATION_NOT_FOUND` / `CONTACT_NOT_FOUND` (404), `INVALID_WEBHOOK_CREDENTIAL` (401), `INBOX_VALIDATION_ERROR` (400).

Con la credencial válida, el webhook **siempre responde 200**, incluso si el payload no era procesable (`status: "ignored"`): un error provoca reintentos innecesarios del BSP.

## Guardar mensajes: `POST /api/v1/inbox/messages/`

Tercer camino de entrada al historial y el único que **solo escribe**: no llama
a YCloud ni reenvía a n8n. Registra lo que ya ocurrió en otro sitio — una
respuesta que el agente de n8n despachó por su cuenta, una migración desde otro
CRM, un histórico que rellenar.

Autentica en este orden: si llega `X-API-Key` manda la credencial del canal,
aunque la petición traiga además una cookie de sesión; si no, se usa la sesión
del panel. Así el mismo endpoint sirve al dashboard y a n8n sin que una
identidad pueda suplantar a la otra.

Decisiones de diseño que lo distinguen del resto:

* **Idempotente por `wa_message_id`** dentro de la empresa: repetir devuelve
  `200` con `duplicate: true`.
* **Una `direction` incoherente con `sender_type` se rechaza** en vez de
  corregirse. Un histórico con la dirección mal puesta arruina la analítica
  posterior, y es preferible que falle al escribir.
* **No aplica R1.** El interruptor del chatbot regula quién *puede enviar*, no
  quién puede *registrar lo ya enviado*.
* **Lotes de hasta 200** con `{"messages": [...]}`: un elemento inválido no
  tumba a los demás, y se responde `207` con los fallos localizados por índice.

El contrato completo (campos, alias, ejemplos y códigos de error) está en
[chatbot_messages.md](chatbot_messages.md).

## Credencial de los webhooks

`/webhook/whatsapp/` y `/n8n/bot-reply/` no usan sesión (y `/messages/` la acepta como alternativa). La empresa se resuelve **exclusivamente** desde la credencial del canal, nunca desde el cuerpo de la petición: se envía en la cabecera `X-API-Key` o, para paneles de BSP que solo permiten personalizar la URL, en el parámetro `?api_key=` (la cabecera es preferible, un secreto en la URL acaba en los logs del proxy).

La credencial se guarda **hasheada** (SHA-256); el valor en claro se muestra una sola vez, al generarla:

```bash
python manage.py inbox_channel --company <slug> --rotate-key \
    --ycloud-api-key <key> --ycloud-from +573001112233 \
    --n8n-url https://n8n.example/webhook/xxx
```

También se puede rotar desde el admin de Django, con la acción «Generar credencial de webhook nueva».

## Integraciones

**Entrada.** El parser acepta el formato de YCloud (`whatsapp.inbound_message.received`) y, como respaldo, el de la Cloud API de Meta. Los ecos de mensajes salientes (`whatsapp.smb.message.echoes`) se ignoran: procesarlos duplicaría el hilo. **Solo se aceptan mensajes `type: "text"`**; multimedia es el punto de extensión más obvio. El mensaje se fecha con la hora real de WhatsApp (`sendTime`), no con la de recepción del webhook. El nombre del perfil de WhatsApp solo se escribe si el contacto no tiene nombre propio: uno editado a mano en el CRM no se pisa.

**Salida.** `POST {YCLOUD_API_BASE}/whatsapp/messages` con `X-API-Key`, timeout de 30 s. Solo texto plano: fuera de la ventana de servicio de 24 h de WhatsApp los mensajes libres son rechazados y quedan `failed`; hace falta soporte de plantillas para reactivar conversaciones.

**n8n.** Recibe el JSON crudo de YCloud, sin transformar: hace su propio parsing y el backend no le impone un esquema. El flujo de n8n **no debe enviar el mensaje a YCloud por su cuenta** — el envío es responsabilidad exclusiva del backend; si ambos envían, el cliente recibe el mensaje duplicado. El bot no tiene memoria del lado del backend: no se envía historial, el agente mantiene el contexto.

## Tiempo real

El transporte queda desacoplado en `services/realtime.py` porque el despliegue actual es WSGI (Vercel), donde no hay WebSocket: el dashboard funciona con el *polling* de respaldo. `INBOX_REALTIME_BACKEND` apunta a un callable `(company_id, event, payload) -> None` que permite enchufar Channels, Redis pub/sub o SSE sin tocar la capa de servicios. Sin backend, la difusión es un no-op registrado en log.

Eventos: `messages.updated` (`{conversation_id, after_id, display_id}`), `conversations.changed` (`{}`) y `contact.updated` (`{contact}`, única excepción a R5). El `after_id` es el `MAX(id)` **previo** a insertar, no `id - 1`.

## Configuración

Las credenciales son por empresa y viven en `WhatsAppChannel`. En settings solo hay infraestructura: `INBOX_YCLOUD_API_BASE` (`YCLOUD_API_BASE`), `INBOX_YCLOUD_TIMEOUT` (`YCLOUD_TIMEOUT`) e `INBOX_REALTIME_BACKEND`.

## Diferencias deliberadas con `REFERENCIA_TECNICA.md`

Corregido de su sección «corregir al portar»: autenticación en toda la API, credencial por tenant en los webhooks, `display_id` con secuencia atómica, `after_id` con `MAX(id)` previo, y `404` separado de `500` (la referencia disfrazaba cualquier fallo interno de «no encontrado»).

Descartado de su sección «ruido heredado»: los campos `reply` y `outbound` siempre nulos, el alias `last_message_at`, el duplicado `phone`/`phone_number`, los campos preformateados (`timestamp`, `last_message_day`, `create_time`) —las fechas viajan en ISO-8601 y el cliente decide cómo mostrarlas— y `dashboard_context()`, que solo servía al render de HTML.

Sigue pendiente, igual que en la referencia: encolar los envíos a WhatsApp en vez de bloquear el request, soporte de plantillas para la ventana de 24 h, estados de entrega (`delivered`/`read` están en el enum pero nada los escribe: falta suscribirse a los webhooks de estado de YCloud) y correlacionar el callback del bot por algo más que el teléfono.
