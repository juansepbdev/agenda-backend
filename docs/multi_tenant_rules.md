# Reglas multiempresa

La empresa siempre procede de `request.user.company` para endpoints internos. Nunca se recibe `company_id` como campo escribible. Los selectores comienzan en `Event.objects.filter(company=user.company)` y los `get_object()` de ViewSets se construyen desde ese queryset, por lo que un UUID de otro tenant responde 404.

Los servicios validan que asesor, cliente, supervisor y actores compartan empresa. El superusuario es global; no equivale al rol `ADMIN` de tenant. Webhooks e integraciones deben resolver la empresa exclusivamente desde una credencial asociada al tenant; la implementación inicial usa la identidad autenticada y debe sustituirse por API key/firmas antes de exponerla públicamente.
