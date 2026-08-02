"""Entrypoint de la función serverless de Vercel.

Vercel importa este módulo y busca una variable `app` (o `application`)
que sea un callable WSGI.
"""

import os
import sys
from pathlib import Path

# La raíz del proyecto no está en sys.path cuando Vercel ejecuta api/index.py.
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("DJANGO_ENV", "production")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()

# Vercel espera `app`.
app = application
