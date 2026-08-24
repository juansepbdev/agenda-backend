# Dashboard e inbox para frontend

Base URL: `https://api.tu-dominio.com/api/v1`. Todo lo de este documento requiere sesión Django o Basic Auth y resuelve la empresa desde la persona autenticada: **no envíes `company_id`**. Para la agenda y el CRM, ver [frontend_endpoints.md](frontend_endpoints.md).

Dos bloques:

* **`/dashboard/`** — métricas de solo lectura para las gráficas y tarjetas KPI.
* **`/inbox/`** — el chat en vivo: conversaciones, mensajes y el interruptor del bot.

---

# Parte 1 · Dashboard de métricas

Siete endpoints, todos `GET`, todos con los mismos parámetros de rango.

| Endpoint | Qué pinta |
|---|---|
| `GET /dashboard/overview/` | Tarjetas KPI de la portada, con variación vs. periodo anterior |
| `GET /dashboard/messages/` | Volumen de mensajes: totales, serie temporal, reparto por estado |
| `GET /dashboard/messages/heatmap/` | Matriz 7×24 de actividad por día y hora |
| `GET /dashboard/conversations/` | Estado del inbox, tiempos de respuesta, automatización, top contactos |
| `GET /dashboard/events/` | Eventos: totales, tasas, serie temporal, desgloses |
| `GET /dashboard/advisors/` | Ranking de rendimiento por asesor |
| `GET /dashboard/funnel/` | Embudo de WhatsApp a visita completada |

## Datos de demostración

Para desarrollar contra un panel que no salga en blanco:

```bash
python manage.py seed_dashboard_demo --company <slug> --dry-run   # ver qué haría
python manage.py seed_dashboard_demo --company <slug>             # aplicar
python manage.py seed_dashboard_demo --company <slug> --undo      # revertir
```

Siembra 5 conversaciones (18 mensajes repartidos en los últimos 6 días), 2 clientes y 5 eventos, elegidos para que **ninguna métrica salga en cero**: hay un hilo con traspaso bot→asesor, uno solo de bot, uno solo de asesor, uno sin contestar, y eventos en los cinco estados que alimentan las tasas.

Es idempotente (repetirlo no duplica), todo lo que crea queda marcado con `demo-seed` y `--undo` borra exactamente eso. Los teléfonos usan el prefijo reservado `+5730055500XX`.

**Los dos eventos futuros** (`CONFIRMED` y `PENDING`) no aparecen con `period=7d`, porque es una ventana hacia atrás. Para verlos, usa `date_field=created_at` o un rango explícito que llegue al futuro: `?from=2026-08-17&to=2026-08-30`.

## Alcance por rol

| Bloque | ADMIN / SUPERVISOR | ADVISOR |
|---|---|---|
| Agenda (`events/`, `advisors/`, bloque `agenda` de `overview/`) | Empresa o equipo supervisado | Solo sus propios eventos |
| Inbox (`messages/`, `messages/heatmap/`, `conversations/`, `funnel/`, bloque `inbox` de `overview/`) | Completo | `403`; en `overview/` el bloque llega como `null` |

El recorte de agenda es el mismo que aplica `/api/v1/events/`, así que los números cuadran con lo que el usuario ve en su calendario. El inbox no tiene propietario por conversación, de modo que sus métricas son necesariamente de empresa y se reservan a quien tiene alcance de empresa.

**Para el menú:** decide qué pestañas mostrar con `GET /users/me/permissions/` en vez de deducirlo del rol a mano.

## Parámetros comunes

| Parámetro | Valores | Por defecto |
|---|---|---|
| `period` | `today`, `yesterday`, `7d`, `14d`, `30d`, `90d`, `180d`, `365d`, `mtd`, `ytd` | `30d` |
| `from` / `to` | `YYYY-MM-DD` o ISO-8601 completo. Tienen prioridad sobre `period` | — |
| `granularity` | `hour`, `day`, `week`, `month` | Se deduce del rango |
| `tz` | Zona IANA, p. ej. `America/Bogota` | La de la empresa |
| `date_field` | `start_at`, `created_at` — solo en `events/` y `advisors/` | `start_at` |

Cuatro detalles que evitan sorpresas al graficar:

* **El rango es semiabierto** `[start, end)`. `to=2026-08-31` incluye el día 31 completo porque el corte se pone en el 1 de septiembre a las 00:00.
* **Las cubetas se agrupan en la zona de la empresa**, no en UTC. Un "día" es el día que ve el usuario.
* **La granularidad automática** es `hour` hasta 2 días, `day` hasta 62, `week` hasta 366 y `month` a partir de ahí. El tope es 1000 puntos por serie; pasarse devuelve `400`.
* **`date_field=start_at`** responde *qué había agendado* en el rango; **`created_at`**, *cuánto se reservó*. Son dos preguntas distintas: si tu gráfica se llama "reservas por día", quieres `created_at`.

Toda respuesta incluye `period` con lo que el backend acabó resolviendo. Úsalo para rotular el eje y para mostrar el rango efectivo cuando el usuario no lo especificó:

```json
{"period": {
  "start": "2026-07-25T00:00:00-05:00",
  "end": "2026-08-24T00:00:00-05:00",
  "granularity": "day",
  "timezone": "America/Bogota",
  "label": "30d"
}}
```

## `GET /dashboard/overview/`

Las tarjetas de la portada, con comparación contra el periodo inmediatamente anterior de la misma duración.

```bash
curl -u "admin@inmobiliaria.co:password" \
  "https://api.tu-dominio.com/api/v1/dashboard/overview/?period=30d"
```

```json
{
  "period": {"...": "..."},
  "comparison_period": {"...": "..."},
  "agenda": {
    "date_field": "start_at",
    "total": 128,
    "by_status": {"pending": 12, "confirmed": 20, "in_progress": 0, "completed": 74, "cancelled": 15, "no_show": 7, "rescheduled": 0},
    "closed": 96,
    "completion_rate_pct": 77.08,
    "cancellation_rate_pct": 15.63,
    "no_show_rate_pct": 7.29,
    "from_chatbot": 54,
    "chatbot_share_pct": 42.19,
    "auto_assigned": 54,
    "avg_duration_minutes": 60.0,
    "trend": {
      "total": {"current": 128, "previous": 96, "change": 32, "change_pct": 33.33},
      "completed": {"...": "..."},
      "cancelled": {"...": "..."},
      "no_show": {"...": "..."},
      "from_chatbot": {"...": "..."},
      "completion_rate_pct": {"...": "..."}
    }
  },
  "inbox": {
    "messages": {"total": 2140, "inbound": 980, "outbound": 1160, "from_contact": 980, "from_bot": 890, "from_agent": 270, "failed": 4, "bot_share_pct": 76.72, "failure_rate_pct": 0.34},
    "conversations": {"...": "..."},
    "contacts": {"...": "..."},
    "automation": {"...": "..."},
    "response_times": {"...": "..."},
    "trend": {
      "messages_total": {"...": "..."},
      "messages_inbound": {"...": "..."},
      "messages_outbound": {"...": "..."},
      "conversations_new": {"...": "..."},
      "contacts_new": {"...": "..."},
      "bot_share_pct": {"...": "..."}
    }
  }
}
```

**Al pintar la flecha de variación:** `change_pct` llega como `null` cuando el periodo anterior fue cero. No es un `0` ni un `100` — es "no hay base de comparación". Muestra un guion, no una flecha verde.

**`inbox` llega como `null`** para un usuario ADVISOR. Comprueba antes de leer dentro.

## `GET /dashboard/messages/`

```json
{
  "period": {"...": "..."},
  "totals": {"total": 2140, "inbound": 980, "outbound": 1160, "from_bot": 890, "from_agent": 270, "failed": 4, "bot_share_pct": 76.72, "failure_rate_pct": 0.34},
  "series": [
    {"bucket": "2026-08-22T00:00:00-05:00", "total": 84, "inbound": 38, "outbound": 46, "bot": 40, "agent": 6, "contact": 38},
    {"bucket": "2026-08-23T00:00:00-05:00", "total": 0, "inbound": 0, "outbound": 0, "bot": 0, "agent": 0, "contact": 0}
  ],
  "by_status": [{"status": "sent", "total": 1156, "share_pct": 54.02}]
}
```

**`series` ya viene con las cubetas vacías rellenas a cero** y ordenada. Puedes pasarla directo a la librería de gráficas sin reconstruir el eje temporal ni buscar huecos.

## `GET /dashboard/messages/heatmap/`

Matriz de 7 filas (día de la semana, **lunes = 0**) × 24 columnas (hora local), más el pico. Es la vista con la que se decide el horario de los asesores.

```json
{
  "heatmap": {
    "matrix": [[0, 0, 1, "…24 valores…"], "…7 filas…"],
    "peak": {"weekday": 2, "hour": 19, "count": 63},
    "weekday_labels": ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
  }
}
```

## `GET /dashboard/conversations/`

Acepta además `limit` (1..100, por defecto 10) para el tamaño del ranking de contactos.

```json
{
  "conversations": {
    "new": 74,
    "active": 112,
    "current": {
      "total": 310,
      "by_status": {"open": 88, "pending": 4, "resolved": 218},
      "by_assignment": {"bot": 61, "agent": 24, "unassigned": 225},
      "resolution_rate_pct": 70.32,
      "unread_conversations": 9
    }
  },
  "contacts": {"total": 310, "new": 74, "active": 112, "chatbot_enabled": 280, "chatbot_disabled": 30, "linked_to_client": 96, "linked_rate_pct": 30.97},
  "response_times": {
    "unit": "seconds",
    "overall": {"samples": 640, "avg": 412.5, "min": 3.0, "p50": 22.0, "p90": 1840.0, "max": 9100.0},
    "first_response": {"...": "..."},
    "by_sender": {"bot": {"...": "..."}, "agent": {"...": "..."}},
    "unanswered_conversations": 6,
    "truncated": false
  },
  "automation": {"conversations_with_reply": 92, "bot_only": 61, "agent_only": 7, "handoff_conversations": 24, "full_automation_rate_pct": 66.3, "handoff_rate_pct": 26.09},
  "top_contacts": [{"contact_id": "uuid", "name": "Laura", "phone_number": "+573001234567", "chatbot_enabled": true, "messages": 42, "inbound": 20, "outbound": 22}]
}
```

Cómo leer estas cifras, para que las etiquetes bien en la interfaz:

* **`new` y `active` son del periodo; todo lo que cuelga de `current` es la foto de hoy.** Una conversación no guarda el historial de sus cambios de estado, así que "resueltas" no puede ser "resueltas en agosto". Rotúlalo como estado actual.
* **Un tiempo de respuesta** es el hueco entre un entrante sin contestar y la primera salida que lo contesta. Varios entrantes seguidos del mismo contacto cuentan una sola vez; una salida sin entrante previo es un mensaje proactivo y no entra en la muestra.
* **`by_sender` separa bot y asesor a propósito.** El bot responde en segundos y la persona en minutos u horas: son dos números distintos y hay que mostrarlos por separado. Muestra `p50` como cifra principal, no `avg` — un caso extremo desplaza la media y no representa la experiencia típica.
* **`samples: 0` y `avg: null`** significan "no hubo muestras", no "cero segundos". Pinta un guion.
* **`unanswered_conversations`** son los entrantes que se quedaron sin respuesta al cerrar el periodo. Es probablemente la cifra más accionable del panel.
* **`truncated: true`** avisa de que se alcanzó el techo de 50 000 mensajes recorridos y la muestra es parcial. Sugiere al usuario acotar el rango.

## `GET /dashboard/events/`

```json
{
  "totals": {"...": "igual que el bloque agenda de overview"},
  "series": [{"bucket": "2026-08-22T00:00:00-05:00", "total": 6, "completed": 4, "cancelled": 1, "no_show": 0, "from_chatbot": 3}],
  "breakdowns": {
    "by_type": [{"event_type": "PROPERTY_VISIT", "total": 96, "share_pct": 75.0}],
    "by_source": [{"source": "CHATBOT", "total": 54, "share_pct": 42.19}],
    "by_no_show_type": [{"no_show_type": "CLIENT_NO_SHOW", "total": 6, "share_pct": 85.71}]
  }
}
```

**Las tasas se calculan sobre los eventos cerrados** (completados + cancelados + no-show), no sobre el total, y ese denominador viene en `closed`. Un evento futuro todavía pendiente no es un fracaso; meterlo en el denominador lo aparentaría y la tasa de completado caería sola con solo agendar más.

## `GET /dashboard/advisors/`

Ordenado por completados, **no** por totales: cargar la agenda no es el resultado, cerrarla sí.

```json
{
  "advisors": [{
    "advisor_id": "uuid", "code": "A-001", "name": "Carlos Pérez", "email": "carlos@demo.co",
    "total": 42, "pending": 4, "confirmed": 6, "completed": 28, "cancelled": 3, "no_show": 1,
    "from_chatbot": 19, "completion_rate_pct": 87.5, "no_show_rate_pct": 3.13
  }]
}
```

Un ADVISOR recibe una lista con su propia fila, lo que permite reutilizar el mismo componente para la vista individual.

## `GET /dashboard/funnel/`

```json
{
  "funnel": {
    "steps": [
      {"key": "contacts", "label": "Contactos nuevos", "value": 74, "conversion_from_previous_pct": null},
      {"key": "conversations", "label": "Conversaciones abiertas", "value": 74, "conversion_from_previous_pct": 100.0},
      {"key": "clients", "label": "Clientes creados por el bot", "value": 31, "conversion_from_previous_pct": 41.89},
      {"key": "events_scheduled", "label": "Visitas agendadas por el bot", "value": 22, "conversion_from_previous_pct": 70.97},
      {"key": "events_completed", "label": "Visitas completadas", "value": 14, "conversion_from_previous_pct": 63.64}
    ],
    "overall_conversion_pct": 18.92
  }
}
```

Cada escalón se cuenta por su propia fecha de creación dentro del periodo, así que **no son subconjuntos exactos** unos de otros: un contacto de ayer puede agendar hoy, y por eso un escalón puede superar al anterior. Es una foto de caudal, no una cohorte. Usa `conversion_from_previous_pct` (que ya viene calculado) y evita textos del tipo "de estos 74 contactos, 14 completaron".

`label` viene listo para pintar; `key` es el identificador estable si quieres tus propias etiquetas.

---

# Parte 2 · Inbox en vivo

Base `/api/v1/inbox/`. Requieren sesión de usuario.

| Método y ruta | Uso |
|---|---|
| `GET /inbox/conversations/` | Lista ordenada por actividad descendente. `?filter=all\|me\|unassigned\|bot` |
| `GET /inbox/conversations/{uuid}/` | Carga inicial del chat: conversación + últimos 100 mensajes + contacto |
| `GET /inbox/conversations/{uuid}/messages/` | Paginación por cursor: `limit` (1..200, def. 50), `after_id`, `before_id` |
| `POST /inbox/conversations/{uuid}/messages/` | Envío del asesor: `{"content": "..."}` |
| `POST /inbox/contacts/{uuid}/chatbot/` | Interruptor del bot: `{"enabled": bool}` |
| `POST /inbox/messages/` | Guardar un mensaje sin enviarlo por WhatsApp — ver [chatbot_messages.md](chatbot_messages.md) |

## Actualización en vivo por polling

El despliegue actual es WSGI y no hay WebSocket, así que **el chat se refresca con polling** cada ~5 s. El patrón es:

1. Abre el chat con `GET /inbox/conversations/{uuid}/` y guarda el `id` del último mensaje.
2. Cada 5 s llama a `GET /inbox/conversations/{uuid}/messages/?after_id={último_id}`.
3. Añade lo que venga y actualiza el cursor. Si viene vacío, no hagas nada.

`message.id` es un entero creciente **a propósito**: es lo que sostiene el cursor. Los demás identificadores son UUID.

Para scroll hacia arriba, usa `before_id` con el `id` del mensaje más antiguo que tengas cargado. El endpoint siempre devuelve orden cronológico ascendente, vayas hacia adelante o hacia atrás.

## Reglas de la interfaz

* **Abrir un chat pone `unread_count` a 0** como efecto de `GET /inbox/conversations/{uuid}/`. No hace falta llamar a nada más, y la conversación **no** se reordena en la lista por haberla abierto.
* **Con el chatbot encendido, el asesor no puede responder.** `POST /conversations/{uuid}/messages/` devuelve `403 CHATBOT_ENABLED`. Deshabilita el campo de texto cuando `contact.chatbot_enabled` sea `true` y ofrece el interruptor en su lugar.
* **`ycloud_ok: false` en la respuesta de envío** significa que el mensaje se guardó pero WhatsApp no lo aceptó. El mensaje queda con `status: "failed"`: márcalo en la burbuja en vez de tratarlo como enviado.
* **`assignment`** (`unassigned` / `me` / `bot`) es lo que alimenta los filtros de la lista, y lo mueve el backend solo: un entrante con el bot encendido la pasa a `bot`, una respuesta del asesor a `me`.

## Errores

Envoltorio común en toda la API:

```json
{"error": {"code": "CHATBOT_ENABLED", "message": "El chatbot está encendido; apágalo para responder manualmente.", "details": {}}}
```

| Código | HTTP | Cuándo |
|---|---|---|
| `ANALYTICS_VALIDATION_ERROR` | 400 | `period`, `granularity`, `tz`, `date_field` o rango inválidos |
| `PERMISSION_SCOPE` | 403 | El usuario no pertenece a ninguna empresa |
| `CHATBOT_ENABLED` | 403 | El asesor intenta responder con el bot encendido |
| `CONVERSATION_NOT_FOUND` / `CONTACT_NOT_FOUND` | 404 | No existe en esa empresa |
| `INBOX_VALIDATION_ERROR` | 400 | Cuerpo inválido al escribir en el inbox |

Un `403` sin cuerpo de dominio en `/dashboard/messages/`, `/conversations/` o `/funnel/` significa que el usuario es ADVISOR y no tiene alcance para métricas de inbox.

`details` lleva contexto accionable cuando lo hay:

```json
{"error": {"code": "ANALYTICS_VALIDATION_ERROR", "message": "El rango es demasiado largo para esa granularidad.",
           "details": {"granularity": "hour", "estimated_buckets": 52584, "max_buckets": 1000}}}
```

El esquema OpenAPI completo está en `/api/docs/` y `/api/schema/`.
