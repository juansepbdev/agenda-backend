"""Funciones puras del inbox: normalización de teléfono, avatar y timestamps."""

import colorsys
import hashlib
import re
from datetime import datetime
from datetime import timezone as dt_timezone

from django.utils import timezone

DEFAULT_AVATAR_COLOR = "#7C3AED"


def normalize_phone(raw: str) -> str:
    """Deja solo dígitos y antepone `+`. `"57 300-111 22 33"` -> `"+573001112233"`.

    A diferencia de `apps.clients.services.normalize_phone`, esta versión nunca
    lanza: los webhooks traen números de cualquier país y un formato raro no
    debe tumbar la ingesta. Cadena vacía si no hay dígitos.
    """
    digits = re.sub(r"\D", "", raw or "")
    return f"+{digits}" if digits else ""


def avatar_initial_for(name: str, phone_number: str) -> str:
    for candidate in (name or "", phone_number or ""):
        stripped = candidate.strip()
        if stripped:
            return stripped[0].upper()
    return "?"


def avatar_color_for_phone(phone_number: str) -> str:
    """Color estable por contacto: md5(teléfono) -> matiz -> HSV(h, 0.55, 0.75)."""
    if not phone_number:
        return DEFAULT_AVATAR_COLOR
    digest = hashlib.md5(phone_number.encode("utf-8")).hexdigest()
    hue = (int(digest[:8], 16) % 360) / 360
    red, green, blue = colorsys.hsv_to_rgb(hue, 0.55, 0.75)
    return f"#{round(red * 255):02X}{round(green * 255):02X}{round(blue * 255):02X}"


def source_id_for_phone(phone_number: str) -> str:
    return f"wa_{phone_number.lstrip('+')}" if phone_number else ""


def parse_wa_timestamp(value) -> datetime:
    """Acepta ISO-8601 (con `Z`), epoch en segundos (int o str) o vacío -> ahora.

    Lo naíf se asume UTC. Sirve para fechar el mensaje con la hora real de
    WhatsApp y no con la de recepción del webhook.
    """
    if value in (None, ""):
        return timezone.now()

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=dt_timezone.utc)

    text = str(value).strip()
    if text.isdigit():
        return datetime.fromtimestamp(int(text), tz=dt_timezone.utc)

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return timezone.now()
    return parsed if timezone.is_aware(parsed) else parsed.replace(tzinfo=dt_timezone.utc)
