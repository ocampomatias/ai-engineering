"""Orquestador multi-agente con supervisor (pre-entrega 6).

Un supervisor delega en un investigador (base de cobros y documentación) y un
analista (cálculos y validación), y una síntesis redacta la respuesta final.
"""

from .graph import RECURSION_LIMIT, Ejecucion, construir_orquestador, ejecutar, rutear
from .state import Aporte, Decision, EstadoOrquestador

__all__ = [
    "RECURSION_LIMIT",
    "Aporte",
    "Decision",
    "Ejecucion",
    "EstadoOrquestador",
    "construir_orquestador",
    "ejecutar",
    "rutear",
]
