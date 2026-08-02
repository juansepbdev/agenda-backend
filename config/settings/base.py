"""Settings comunes a todos los entornos.

Los valores concretos se leen de variables de entorno. En local se cargan
desde `.env.<DJANGO_ENV>` (por ejemplo `.env.local` o `.env.production`);
en Vercel se inyectan directamente en el entorno del proceso.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    CSRF_TRUSTED_ORIGINS=(list, []),
    CORS_ALLOWED_ORIGINS=(list, []),
)

# Entorno activo: "local" (por defecto) o "production".
DJANGO_ENV = env("DJANGO_ENV", default="local")

# Carga del archivo .env correspondiente. `read_env` NO sobreescribe variables
# que ya existan en el entorno, así que en Vercel manda siempre el dashboard.
env_file = BASE_DIR / f".env.{DJANGO_ENV}"
if env_file.exists():
    env.read_env(env_file)

SECRET_KEY = env("SECRET_KEY")

DEBUG = env.bool("DEBUG", default=False)

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    "rest_framework",
    "corsheaders",
    "drf_spectacular",

    "apps.companies.apps.CompaniesConfig",
    "apps.users.apps.UsersConfig",
    "apps.advisors.apps.AdvisorsConfig",
    "apps.clients.apps.ClientsConfig",
    "apps.scheduling.apps.SchedulingConfig",
    "apps.integrations.apps.IntegrationsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "es-co"

TIME_ZONE = "America/Bogota"

USE_I18N = True
USE_TZ = True

# -----------------------------------------------------------------------------
# Archivos estáticos
# -----------------------------------------------------------------------------
# STATIC_ROOT termina en `staticfiles_build/static` a propósito: en Vercel el
# directorio `staticfiles_build` se publica como salida estática, de forma que
# los archivos quedan servidos por la CDN bajo la ruta `/static/...`.

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles_build" / "static"

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        # Sin manifiesto: el lambda de Vercel no contiene `staticfiles_build`,
        # así que un ManifestStaticFilesStorage fallaría al renderizar plantillas.
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "users.User"

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "EXCEPTION_HANDLER": "apps.scheduling.exceptions.api_exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Agenda Inmobiliaria API",
    "DESCRIPTION": "API multiempresa de agenda inmobiliaria.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}
