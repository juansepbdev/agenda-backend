# Flujos de eventos

`PENDING → CONFIRMED → IN_PROGRESS → COMPLETED`. También se permite completar desde `CONFIRMED`. Cancelar está prohibido desde `COMPLETED` y `RESCHEDULED`. La inasistencia registra tipo y notas.

La reprogramación no modifica el evento: cambia el original a `RESCHEDULED`, crea uno nuevo y los enlaza en una transacción. La reasignación valida empresa, disponibilidad y conflicto. Cada creación, transición, reasignación y reprogramación crea historial sin secretos.
