"""Aritmética compartida por las métricas: porcentajes, variaciones y cuantiles."""


def rate(part: int, whole: int, *, digits: int = 2) -> float:
    """Porcentaje 0-100. Sin base no hay tasa, y `0.0` es la respuesta honesta."""
    if not whole:
        return 0.0
    return round(part * 100 / whole, digits)


def average(values, *, digits: int = 2) -> float | None:
    """`None` con lista vacía: una media de cero muestras no es cero."""
    if not values:
        return None
    return round(sum(values) / len(values), digits)


def percentile(sorted_values: list[float], fraction: float, *, digits: int = 2) -> float | None:
    """Cuantil por interpolación lineal sobre una lista **ya ordenada**."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return round(sorted_values[0], digits)

    position = fraction * (len(sorted_values) - 1)
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    weight = position - low
    return round(sorted_values[low] * (1 - weight) + sorted_values[high] * weight, digits)


def distribution(values: list[float]) -> dict:
    """Resumen estándar de una muestra de tiempos (en segundos)."""
    ordered = sorted(values)
    return {
        "samples": len(ordered),
        "avg": average(ordered),
        "min": round(ordered[0], 2) if ordered else None,
        "p50": percentile(ordered, 0.50),
        "p90": percentile(ordered, 0.90),
        "max": round(ordered[-1], 2) if ordered else None,
    }


def delta(current, previous) -> dict:
    """Variación absoluta y relativa entre dos periodos.

    `change_pct` es `None` cuando el periodo anterior fue cero: un crecimiento
    "infinito" no es un número que una tarjeta KPI pueda mostrar, y devolver
    `100` mentiría sobre la base de comparación.
    """
    if current is None or previous is None:
        return {"current": current, "previous": previous, "change": None, "change_pct": None}

    change = round(current - previous, 2)
    change_pct = None if not previous else round(change * 100 / previous, 2)
    return {"current": current, "previous": previous, "change": change, "change_pct": change_pct}


def top_n(counter: dict, limit: int) -> list[tuple]:
    """Pares `(clave, valor)` ordenados por valor descendente."""
    return sorted(counter.items(), key=lambda item: (-item[1], str(item[0])))[:limit]
