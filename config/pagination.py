"""Paginación por defecto de la API.

`PageNumberPagination` de DRF ignora `?page_size=` a menos que se declare
`page_size_query_param`. El frontend lo envía en varios listados (asesores,
clientes, disponibilidades), así que sin esto las respuestas se recortan en
silencio a `PAGE_SIZE`. `max_page_size` evita que el parámetro se convierta en
una forma barata de pedir la tabla entera.
"""

from rest_framework.pagination import PageNumberPagination


class DefaultPagination(PageNumberPagination):
    page_size_query_param = "page_size"
    max_page_size = 200
