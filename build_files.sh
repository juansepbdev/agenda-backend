#!/usr/bin/env bash
# Script de build para Vercel (@vercel/static-build).
# Genera los archivos estáticos en `staticfiles_build/static`, que Vercel
# publica en la CDN bajo la ruta /static/.
set -euo pipefail

export DJANGO_ENV="${DJANGO_ENV:-production}"
export DJANGO_SETTINGS_MODULE="config.settings.production"

python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

python3 manage.py collectstatic --noinput --clear
