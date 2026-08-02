# Arquitectura de agenda

La solución separa `companies`, `users`, `advisors`, `clients`, `scheduling` e `integrations`. `CompanyOwnedModel` aporta el tenant, y `scheduling.services` contiene disponibilidad, conflictos, asignación, historial y transiciones.

La creación manual valida el tenant, relaciones, disponibilidad y conflicto antes de crear el evento y su historial dentro de una transacción. El flujo chatbot resuelve la empresa desde la identidad autenticada, aplica idempotencia, reutiliza/crea al cliente, bloquea candidatos, asigna y crea evento e historial atómicamente.

Los datetimes son aware (`USE_TZ=True`); los calendarios consultan un único rango. La asignación soporta first available, least events, round robin (estado bloqueado), prioridad (número menor es preferente) y aleatoria.
