"""Puente hacia el agente de IA en n8n.

Reenvía el JSON **crudo de YCloud, sin transformar**: n8n hace su propio
parsing y el backend no le impone un esquema al flujo del bot.

*Fire-and-forget* respecto a la lógica de negocio: cualquier fallo se registra y
se devuelve como `ok: false`, pero el webhook responde 200 igual. El timeout es
corto a propósito, porque bloquea la respuesta al webhook de YCloud.
"""

import logging

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 5.0


def is_configured(channel) -> bool:
    return bool(channel and channel.n8n_webhook_url)


def forward_ycloud_event(channel, payload: dict) -> dict:
    if not is_configured(channel):
        return {"ok": False, "error": "n8n no configurado para la empresa.", "status_code": None}

    timeout = channel.n8n_timeout_seconds or DEFAULT_TIMEOUT
    try:
        response = requests.post(channel.n8n_webhook_url, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        logger.warning("n8n inalcanzable (%s): %s", channel.n8n_webhook_url, exc)
        return {"ok": False, "error": f"Error de red hacia n8n: {exc}", "status_code": None}

    if response.status_code >= 400:
        logger.warning("n8n respondió %s: %s", response.status_code, response.text[:500])
        return {"ok": False, "error": f"n8n respondió {response.status_code}", "status_code": response.status_code}

    return {"ok": True, "error": None, "status_code": response.status_code}
