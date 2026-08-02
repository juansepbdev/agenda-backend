# API endpoints

Endpoints internos: `GET/PATCH /api/v1/companies/current/`, `/api/v1/users/`, `/api/v1/clients/`, `/api/v1/events/` y `/api/v1/scheduling-configurations/`. Eventos expone `confirm`, `start`, `complete`, `cancel`, `no-show`, `reschedule`, `reassign` e `history` como acciones por UUID. Calendario: `/api/v1/calendar/day/`, `week/`, `month/`.

La integración es `POST /api/v1/integrations/chatbot/events/`; requiere identidad autenticada de tenant e `idempotency_key`. Las respuestas de dominio usan `{ "error": { "code", "message", "details" } }`. La especificación OpenAPI está en `/api/schema/` y `/api/docs/`.
