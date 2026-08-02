# Configuración, ejecución y despliegue

Guía para correr el backend en **desarrollo**, en **producción local** y desplegarlo en **Vercel**.

---

## 1. Cómo está organizada la configuración

```
config/settings/
├── __init__.py     # vacío a propósito: NO definir settings aquí
├── base.py         # común a todos los entornos
├── local.py        # desarrollo: SQLite, DEBUG=True, CORS abierto
└── production.py   # PostgreSQL/Supabase, seguridad HTTPS, Vercel
```

El módulo de settings **se elige solo** a partir de la variable de entorno `DJANGO_ENV`:

| `DJANGO_ENV` | Módulo cargado             | Archivo `.env` que se lee |
|--------------|----------------------------|---------------------------|
| `local` (por defecto) | `config.settings.local`      | `.env.local`        |
| `production` | `config.settings.production` | `.env.production`   |

Esa lógica vive en `manage.py`, `config/wsgi.py`, `config/asgi.py` y `api/index.py`.
No hace falta pasar `--settings=...` nunca.

> **Importante:** `.env.<entorno>` sólo se lee si el archivo existe, y **nunca sobreescribe**
> variables que ya estén en el entorno. Por eso en Vercel mandan siempre las variables del
> dashboard, aunque el archivo llegara a subirse.

---

## 2. Variables de entorno

Hay dos plantillas versionadas: `.env.local.example` y `.env.production.example`.

### Comunes

| Variable | Obligatoria | Descripción |
|---|---|---|
| `DJANGO_ENV` | sí | `local` o `production` |
| `SECRET_KEY` | sí | Clave secreta de Django |
| `ALLOWED_HOSTS` | no | Lista separada por comas |
| `CSRF_TRUSTED_ORIGINS` | no | Lista con esquema (`https://...`) |
| `CORS_ALLOWED_ORIGINS` | no | Lista con esquema |

### Sólo producción

| Variable | Obligatoria | Descripción |
|---|---|---|
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` / `DB_HOST` / `DB_PORT` | sí (opción A) | Credenciales sueltas de Postgres |
| `DB_SSLMODE` | no | Por defecto `require` |
| `DATABASE_URL` | sí (opción B) | Sólo se usa **si `DB_HOST` está vacío** |
| `USE_HTTPS` | no | `True` en Vercel, `False` para correr en local. Por defecto `True` |
| `FRONTEND_URL` | no | Se añade a CORS y CSRF |
| `BACKEND_URL` | no | Se añade a CSRF |
| `DJANGO_DOMAIN` | no | Dominio propio, se añade a `ALLOWED_HOSTS` |

Generar una `SECRET_KEY`:

```bash
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

### Opción A vs Opción B para la base de datos

`production.py` prioriza las variables sueltas: **si `DB_HOST` tiene valor, `DATABASE_URL` se ignora.**

Se recomienda la **opción A** cuando la contraseña tiene caracteres especiales
(`!`, `%`, `@`, `/`, `#`), porque en una URL habría que codificarlos:

```
zA!N7.r%SFeBC9B   ->   zA%21N7.r%25SFeBC9B
```

Una `DATABASE_URL` con la contraseña sin codificar se parsea mal y produce un
error de autenticación difícil de diagnosticar.

---

## 3. Desarrollo local (SQLite)

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.local.example .env.local     # y edita SECRET_KEY

python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

- API: <http://127.0.0.1:8000/api/v1/>
- Swagger: <http://127.0.0.1:8000/api/docs/>
- Admin: <http://127.0.0.1:8000/admin/>

Tests:

```bash
pytest          # configurado en pytest.ini con config.settings.local
ruff check .
```

---

## 4. Producción en local (Supabase + gunicorn)

Sirve para reproducir exactamente lo que corre en Vercel, pero sobre `http://`.

```bash
cp .env.production.example .env.production   # y rellena credenciales
```

En `.env.production` deja **`USE_HTTPS=False`**. Si lo dejas en `True`, Django
responde `301` hacia `https://127.0.0.1:8000` y no vas a poder probar nada.

```bash
export DJANGO_ENV=production

python manage.py check --deploy
python manage.py migrate                     # aplica migraciones en Supabase
python manage.py collectstatic --noinput

gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 2
```

Comprobación rápida:

```bash
curl -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/api/docs/                 # 200
curl -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/static/admin/css/base.css # 200
curl -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/api/v1/events/            # 403 (requiere auth)
```

Los estáticos los sirve **WhiteNoise** desde `staticfiles_build/static/`.

`check --deploy` con `USE_HTTPS=False` avisa de `security.W004/W008/W012/W016`.
Es esperado: son exactamente los settings que `USE_HTTPS` desactiva para poder
correr sobre HTTP. Con `USE_HTTPS=True` esas cuatro advertencias desaparecen.

---

## 5. Despliegue en Vercel

### Archivos implicados

| Archivo | Función |
|---|---|
| `api/index.py` | Función serverless: expone el callable WSGI como `app` |
| `vercel.json` | Rutas y builds (Python + estáticos) |
| `build_files.sh` | Instala dependencias y ejecuta `collectstatic` en el build |
| `.vercelignore` | Excluye `venv/`, `.env*`, `db.sqlite3`, `docs/` del deployment |
| `requirements.txt` | Lo que Vercel instala |

Cómo encajan:

- Toda petición se enruta a `api/index.py`, salvo `/static/*`, que sirve la CDN.
- `build_files.sh` genera `staticfiles_build/static/` y Vercel lo publica como salida estática
  (`distDir: staticfiles_build`), de forma que los archivos quedan bajo `/static/...`.

> El Python del builder de Vercel está gestionado por **uv** y marcado como *externally
> managed* (PEP 668). Un `pip install -r requirements.txt` a secas falla con
> `error: externally-managed-environment`. Por eso `build_files.sh` intenta, en orden:
> `uv pip install --system` → virtualenv aislado → `pip --break-system-packages`.

Al usar `builds` en `vercel.json`, Vercel muestra este aviso, que es **esperado**:

```
WARNING! Due to `builds` existing in your configuration file, the Build and
Development Settings defined in your Project Settings will not apply.
```

Significa que el *Build Command* del dashboard se ignora en favor de `build_files.sh`.

### Variables de entorno en Vercel

En **Project Settings → Environment Variables**, para *Production* (y *Preview* si aplica):

```
DJANGO_ENV=production
SECRET_KEY=<clave de producción, distinta a la de local>
USE_HTTPS=True

DB_NAME=postgres
DB_USER=postgres.wayriylrugxmawhrhrvy
DB_PASSWORD=<password de Supabase>
DB_HOST=aws-0-us-east-1.pooler.supabase.com
DB_PORT=6543
DB_SSLMODE=require

FRONTEND_URL=https://agenda-frontend-gamma.vercel.app
```

`SECRET_KEY` y `DJANGO_ENV` también deben existir en el entorno de **build**, porque
`collectstatic` inicializa Django. En Vercel las variables están disponibles en build
por defecto salvo que las marques como "sensitive/runtime only".

No definas `ALLOWED_HOSTS`: `production.py` añade solo `.vercel.app` más los dominios
que Vercel expone en `VERCEL_URL`, `VERCEL_BRANCH_URL` y `VERCEL_PROJECT_PRODUCTION_URL`.

### Versión de Python

Django 6.0 requiere Python ≥ 3.12. Confírmalo en
**Project Settings → General → Node.js/Python Version** (3.12 en adelante).

### Desplegar

```bash
npm i -g vercel
vercel login
vercel            # preview
vercel --prod     # producción
```

O conectando el repo de GitHub para deploy automático en cada push.

### Migraciones

El filesystem de Vercel es de solo lectura y no hay hook de post-deploy, así que
**las migraciones se corren desde tu máquina** apuntando a la misma base de datos:

```bash
DJANGO_ENV=production python manage.py migrate
```

Hazlo *antes* de promover el deployment a producción.

---

## 6. Decisiones de configuración y por qué

### `DJANGO_SETTINGS_MODULE`

Antes valía `config.settings`, que es el **paquete**. Su `__init__.py` sólo cargaba
dotenv, así que Django arrancaba sin `INSTALLED_APPS`, sin `AUTH_USER_MODEL` y sin
`REST_FRAMEWORK`: silenciosamente usaba los valores por defecto de Django. Ahora el
`__init__.py` está vacío y los entrypoints apuntan a `config.settings.local` o
`config.settings.production`.

### Pooler de Supabase (puerto 6543)

El puerto 6543 es pgbouncer en modo *transaction*. No admite conexiones persistentes
ni cursores del lado del servidor, por eso `production.py` fuerza:

```python
CONN_MAX_AGE = 0
DISABLE_SERVER_SIDE_CURSORS = True
```

Sin `DISABLE_SERVER_SIDE_CURSORS` los `.iterator()` de querysets fallan de forma
intermitente. `CONN_MAX_AGE = 0` además es lo correcto en serverless, donde cada
invocación es un proceso distinto.

El puerto 5432 (conexión directa) no sirve en Vercel: agota el límite de conexiones.

### `SECURE_PROXY_SSL_HEADER`

Estaba definido como tupla de **tres** elementos, incluyendo el host de la base de
datos. Django espera exactamente `(header, valor)`; con tres elementos el
desempaquetado falla y Django nunca reconoce las peticiones como HTTPS. Ahora es
`("HTTP_X_FORWARDED_PROTO", "https")`.

### `ALLOWED_HOSTS`

Contenía `"aws-0-us-east-1.pooler.supabase.com"` (el host de la BD, que no es un host
entrante) y `"0.0.0.0:8000"`. `ALLOWED_HOSTS` no acepta puerto: Django compara contra
el `Host` header ya sin puerto, así que esa entrada nunca coincidía.

### `psycopg2`

Se quitó de `requirements.txt`. El proyecto ya usa `psycopg` 3 con `psycopg-binary`,
que es lo que Django 6 prefiere. `psycopg2` (no `-binary`) compila desde fuente y
necesita `libpq-dev`, que no está en el builder de Vercel: rompía el build.

### Almacenamiento de estáticos

Se usa `whitenoise.storage.CompressedStaticFilesStorage`, **no** la variante
`...Manifest...`. El lambda no incluye `staticfiles_build/`, así que un manifiesto
haría fallar cualquier `{% static %}` en tiempo de request.

### `USE_HTTPS`

Un solo interruptor para `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`,
`CSRF_COOKIE_SECURE` y HSTS. Permite usar el mismo `production.py` en Vercel (HTTPS)
y en local (HTTP) sin duplicar settings.

---

## 7. Problemas frecuentes

| Síntoma | Causa | Solución |
|---|---|---|
| `ImproperlyConfigured: Set the SECRET_KEY environment variable` | Falta el `.env` o la variable en Vercel | Crear `.env.<entorno>` o definirla en el dashboard |
| Redirección infinita a `https://127.0.0.1` en local | `USE_HTTPS=True` en `.env.production` | Ponerlo en `False` |
| `DisallowedHost` | Dominio no listado | Añadirlo a `ALLOWED_HOSTS` o a `DJANGO_DOMAIN` |
| `403 CSRF verification failed` desde el frontend | Origen no confiable | Añadirlo a `CSRF_TRUSTED_ORIGINS` **con esquema** |
| CORS bloqueado en el navegador | Origen no permitido | Definir `FRONTEND_URL` o `CORS_ALLOWED_ORIGINS` |
| `password authentication failed` con `DATABASE_URL` | Caracteres especiales sin codificar | Usar las variables `DB_*` sueltas |
| `500` en `/admin/` en Vercel, sin CSS | `collectstatic` no corrió | Revisar el log del build de `build_files.sh` |
| Build de Vercel: `error: externally-managed-environment` | `pip install` sobre el Python gestionado por uv (PEP 668) | Ya cubierto por `build_files.sh`; no volver a poner un `pip install` pelado |
| Build de Vercel: `Command "./build_files.sh" exited with 1` | Falta `SECRET_KEY` o `DJANGO_ENV` en el entorno de **build** | Definirlas en Environment Variables sin marcarlas como runtime-only |
| `no such table: ...` | Migraciones sin aplicar en Supabase | `DJANGO_ENV=production python manage.py migrate` |

---

## 8. Checklist antes de desplegar

- [ ] `DJANGO_ENV=production python manage.py check --deploy` sin errores de seguridad (con `USE_HTTPS=True`)
- [ ] `SECRET_KEY` de producción distinta a la de desarrollo
- [ ] Variables definidas en Vercel para *Production* y *Preview*
- [ ] `USE_HTTPS=True` en Vercel
- [ ] Migraciones aplicadas contra Supabase
- [ ] `FRONTEND_URL` apunta al dominio real del frontend
- [ ] `.env.production` **no** está en el repositorio (`git check-ignore .env.production`)
