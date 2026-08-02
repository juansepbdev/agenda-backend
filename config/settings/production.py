"""Settings de producción.

Sirve para dos escenarios:

1. Ejecutar producción en local (gunicorn sobre http://127.0.0.1:8000)
   -> basta con `USE_HTTPS=False` en `.env.production`.
2. Desplegar en Vercel (HTTPS detrás de proxy)
   -> `USE_HTTPS=True` (valor por defecto).
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403

DEBUG = False

# Dominio de producción en Vercel.
VERCEL_DOMAIN = "agenda-backend-tau.vercel.app"


# -----------------------------------------------------------------------------
# Hosts permitidos
# -----------------------------------------------------------------------------
# Se parte de la variable de entorno y se añaden los dominios conocidos, en vez
# de sobreescribir la lista: así sigue funcionando `gunicorn` en local.
# Nunca se usa ALLOWED_HOSTS = ["*"].

ALLOWED_HOSTS = env.list(  # noqa: F405
    "ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1", "0.0.0.0"],
)

for _host in (VERCEL_DOMAIN, ".vercel.app"):
    if _host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_host)

# Vercel expone el dominio del deployment actual en estas variables
# (útil para los deployments de preview, que tienen un subdominio distinto).
for _var in ("VERCEL_URL", "VERCEL_BRANCH_URL", "VERCEL_PROJECT_PRODUCTION_URL"):
    _host = env(_var, default="")  # noqa: F405
    if _host and _host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_host)

# Dominio propio opcional.
DJANGO_DOMAIN = env("DJANGO_DOMAIN", default="")  # noqa: F405
if DJANGO_DOMAIN and DJANGO_DOMAIN not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(DJANGO_DOMAIN)


# -----------------------------------------------------------------------------
# Base de datos PostgreSQL / Supabase
# -----------------------------------------------------------------------------
# Se configura con variables sueltas (DB_NAME / DB_USER / DB_PASSWORD / DB_HOST /
# DB_PORT) en vez de una DATABASE_URL, porque la contraseña de Supabase lleva
# caracteres que en una URL habría que codificar (! % @ / #), y un olvido ahí
# produce un "password authentication failed" difícil de diagnosticar.

_DB_HOST = env("DB_HOST", default="")  # noqa: F405

if not _DB_HOST:
    # Sin esto, DATABASES se quedaría con el SQLite heredado de base.py y
    # producción arrancaría contra un filesystem de solo lectura en Vercel.
    raise ImproperlyConfigured(
        "DB_HOST es obligatorio en producción. Define DB_NAME, DB_USER, "
        "DB_PASSWORD, DB_HOST y DB_PORT en .env.production (local) o en las "
        "Environment Variables del proyecto en Vercel."
    )

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME", default="postgres"),  # noqa: F405
        "USER": env("DB_USER"),  # noqa: F405
        "PASSWORD": env("DB_PASSWORD"),  # noqa: F405
        "HOST": _DB_HOST,
        "PORT": env("DB_PORT", default="6543"),  # noqa: F405
        "OPTIONS": {"sslmode": env("DB_SSLMODE", default="require")},  # noqa: F405
    }
}

# El pooler de Supabase en el puerto 6543 es pgbouncer en modo *transaction*:
# no admite conexiones persistentes ni cursores del lado del servidor.
DATABASES["default"]["CONN_MAX_AGE"] = 0
DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True


# -----------------------------------------------------------------------------
# Seguridad / HTTPS
# -----------------------------------------------------------------------------
# En Vercel el TLS lo termina el proxy, por eso SECURE_PROXY_SSL_HEADER.
# Al correr producción en local sobre http:// hay que poner USE_HTTPS=False,
# de lo contrario Django redirige a https y nada funciona.

USE_HTTPS = env.bool("USE_HTTPS", default=True)  # noqa: F405

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SECURE_SSL_REDIRECT = USE_HTTPS
SESSION_COOKIE_SECURE = USE_HTTPS
CSRF_COOKIE_SECURE = USE_HTTPS

SECURE_HSTS_SECONDS = 31536000 if USE_HTTPS else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = USE_HTTPS
SECURE_HSTS_PRELOAD = USE_HTTPS

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"


# -----------------------------------------------------------------------------
# CSRF / CORS
# -----------------------------------------------------------------------------

FRONTEND_URL = env("FRONTEND_URL", default="")  # noqa: F405
BACKEND_URL = env("BACKEND_URL", default="")  # noqa: F405

CSRF_TRUSTED_ORIGINS = env.list(  # noqa: F405
    "CSRF_TRUSTED_ORIGINS",
    default=[],
)

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])  # noqa: F405

# El dominio propio de Vercel siempre es de confianza: sin esto el login del
# admin en https://agenda-backend-tau.vercel.app/admin/ falla con error CSRF.
for _origin in (f"https://{VERCEL_DOMAIN}", "https://*.vercel.app", FRONTEND_URL, BACKEND_URL):
    if _origin and _origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(_origin)

if FRONTEND_URL and FRONTEND_URL not in CORS_ALLOWED_ORIGINS:
    CORS_ALLOWED_ORIGINS.append(FRONTEND_URL)

CORS_ALLOW_CREDENTIALS = True


# -----------------------------------------------------------------------------
# Logging: a stdout, que es lo que recoge Vercel.
# -----------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "[{levelname}] {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
}
