# AGENTS.md — backend

API multiempresa de agenda inmobiliaria. Django 6 + DRF, desplegada en Vercel como función serverless.

Este archivo es para agentes de IA y para quien llegue nuevo. No repite el `README.md`: recoge lo que **no se deduce leyendo el código** y lo que cuesta un ciclo de CI o un despliegue roto averiguar.

---

## Arranque

Python **3.12** (`.python-version`).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements/local.txt
cp .env.local.example .env.local   # ajusta SECRET_KEY
python manage.py migrate
python manage.py runserver
```

Docs interactiva en `/api/docs/`.

**Ojo con los requirements.** El CI y Vercel instalan **`requirements.txt`** (pineado, en la raíz), no `requirements/local.txt`. Los dos existen y pueden divergir. Si añades una dependencia, va en `requirements.txt` o el despliegue no la tendrá.

Única variable obligatoria en cualquier entorno: `SECRET_KEY`. En producción además `DB_HOST`, `DB_USER` y `DB_PASSWORD`. Genera el secreto con:

```bash
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

### Settings

`DJANGO_ENV` decide el módulo: `local` (por defecto) o `production`. Lo resuelven `manage.py`, `config/wsgi.py` y `config/asgi.py`, así que **nunca hace falta `--settings=`**. El archivo `.env.<DJANGO_ENV>` se carga solo, y no sobreescribe variables ya presentes en el entorno — en Vercel manda siempre el dashboard.

---

## Comandos

Estos son **exactamente** los que corre el CI (`.github/workflows/ci.yml`). Correrlos antes de subir evita el 90 % de los rojos:

```bash
ruff check .
ruff format --check apps/scheduling apps/advisors apps/clients apps/users apps/integrations
python manage.py makemigrations --check --dry-run
pytest -q
```

Tres cosas que sorprenden:

1. **`ruff format --check` no cubre el repo entero**, solo esas cinco apps. `apps/inbox`, `apps/analytics`, `apps/companies` y `config/` quedan fuera. Correr `ruff format .` a secas reformatea archivos que el CI nunca mira y mete ruido en el diff.
2. **`ruff check .` sí cubre todo** (menos migraciones).
3. **`makemigrations --check` falla si tocas un modelo y no generas la migración.** Es el segundo rojo más común.

El CI corre en todos los pull requests.

---

## Estructura

| App | Qué es |
|---|---|
| `companies` | El tenant. `Company`, y los abstractos `TimeStampedModel` y `CompanyOwnedModel` de los que hereda casi todo |
| `users` | `User` con login por email y rol ADMIN / SUPERVISOR / ADVISOR. `GET /users/me/permissions/` publica las capacidades que consume la interfaz |
| `advisors` | `Advisor`, disponibilidad semanal y supervisión |
| `clients` | `Client` y la normalización de teléfonos |
| `scheduling` | El núcleo: `Event`, `EventHistory`, `SchedulingConfiguration`. Aquí viven `selectors.py` y `exceptions.py`, que usa **todo** el proyecto |
| `integrations` | Endpoints con los que el chatbot agenda visitas |
| `inbox` | CRM conversacional de WhatsApp: canal, contactos, conversaciones, mensajes, webhooks |
| `analytics` | Dashboard de solo lectura bajo `/api/v1/dashboard/` |

En `config/`: settings por entorno, `urls.py` y `pagination.py`. **No definas settings en `config/settings/__init__.py`**, está vacío a propósito.

---

## Convenciones

Todas salen de leer el código, no de una guía. Si escribes algo nuevo, cópialas.

### Aislamiento multiempresa

Es la regla que nunca se rompe (`docs/multi_tenant_rules.md`):

- La empresa sale de `request.user.company`, o de la credencial del canal en los endpoints máquina a máquina. **Nunca del cuerpo de la petición.**
- Todo queryset arranca filtrado por empresa. Como `get_object()` se construye sobre el queryset recortado, un UUID de otro tenant devuelve **404, no 403**: no se confirma que el recurso exista.
- `get_user_company(user)` (`apps/scheduling/selectors.py`) es la puerta de entrada y lanza 403 si el usuario no tiene empresa, en vez de reventar con un 500.

### Alcance por rol

El patrón canónico es `get_events_visible_to_user` en `apps/scheduling/selectors.py`: parte del queryset de la empresa y recorta según el rol — ADMIN todo, SUPERVISOR su equipo, ADVISOR lo suyo, y `qs.none()` como fallback.

Se repite tal cual en `apps/advisors/views.py` (`advisors_visible_to_user`). **Si añades un recurso con alcance por rol, cópialo; no inventes otra forma.** Argumentos keyword-only (`*, user`).

### Errores de dominio

Heredan de `DomainError` (`apps/scheduling/exceptions.py`). **No crees una jerarquía nueva**: `apps/inbox/exceptions.py` y `apps/analytics/exceptions.py` heredan de ahí. Cada subclase declara `code` y `status_code`.

El manejador global las serializa siempre igual:

```json
{"error": {"code": "...", "message": "...", "details": {}}}
```

### Vistas y servicios

- **ViewSets** cuando hay un modelo detrás y el recurso es CRUD. Las operaciones de dominio van como `@action(detail=True, methods=["post"])`: `confirm`, `cancel`, `reassign`…
- **`@api_view`** cuando el recurso es derivado o no hay modelo: analytics, inbox, calendario, integraciones.
- **La lógica vive en `services/`.** Las vistas parsean la petición, resuelven el alcance y delegan. Funciones keyword-only, y `@transaction.atomic` cuando escriben varias tablas.

Hay dos excepciones reales en el código (`apps/integrations/views.py` crea el evento inline, y los `activate`/`deactivate` guardan en la vista). Son deuda, no el modelo a seguir.

### Autenticación máquina a máquina

Los webhooks no usan sesión. El patrón está en `apps/inbox/views.py`: la credencial llega en `X-API-Key`, se resuelve contra un hash guardado en la base, y **de ahí sale la empresa**. Se compara con `constant_time_compare`.

Si llegan credencial y cookie a la vez, **manda la credencial**: así una identidad no puede suplantar a la otra.

El webhook responde 200 incluso con payloads que no puede procesar, para no provocar reintentos del proveedor.

### Idioma

Comentarios, docstrings y mensajes de usuario **en español**. Identificadores, códigos de error y enums **en inglés**.

Los comentarios explican **por qué**, no qué hace la línea. Ejemplo real:

```python
# Orden explícito: sin él la paginación de DRF puede devolver la misma fila
# en dos páginas distintas (UnorderedObjectListWarning).
```

**Los nombres de tests siguen el idioma del archivo que estés tocando**, no una regla global: `apps/analytics/tests/` está en inglés, el resto en español. No unifiques.

---

## Tests

Viven en `apps/<app>/tests/`, agrupados por tema y no por archivo de código. `pytestmark = pytest.mark.django_db` a nivel de módulo.

Cuatro costumbres que conviene mantener:

1. **Dos empresas siempre.** Los `conftest.py` montan `company` y `other_company` porque lo primero que hay que poder demostrar es que la B no ve nada de la A.
2. **Fechas relativas a `timezone.now()`**, nunca literales, para que las pruebas no caduquen.
3. **Helpers a nivel de módulo** (`make_company`, `make_advisor`, `make_event`), importados con `from .conftest import make_event`. No son fixtures.
4. **Nadie toca la red.** Se dobla con un fixture `autouse` que hace `monkeypatch.setattr` **sobre el módulo de servicio** (`ycloud`, `n8n`), nunca sobre `requests`. Ver `apps/inbox/tests/conftest.py`.

---

## Trampas

Cosas que cuestan un despliegue o un ciclo de CI si no se saben.

**Las migraciones NO se ejecutan al desplegar.** El filesystem de Vercel es de solo lectura y no hay hook de post-deploy. Se corren desde tu máquina contra la misma base, **antes** de promover:

```bash
DJANGO_ENV=production python manage.py migrate
```

Si se olvida, el síntoma es `no such table: ...`. `build_files.sh` solo hace `collectstatic`.

**No crees `pyproject.toml` en la raíz.** Vercel lo detecta, cambia el instalador a `uv`, y `uv` aborta el build si el archivo no declara una tabla `[project]`. Por eso la config de ruff vive en `ruff.toml`; está comentado ahí y hay un commit que lo revierte.

**Las migraciones no se formatean ni se lintan.** `extend-exclude = ["*/migrations/*"]`: son historial, se dejan como las generó Django.

**Restricciones de Vercel serverless.** `maxDuration` es de 60 s por función; cualquier trabajo por lotes tiene que acotarse y ser reanudable. La base es Postgres a través del pooler de Supabase en el puerto 6543, con `CONN_MAX_AGE = 0` y sin cursores de servidor. `SECRET_KEY` y `DJANGO_ENV` tienen que existir también en el entorno de **build**, porque `collectstatic` inicializa Django.

**`USE_HTTPS=True` con gunicorn en local** produce una redirección infinita a `https://127.0.0.1:8000`. Ponlo en `False`.

**`ALLOWED_HOSTS` no acepta puerto**: `"0.0.0.0:8000"` no coincide nunca.

---

## Documentos que van por detrás del código

**`DEPLOYMENT.md` está desactualizado en toda la sección de Vercel.** Describe un `api/index.py` que no existe, un `vercel.json` con `builds` que ya no se usa, y dice que `build_files.sh` instala dependencias cuando solo hace `collectstatic`.

**El código es el que despliega bien.** No lo "arregles" para que cuadre con el documento.

Regla general: el README manda sobre el arranque, pero **el CI manda sobre los comandos**.

---

## Datos de prueba

```bash
python manage.py seed_demo --company-slug demo
```
5 asesores, 5 clientes y 5 eventos. Idempotente. Contraseña `Demo1234!`.

```bash
python manage.py seed_dashboard_demo --company demo [--dry-run] [--undo]
```
Datos para que el dashboard tenga volumen. Marca todo lo que crea y `--undo` borra exactamente eso. Pide confirmación si la base no es SQLite.

```bash
python manage.py inbox_channel --company demo --rotate-key
```
Alta y mantenimiento del canal de WhatsApp. Es la **única forma de ver la credencial del webhook en claro**: se guarda hasheada y solo se muestra al generarla.

---

## Flujo de contribución

`staging` es la rama de integración, igual que en el frontend:

```bash
git checkout -b tipo/descripcion-corta origin/staging
# ...cambios...
ruff check . && ruff format --check apps/scheduling apps/advisors apps/clients apps/users apps/integrations
python manage.py makemigrations --check --dry-run && pytest -q
git push -u origin tipo/descripcion-corta
```

Los PR apuntan a `staging`, no a `main`. Mensajes de commit en Conventional Commits, en español: `feat(clients): ...`, `fix: ...`, `test: ...`.

---

## Documentación

| Documento | Cubre |
|---|---|
| `docs/multi_tenant_rules.md` | Las reglas de aislamiento. Fuente normativa |
| `docs/agenda_architecture.md` | Separación de apps y estrategias de asignación de asesor |
| `docs/api_endpoints.md` | Índice de todos los endpoints |
| `docs/event_workflows.md` | Máquina de estados del evento y transiciones prohibidas |
| `docs/permissions_matrix.md` | Qué ve y qué puede hacer cada rol |
| `docs/inbox_whatsapp.md` | Diseño del inbox y sus reglas de negocio |
| `docs/chatbot_endpoints.md` · `docs/chatbot_messages.md` | Contratos del chatbot: agendar y mensajería |
| `docs/frontend_endpoints.md` · `docs/frontend_dashboard.md` | Lo que consume el panel web |
| `REFERENCIA_TECNICA.md` | La especificación original del CRM conversacional |
