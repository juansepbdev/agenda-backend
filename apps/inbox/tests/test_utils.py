"""Funciones puras: teléfono, avatar y marcas de tiempo."""

from datetime import datetime
from datetime import timezone as dt_timezone

import pytest

from apps.inbox.utils import (
    avatar_color_for_phone,
    avatar_initial_for,
    normalize_phone,
    parse_wa_timestamp,
    source_id_for_phone,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("57 300-111 22 33", "+573001112233"),
        ("+573001112233", "+573001112233"),
        ("(300) 111 2233", "+3001112233"),
        ("", ""),
        (None, ""),
        ("sin dígitos", ""),
    ],
)
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    ("name", "phone", "expected"),
    [("diego", "+57300", "D"), ("", "+57300", "+"), ("", "", "?"), ("  ", "  ", "?")],
)
def test_avatar_initial_for(name, phone, expected):
    assert avatar_initial_for(name, phone) == expected


def test_avatar_color_es_estable_y_varia_por_telefono():
    color = avatar_color_for_phone("+573001112233")

    assert color == avatar_color_for_phone("+573001112233")
    assert len(color) == 7 and color.startswith("#")
    assert color != avatar_color_for_phone("+573009998877")


def test_source_id_quita_el_mas():
    assert source_id_for_phone("+573001112233") == "wa_573001112233"
    assert source_id_for_phone("") == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-08-20T16:00:00.000Z", datetime(2026, 8, 20, 16, 0, tzinfo=dt_timezone.utc)),
        ("2026-08-20T16:00:00+00:00", datetime(2026, 8, 20, 16, 0, tzinfo=dt_timezone.utc)),
        ("2026-08-20T16:00:00", datetime(2026, 8, 20, 16, 0, tzinfo=dt_timezone.utc)),
        (1755705600, datetime.fromtimestamp(1755705600, tz=dt_timezone.utc)),
        ("1755705600", datetime.fromtimestamp(1755705600, tz=dt_timezone.utc)),
    ],
)
def test_parse_wa_timestamp_formatos_conocidos(raw, expected):
    assert parse_wa_timestamp(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "no es una fecha"])
def test_parse_wa_timestamp_cae_a_ahora(raw):
    parsed = parse_wa_timestamp(raw)

    assert parsed.tzinfo is not None
