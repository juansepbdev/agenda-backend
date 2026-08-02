#!/usr/bin/env bash
# Build de Vercel. Las dependencias las instala Vercel desde requirements.txt
# antes de ejecutar este script: aquí NO se instala nada.
#
# Nota: Vercel ejecuta `collectstatic` por su cuenta cuando detecta STATIC_ROOT.
# Se deja aquí de forma explícita para que el build falle de inmediato (y con un
# error legible) si los settings de producción no cargan.
set -euo pipefail

export DJANGO_ENV="${DJANGO_ENV:-production}"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.production}"

echo "==> Ejecutando collectstatic"
python manage.py collectstatic --noinput

echo "==> Build completado correctamente"
