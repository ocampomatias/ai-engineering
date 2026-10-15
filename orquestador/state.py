"""Estado compartido del orquestador: qué aportó cada agente y qué decidió el supervisor.

Cada campo tiene un solo dueño, que es el único nodo que lo escribe:

| Campo              | Lo escribe   | Reducer                    |
|--------------------|--------------|----------------------------|
| messages           | todos        | add_messages (MessagesState) |
| aportes            | especialistas | operator.add (se acumulan) |
| decisiones         | supervisor   | operator.add (se acumulan) |
| siguiente, instruccion | supervisor | se reemplazan            |
| respuesta_final    | síntesis     | se reemplaza               |

Los aportes y las decisiones son modelos de Pydantic: lo que un agente le pasa
a otro se valida antes de entrar al estado, no cuando el siguiente lo lee.
"""

import operator
from typing import Annotated, Literal, Self

from langgraph.graph import MessagesState
from pydantic import BaseModel, Field, model_validator

Especialista = Literal["investigador", "analista"]
Destino = Literal["investigador", "analista", "sintesis"]


class Aporte(BaseModel):
    """Lo que devolvió un especialista para una instrucción del supervisor."""

    agente: Especialista
    instruccion: str = Field(min_length=1, description="La tarea que le dio el supervisor.")
    contenido: str = Field(min_length=1, description="El resultado, o el error si falló.")
    herramientas: list[str] = Field(description="Herramientas que llamó, en orden.")
    error: bool = Field(default=False, description="True si el especialista no pudo terminar.")
    ms: float = Field(ge=0, description="Duración del especialista.")


class Decision(BaseModel):
    """La salida estructurada del supervisor en cada vuelta."""

    evaluacion: str = Field(
        description=(
            "Evaluación del último aporte con la rúbrica: ¿responde la instrucción?, ¿los datos "
            "son concretos (IDs, montos, códigos)?, ¿hay errores o huecos? Si todavía no hay "
            "aportes, decilo."
        )
    )
    siguiente: Destino = Field(
        description=(
            "Quién interviene ahora: 'investigador' (buscar datos de comercios, cobros o "
            "documentación), 'analista' (cálculos y validación sobre datos ya obtenidos) o "
            "'sintesis' (ya hay todo lo necesario para responder)."
        )
    )
    instruccion: str = Field(
        default="",
        description=(
            "Tarea concreta y autocontenida para el especialista elegido. Si es una corrección, "
            "decí qué faltó. Vacía si el siguiente es 'sintesis'."
        ),
    )
    motivo: str = Field(description="Por qué esa decisión, en una frase.")

    @model_validator(mode="after")
    def _instruccion_para_especialistas(self) -> Self:
        if self.siguiente != "sintesis" and not self.instruccion.strip():
            raise ValueError("delegar a un especialista requiere una instrucción")
        return self


class EstadoOrquestador(MessagesState):
    """El estado del grafo. Hereda `messages` (con add_messages) de MessagesState."""

    aportes: Annotated[list[Aporte], operator.add]
    decisiones: Annotated[list[Decision], operator.add]
    siguiente: Destino
    instruccion: str
    respuesta_final: str
