"""Salida a WhatsApp vía YCloud.

Una sola función pública que **nunca lanza excepciones**: siempre devuelve la
misma forma. El llamador persiste el resultado (R6).
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://api.ycloud.com/v2"
DEFAULT_TIMEOUT = 30.0


def _result(*, ok, error=None, wamid=None, raw=None, status_code=None) -> dict:
    return {"ok": ok, "error": error, "wamid": wamid, "raw": raw, "status_code": status_code}


def send_whatsapp_text(*, channel, to: str, body: str, from_number: str | None = None) -> dict:
    """POST /whatsapp/messages. Cortocircuita sin gastar petición si falta config."""
    sender = from_number or (channel.ycloud_from if channel else "")
    api_key = channel.ycloud_api_key if channel else ""

    if not api_key:
        return _result(ok=False, error="YCloud API key no configurada para la empresa.")
    if not sender:
        return _result(ok=False, error="Número remitente de YCloud no configurado.")
    if not to:
        return _result(ok=False, error="Destinatario vacío.")
    if not body:
        return _result(ok=False, error="Cuerpo del mensaje vacío.")

    api_base = (channel.ycloud_api_base or getattr(settings, "INBOX_YCLOUD_API_BASE", DEFAULT_API_BASE)).rstrip("/")
    timeout = getattr(settings, "INBOX_YCLOUD_TIMEOUT", DEFAULT_TIMEOUT)
    url = f"{api_base}/whatsapp/messages"
    payload = {"from": sender, "to": to, "type": "text", "text": {"body": body}}

    try:
        response = requests.post(
            url,
            json=payload,
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        logger.warning("YCloud inalcanzable (%s): %s", url, exc)
        return _result(ok=False, error=f"Error de red hacia YCloud: {exc}")

    try:
        raw = response.json()
    except ValueError:
        raw = response.text

    if response.status_code >= 400:
        logger.warning("YCloud respondió %s: %s", response.status_code, raw)
        return _result(ok=False, error=f"YCloud respondió {response.status_code}", raw=raw, status_code=response.status_code)

    wamid = None
    if isinstance(raw, dict):
        wamid = raw.get("wamid") or raw.get("id")
    return _result(ok=True, wamid=wamid, raw=raw, status_code=response.status_code)
