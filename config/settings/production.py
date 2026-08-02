import os

import dj_database_url

from .base import *


DEBUG = False


# -----------------------------------------------------------------------------
# Hosts permitidos
# -----------------------------------------------------------------------------

ALLOWED_HOSTS = [
    ".vercel.app",
]

DJANGO_DOMAIN = os.getenv("DJANGO_DOMAIN")

if DJANGO_DOMAIN:
    ALLOWED_HOSTS.append(DJANGO_DOMAIN)


# -----------------------------------------------------------------------------
# Base de datos PostgreSQL / Supabase
# -----------------------------------------------------------------------------
from .base import *


DEBUG = False

DATABASES = {
    "default": dj_database_url.parse(
        env("DATABASE_URL"),
        conn_max_age=0,
        ssl_require=True,
    )
}

# -----------------------------------------------------------------------------
# Seguridad HTTPS
# ----------------------------------------------------v-------------------------

SECURE_PROXY_SSL_HEADER = (
    "HTTP_X_FORWARDED_PROTO",
    "https",
)

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

SECURE_SSL_REDIRECT = True

SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True


# -----------------------------------------------------------------------------
# CSRF
# -----------------------------------------------------------------------------

CSRF_TRUSTED_ORIGINS = [
    "https://*.vercel.app",
]

FRONTEND_URL = os.getenv("FRONTEND_URL")
BACKEND_URL = os.getenv("BACKEND_URL")

if FRONTEND_URL:
    CSRF_TRUSTED_ORIGINS.append(FRONTEND_URL)

if BACKEND_URL:
    CSRF_TRUSTED_ORIGINS.append(BACKEND_URL)


# -----------------------------------------------------------------------------
# CORS
# -----------------------------------------------------------------------------

CORS_ALLOWED_ORIGINS = []

if FRONTEND_URL:
    CORS_ALLOWED_ORIGINS.append(FRONTEND_URL)

CORS_ALLOW_CREDENTIALS = True