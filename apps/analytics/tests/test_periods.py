"""Contrato del parseo de rangos: es la base de todos los endpoints."""

from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.analytics.exceptions import AnalyticsValidationError
from apps.analytics.periods import bucket_start, iterate_buckets, parse_period


class FakeCompany:
    timezone = "America/Bogota"


@pytest.fixture
def company():
    return FakeCompany()


def test_default_period_is_thirty_days_in_company_timezone(company):
    period = parse_period({}, company=company)

    assert period.label == "30d"
    assert period.granularity == "day"
    assert str(period.tz) == "America/Bogota"
    assert period.start.utcoffset() == timedelta(hours=-5)


def test_bare_to_date_includes_the_whole_day(company):
    period = parse_period({"from": "2026-08-01", "to": "2026-08-31"}, company=company)

    assert period.start.isoformat().startswith("2026-08-01T00:00:00")
    # Semiabierto: el fin es el 1 de septiembre a medianoche.
    assert period.end.isoformat().startswith("2026-09-01T00:00:00")


def test_granularity_is_deduced_from_the_span(company):
    assert parse_period({"from": "2026-08-22", "to": "2026-08-23"}, company=company).granularity == "hour"
    assert parse_period({"from": "2026-07-01", "to": "2026-08-01"}, company=company).granularity == "day"
    assert parse_period({"from": "2026-01-01", "to": "2026-08-01"}, company=company).granularity == "week"
    assert parse_period({"from": "2024-01-01", "to": "2026-08-01"}, company=company).granularity == "month"


def test_explicit_granularity_wins(company):
    period = parse_period({"from": "2026-07-01", "to": "2026-08-01", "granularity": "week"}, company=company)

    assert period.granularity == "week"


def test_inverted_range_is_rejected(company):
    with pytest.raises(AnalyticsValidationError):
        parse_period({"from": "2026-08-31", "to": "2026-08-01"}, company=company)


def test_unknown_period_is_rejected(company):
    with pytest.raises(AnalyticsValidationError):
        parse_period({"period": "el-mes-pasado"}, company=company)


def test_unknown_granularity_is_rejected(company):
    with pytest.raises(AnalyticsValidationError):
        parse_period({"granularity": "quincena"}, company=company)


def test_unknown_timezone_is_rejected(company):
    with pytest.raises(AnalyticsValidationError):
        parse_period({"tz": "Marte/Olympus"}, company=company)


def test_too_many_buckets_is_rejected(company):
    with pytest.raises(AnalyticsValidationError) as exc:
        parse_period({"from": "2020-01-01", "to": "2026-01-01", "granularity": "hour"}, company=company)

    assert exc.value.details["max_buckets"] == 1000


def test_previous_period_has_the_same_length(company):
    period = parse_period({"period": "7d"}, company=company)
    previous = period.previous()

    assert previous.end == period.start
    assert previous.end - previous.start == period.end - period.start


def test_buckets_cover_the_range_without_gaps(company):
    period = parse_period({"from": "2026-08-01", "to": "2026-08-07"}, company=company)
    buckets = iterate_buckets(period)

    assert len(buckets) == 7
    assert buckets[0].isoformat().startswith("2026-08-01T00:00:00")
    assert buckets[-1].isoformat().startswith("2026-08-07T00:00:00")


def test_bucket_start_uses_local_midnight_not_utc():
    tz = ZoneInfo("America/Bogota")
    # 03:00 UTC del día 23 son las 22:00 locales del día 22.
    moment = timezone.datetime(2026, 8, 23, 3, 0, tzinfo=ZoneInfo("UTC"))

    assert bucket_start(moment, "day", tz).day == 22


def test_week_buckets_start_on_monday(company):
    period = parse_period({"from": "2026-08-01", "to": "2026-08-31", "granularity": "week"}, company=company)

    assert all(bucket.weekday() == 0 for bucket in iterate_buckets(period))
