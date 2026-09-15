"""Pipeline de extracción de entidades técnicas con LangChain (LCEL) y Pydantic.

Pre-entrega 2 del curso AI Engineering de Coderhouse. Reutiliza la
configuración del módulo 1 (`llm_client.settings`).
"""

from .chain import (
    ERRORES_REINTENTABLES,
    ExtraccionError,
    SalidaIncompletaError,
    construir_cadena,
    crear_modelo,
    process_text,
)
from .prompts import crear_prompt
from .schemas import EntidadesTecnicas, NivelCriticidad

__all__ = [
    "ERRORES_REINTENTABLES",
    "EntidadesTecnicas",
    "ExtraccionError",
    "NivelCriticidad",
    "SalidaIncompletaError",
    "construir_cadena",
    "crear_modelo",
    "crear_prompt",
    "process_text",
]
