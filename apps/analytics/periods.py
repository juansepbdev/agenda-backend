"""Ventana temporal de una consulta al dashboard.

Todo endpoint de métricas comparte el mismo contrato de rango y agrupación, así
que el parseo vive en un solo sitio. Tres reglas de diseño:

* **El rango es semiabierto** `[start, end)`. Con `to=2026-08-23` el día 23
  entra completo: `end` se calcula como el 24 a las 00:00. Un rango cerrado
  obligaría a sumar y restar microsegundos en cada agregación.
* **La zona horaria es la de la empresa**, no la del servidor ni UTC. Un
  "mensajes por día" agrupado en UTC parte el día colombiano a las 19:00 y
  produce gráficas que no cuadran con lo que ve el usuario.
* **La granularidad se deduce del tamaño del rango** si no se pide una: 90 días
  agrupados por hora son 2160 puntos que ninguna gráfica puede dibujar.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

from .exceptions import AnalyticsValidationError

GRANULARITIES = ("hour", "day", "week", "month")

# Alias -> número de días hacia atrás desde hoy (incluyendo hoy).
RELATIVE_PERIODS = {
    "today": 1,
    "yesterday": 1,
    "7d": 7,
    "14d": 14,
    "30d": 30,
    "90d": 90,
    "180d": 180,
    "365d": 365,
}
CALENDAR_PERIODS = ("mtd", "ytd")
DEFAULT_PERIOD = "30d"

# Techo de puntos por serie: protege al frontend y a la base de datos de una
# consulta como `from=2020-01-01&granularity=hour`.
MAX_BUCKETS = 1000


@dataclass(frozen=True)
class Period:
    """Rango `[start, end)` con su zona y su granularidad ya resueltas."""

    start: datetime
    end: datetime
    granularity: str
    tz: ZoneInfo
    label: str

    @property
    def days(self) -> float:
        return (self.end - self.start).total_seconds() / 86400

    def previous(self) -> "Period":
        """Ventana inmediatamente anterior y de la misma duración.

        Es la base de las variaciones porcentuales de las tarjetas KPI: "hoy
        vs. ayer", "estos 30 días vs. los 30 anteriores".
        """
        span = self.end - self.start
        return Period(
            start=self.start - span,
            end=self.start,
            granularity=self.granularity,
            tz=self.tz,
            label=f"{self.label} (anterior)",
        )

    def as_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "granularity": self.granularity,
            "timezone": str(self.tz),
            "label": self.label,
        }


# -----------------------------------------------------------------------------
# Parseo
# -----------------------------------------------------------------------------

def resolve_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise AnalyticsValidationError(
            "Zona horaria desconocida.", details={"timezone": name}
        ) from exc


def _parse_boundary(raw: str, tz: ZoneInfo, *, field: str, end_of_day: bool):
    """Acepta `YYYY-MM-DD` o un ISO-8601 completo.

    Una fecha suelta se interpreta en la zona de la empresa; en `to`, además,
    se lleva al final del día, que es lo que espera quien escribe
    `?from=2026-08-01&to=2026-08-31`.
    """
    text = str(raw).strip()
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AnalyticsValidationError(
            f"`{field}` debe ser una fecha `YYYY-MM-DD` o un ISO-8601.",
            details={field: raw},
        ) from exc

    is_bare_date = len(text) == 10
    if is_bare_date and end_of_day:
        moment = datetime.combine(moment.date() + timedelta(days=1), time.min)

    if timezone.is_naive(moment):
        return moment.replace(tzinfo=tz)
    return moment


def _relative_range(period_id: str, tz: ZoneInfo):
    today = timezone.now().astimezone(tz).date()

    if period_id == "yesterday":
        start_date = today - timedelta(days=1)
        return _midnight(start_date, tz), _midnight(today, tz)

    if period_id == "mtd":
        return _midnight(today.replace(day=1), tz), _midnight(today + timedelta(days=1), tz)

    if period_id == "ytd":
        return _midnight(date(today.year, 1, 1), tz), _midnight(today + timedelta(days=1), tz)

    days = RELATIVE_PERIODS[period_id]
    return _midnight(today - timedelta(days=days - 1), tz), _midnight(today + timedelta(days=1), tz)


def _midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz)


def _auto_granularity(start: datetime, end: datetime) -> str:
    span_days = (end - start).total_seconds() / 86400
    if span_days <= 2:
        return "hour"
    if span_days <= 62:
        return "day"
    if span_days <= 366:
        return "week"
    return "month"


def parse_period(params, *, company, default_period: str = DEFAULT_PERIOD) -> Period:
    """Construye el `Period` a partir de los query params de la petición.

    Parámetros aceptados: `from`, `to`, `period`, `granularity`, `tz`.
    `from`/`to` tienen prioridad sobre `period`.
    """
    tz = resolve_timezone(params.get("tz") or company.timezone or "America/Bogota")

    raw_from = params.get("from") or params.get("start")
    raw_to = params.get("to") or params.get("end")
    period_id = (params.get("period") or default_period).strip().lower()

    if raw_from or raw_to:
        now = timezone.now().astimezone(tz)
        start = _parse_boundary(raw_from, tz, field="from", end_of_day=False) if raw_from else None
        end = _parse_boundary(raw_to, tz, field="to", end_of_day=True) if raw_to else now
        if start is None:
            start = end - timedelta(days=RELATIVE_PERIODS[DEFAULT_PERIOD])
        label = "custom"
    else:
        if period_id not in RELATIVE_PERIODS and period_id not in CALENDAR_PERIODS:
            raise AnalyticsValidationError(
                "`period` no reconocido.",
                details={
                    "received": period_id,
                    "allowed": sorted(set(RELATIVE_PERIODS) | set(CALENDAR_PERIODS)),
                },
            )
        start, end = _relative_range(period_id, tz)
        label = period_id

    if end <= start:
        raise AnalyticsValidationError(
            "El rango está invertido: `to` debe ser posterior a `from`.",
            details={"from": start.isoformat(), "to": end.isoformat()},
        )

    granularity = (params.get("granularity") or "").strip().lower()
    if granularity and granularity not in GRANULARITIES:
        raise AnalyticsValidationError(
            "`granularity` no reconocida.",
            details={"received": granularity, "allowed": list(GRANULARITIES)},
        )
    granularity = granularity or _auto_granularity(start, end)

    period = Period(start=start, end=end, granularity=granularity, tz=tz, label=label)
    _guard_bucket_count(period)
    return period


def _guard_bucket_count(period: Period) -> None:
    approximate = {
        "hour": period.days * 24,
        "day": period.days,
        "week": period.days / 7,
        "month": period.days / 28,
    }[period.granularity]
    if approximate > MAX_BUCKETS:
        raise AnalyticsValidationError(
            "El rango es demasiado largo para esa granularidad.",
            details={
                "granularity": period.granularity,
                "estimated_buckets": int(approximate),
                "max_buckets": MAX_BUCKETS,
            },
        )


# -----------------------------------------------------------------------------
# Cubetas
# -----------------------------------------------------------------------------

def bucket_start(moment: datetime, granularity: str, tz: ZoneInfo) -> datetime:
    """Inicio de la cubeta a la que pertenece `moment`, en hora local.

    Se trunca en Python y no en SQL a propósito: cada motor devuelve el
    truncado en una zona distinta, y la clave de la serie tiene que ser la
    misma que genera `iterate_buckets`.
    """
    local = moment.astimezone(tz)
    if granularity == "hour":
        return local.replace(minute=0, second=0, microsecond=0)
    if granularity == "day":
        return _midnight(local.date(), tz)
    if granularity == "week":
        # Semana ISO: empieza en lunes, igual que `TruncWeek` de Django.
        return _midnight(local.date() - timedelta(days=local.weekday()), tz)
    return _midnight(local.date().replace(day=1), tz)


def next_bucket(moment: datetime, granularity: str, tz: ZoneInfo) -> datetime:
    if granularity == "hour":
        return moment + timedelta(hours=1)
    if granularity == "day":
        return _midnight(moment.date() + timedelta(days=1), tz)
    if granularity == "week":
        return _midnight(moment.date() + timedelta(days=7), tz)
    year, month = moment.year + (moment.month // 12), (moment.month % 12) + 1
    return _midnight(date(year, month, 1), tz)


def iterate_buckets(period: Period) -> list[datetime]:
    """Todas las cubetas del periodo, incluidas las vacías.

    Rellenar los huecos aquí evita que cada cliente del dashboard tenga que
    reconstruir el eje temporal para pintar una línea sin saltos.
    """
    buckets, cursor = [], bucket_start(period.start, period.granularity, period.tz)
    while cursor < period.end and len(buckets) <= MAX_BUCKETS:
        buckets.append(cursor)
        cursor = next_bucket(cursor, period.granularity, period.tz)
    return buckets
