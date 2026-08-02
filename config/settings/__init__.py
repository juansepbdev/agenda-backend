"""Paquete de settings.

No definas settings aquí. Usa uno de los módulos concretos:

    config.settings.local       -> desarrollo (SQLite, DEBUG=True)
    config.settings.production  -> producción (PostgreSQL/Supabase, Vercel)

`manage.py`, `config/wsgi.py` y `config/asgi.py` eligen el módulo
automáticamente según la variable de entorno ``DJANGO_ENV``.
"""
