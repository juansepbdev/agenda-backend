# Agenda Inmobiliaria — Backend

API multiempresa de agenda inmobiliaria (Django + Django REST Framework).

## Documentación

| Documento | Contenido |
|---|---|
| [`REFERENCIA_TECNICA.md`](REFERENCIA_TECNICA.md) | Referencia técnica del proyecto |
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | Despliegue en Vercel y ejecución de producción en local |
| [`docs/`](docs/) | Endpoints, arquitectura, reglas multiempresa y matriz de permisos |

## Puesta en marcha (local)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements/local.txt
cp .env.local.example .env.local   # ajusta SECRET_KEY
python manage.py migrate
python manage.py runserver
```

Documentación interactiva de la API en `/api/docs/`.

## Pruebas

```bash
pytest
```
