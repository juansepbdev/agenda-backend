# Seguimiento de leads

Devuelve a la superficie a los clientes con los que no se concretó nada: la cita se canceló, no asistieron, la visita ocurrió y no cerró, o escribieron por WhatsApp y nunca llegaron a agendar.

## La idea

**Las filas registran decisiones, no candidatos.** La cola se **deriva al consultarla** de lo que ya existe; `clients.FollowUp` solo se escribe cuando alguien actúa: se envía, se pospone, se descarta o se gestiona. No hay tabla que precalcular ni proceso que mantener sincronizado.

La clave de un lead es el **teléfono normalizado**, no el cliente: un lead puede ser todavía un contacto de WhatsApp sin ficha de cliente, y el teléfono es lo único que ambos comparten. `FollowUp.client` y `FollowUp.contact` son informativos.

## Quién entra en la cola

| Origen | Condición | `reason` | Dueño |
|---|---|---|---|
| Cita sin cierre | La **última** cita del cliente quedó `CANCELLED`, `NO_SHOW` o `COMPLETED`, no hay ninguna futura, y pasó el plazo | el estado de esa cita | el asesor de la cita |
| Cliente inactivo | Cliente activo sin ninguna cita, creado hace más de `follow_up_inactive_days` | `INACTIVE` | nadie |
| Contacto del chatbot | Escribió por WhatsApp, nunca agendó, y su último mensaje es más viejo que el plazo | `INACTIVE` | nadie |

Un cliente con una cita **futura** nunca entra: el lead está vivo y no hay nada que recuperar. Varias citas cerradas del mismo cliente producen **un solo** lead.

Los leads sin dueño los ven administración y supervisión, que son quienes pueden repartirlos.

## Qué saca a un lead de la cola

Dos cosas, y las dos caducan:

* **Una decisión vigente.** Descartado sale para siempre; pospuesto, hasta su `due_at`; enviado o gestionado, durante `follow_up_cooldown_days`. Pasado el enfriamiento vuelve — eso es lo que hace que el mecanismo sea *periódico* y no un único intento.
* **Salvo que haya actividad nueva.** Si el lead se ha vuelto a mover *después* de la decisión, resurge solo. Un descarte no es una condena: si el cliente vuelve a agendar y cancelar, reaparece.

## Configuración por empresa

En `scheduling.SchedulingConfiguration`:

| Campo | Default |
|---|---|
| `follow_up_enabled` | `True` |
| `follow_up_after_cancelled_days` | 7 |
| `follow_up_after_no_show_days` | 3 |
| `follow_up_after_completed_days` | 30 |
| `follow_up_inactive_days` | 60 |
| `follow_up_cooldown_days` | 30 |
| `follow_up_template` | vacío |
| `follow_up_template_language` | `es` |

Los plazos son `timedelta` sobre instantes, así que no dependen de la zona horaria de la empresa.

## WhatsApp: por qué hace falta una plantilla

WhatsApp solo acepta **texto libre dentro de las 24 h** siguientes al último mensaje del cliente. Un seguimiento a semanas vista cae fuera de esa ventana, así que **solo puede ser una plantilla aprobada** (HSM); el texto plano lo rechazaría Meta.

Sin `follow_up_template` configurada la empresa tiene cola pero **no** envío automático: cada lead queda marcado `skipped:sin-plantilla` y sigue en la lista del asesor. Es una degradación declarada, nunca un fallo silencioso.

El mensaje se registra en el hilo del inbox como **anotación de sistema** (`content_type="system"`). Eso no es un detalle: `store_message` reasigna la conversación según el remitente salvo en los mensajes de sistema, así que cualquier otra combinación le robaría al asesor cientos de conversaciones cada mañana.

El despacho automático **no** pasa por `send_agent_message` y por tanto no choca con la regla R1: el interruptor del chatbot regula quién puede *enviar como asesor*, no el envío automático del sistema.

## Endpoints

Base `/api/v1/`. El teléfono viaja en el **cuerpo** y no en la ruta: el proxy del frontend valida cada segmento contra `^[A-Za-z0-9_-]+$` y el `+` lo haría fallar antes de llegar.

| Método | Ruta | Notas |
|---|---|---|
| `GET` | `follow-ups/` | Cola recortada por rol. `?advisor=<uuid>`, `?reason=` |
| `POST` | `follow-ups/decide/` | `{"phone", "status", "due_at"?, "notes"?}`. `SNOOZED` exige `due_at` |
| `POST` | `follow-ups/send/` | `{"phone"}`. Envía ya |
| `GET` | `cron/follow-ups/` | **Solo el cron.** `Authorization: Bearer $CRON_SECRET` |

Errores propios: `FOLLOW_UP_NOT_FOUND` (404), `FOLLOW_UP_VALIDATION_ERROR` (400), `CRON_UNAUTHORIZED` (401).

## El cron

```json
"crons": [{ "path": "/api/v1/cron/follow-ups/", "schedule": "0 13 * * *" }]
```

Tres cosas que hay que saber, todas comprobadas:

1. **Vercel llama siempre con `GET`.** De ahí que el endpoint no sea `POST`.
2. **No sigue redirecciones.** Sin la barra final, `APPEND_SLASH` devuelve un 301 y el cron lo da por terminado: nunca se enviaría nada, y sin ningún error visible. La barra es obligatoria.
3. **El cron corre en UTC.** `0 13 * * *` son las 08:00 en Bogotá.

Vercel manda la cabecera `Authorization: Bearer <CRON_SECRET>` si la variable existe en el proyecto. Con `CRON_SECRET` vacío el endpoint **deniega**: nunca "sin secreto, pasa".

Espejo local, sobre el mismo servicio que corre en producción:

```bash
python manage.py dispatch_follow_ups --company demo --dry-run
python manage.py dispatch_follow_ups
```

### Idempotencia

Vercel admite explícitamente invocar un cron dos veces, así que no basta con confiar. Antes de enviar se **reclama** la fila con un `UPDATE` condicional sobre `UniqueConstraint(company, normalized_phone)`; si no toca ninguna fila, otro ya se llevó ese lead. El precio es que un fallo entre reclamar y enviar pierde ese mensaje hasta el siguiente enfriamiento: un WhatsApp perdido se nota menos que uno duplicado.

`maxDuration` en Vercel es de 60 s, así que cada ejecución envía como mucho `MAX_SENDS_PER_RUN` (50) por empresa. Lo que no sale hoy sale mañana, porque la cola se deriva y no se pierde.

## Deuda consciente

- Una fila por lead: no queda histórico de descartes anteriores. El rastro de los envíos sí, en los mensajes del hilo.
- La unión de los orígenes se resuelve en memoria, con un tope de 365 días de historial. Habría que paginarlo en SQL si una empresa llega a decenas de miles de clientes.
- Una sola plantilla por empresa, sin variante por motivo.
- Hora del cron fija en UTC: correcta para Colombia, insuficiente para varias zonas.
- Los leads sin cita no tienen asesor mientras `Conversation.advisor` no exista en `staging`.
