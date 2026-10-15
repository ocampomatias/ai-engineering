"""Agentes del orquestador: supervisor, investigador, analista y síntesis."""

from .analyst_agent import crear_analista
from .research_agent import crear_investigador
from .supervisor import MAX_DELEGACIONES, crear_sintesis, crear_supervisor

__all__ = [
    "MAX_DELEGACIONES",
    "crear_analista",
    "crear_investigador",
    "crear_sintesis",
    "crear_supervisor",
]
