#!/usr/bin/env bash

set -euo pipefail

export DJANGO_ENV="${DJANGO_ENV:-production}"
export DJANGO_SETTINGS_MODULE="config.settings.production"

echo "==> Ejecutando collectstatic"
python manage.py collectstatic --noinput --clear

echo "==> Build completado"