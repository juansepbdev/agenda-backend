#!/usr/bin/env bash
# Script de build para Vercel (@vercel/static-build).
# Genera los archivos estáticos en `staticfiles_build/static`, que Vercel
# publica en la CDN bajo la ruta /static/.
#
# Ojo: el Python del builder de Vercel está gestionado por uv y marcado como
# "externally managed" (PEP 668), así que `pip install` a secas falla con
# `error: externally-managed-environment`. Por eso probamos, en orden:
#   1. uv (lo que realmente gestiona ese Python)
#   2. un virtualenv aislado
#   3. pip con --break-system-packages
set -euo pipefail

export DJANGO_ENV="${DJANGO_ENV:-production}"
export DJANGO_SETTINGS_MODULE="config.settings.production"

PYTHON_BIN="python3"

if command -v uv >/dev/null 2>&1; then
    echo "==> Instalando dependencias con uv"
    uv pip install --system -r requirements.txt
elif "$PYTHON_BIN" -m venv .vercel_build_venv >/dev/null 2>&1; then
    echo "==> Instalando dependencias en virtualenv aislado"
    # shellcheck disable=SC1091
    source .vercel_build_venv/bin/activate
    PYTHON_BIN="python"
    "$PYTHON_BIN" -m pip install --upgrade pip
    "$PYTHON_BIN" -m pip install -r requirements.txt
else
    echo "==> Instalando dependencias con pip (--break-system-packages)"
    "$PYTHON_BIN" -m pip install --break-system-packages -r requirements.txt
fi

echo "==> collectstatic"
"$PYTHON_BIN" manage.py collectstatic --noinput --clear
