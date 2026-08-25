import re

from django.db import transaction

from .models import Client


def normalize_phone(phone: str) -> str:
    """Normaliza teléfonos colombianos o internacionales sin almacenar formato ambiguo."""
    digits = re.sub(r"\D", "", phone or "")
    digits = digits.removeprefix("00")
    if len(digits) == 10 and digits.startswith("3"):
        digits = "57" + digits
    if not 8 <= len(digits) <= 15:
        raise ValueError("Número telefónico inválido.")
    return "+" + digits


@transaction.atomic
def create_or_get_client(*, company, phone, defaults):
    normalized_phone = normalize_phone(phone)
    client, created = Client.objects.select_for_update().get_or_create(
        company=company,
        normalized_phone=normalized_phone,
        defaults={**defaults, "phone": phone, "normalized_phone": normalized_phone},
    )
    return client, created
