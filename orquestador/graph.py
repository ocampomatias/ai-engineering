"""El grafo del orquestador: un supervisor que delega en dos especialistas y una síntesis final.

    START -> supervisor -(rutear)-> investigador -> supervisor
                        -(rutear)-> analista     -> supervisor
                        -(rutear)-> sintesis     -> END

Topología jerárquica: los especialistas nunca hablan entre sí; todo pasa por
el supervisor, que es el único que decide el flujo. La función de ruteo
devuelve un `Literal` con los nombres de los nodos, así el grafo sabe de
antemano todos los destinos posibles (y el diagrama Mermaid los dibuja).
"""

import logging
import time
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field

from agente.documentacion import ModoBuscador
from pipeline.chain import crear_modelo

from .agents import crear_analista, crear_investigador, crear_sintesis, crear_supervisor
from .state import Aporte, Decision, EstadoOrquestador

logger = logging.getLogger(__name__)

#: Techo de pasos del grafo: 6 delegaciones x 2 (ida y vuelta) + supervisor + síntesis.
RECURSION_LIMIT = 16


def rutear(estado: EstadoOrquestador) -> Literal["investigador", "analista", "sintesis"]:
    """Arista condicional: el destino es lo que decidió el supervisor."""
    return estado["siguiente"]


def construir_orquestador(
    modelo_supervisor: BaseChatModel | None = None,
    modelo_especialistas: BaseChatModel | None = None,
    *,
    investigador: Any = None,  # noqa: ANN401 - nodos inyectables para los tests
    analista: Any = None,  # noqa: ANN401
    buscador: ModoBuscador = "auto",
) -> CompiledStateGraph:
    """Compila el grafo. Supervisor y especialistas pueden usar modelos distintos.

    Por defecto todos usan el modelo del `.env`; en producción convendría un modelo más
    capaz para el supervisor y uno barato para los especialistas.
    """
    modelo_supervisor = modelo_supervisor or crear_modelo(max_tokens=1024)
    modelo_especialistas = modelo_especialistas or modelo_supervisor

    grafo = StateGraph(EstadoOrquestador)
    grafo.add_node("supervisor", crear_supervisor(modelo_supervisor))
    grafo.add_node("investigador", investigador or crear_investigador(modelo_especialistas, buscador))
    grafo.add_node("analista", analista or crear_analista(modelo_especialistas))
    grafo.add_node("sintesis", crear_sintesis(modelo_supervisor))

    grafo.add_edge(START, "supervisor")
    grafo.add_conditional_edges("supervisor", rutear)
    grafo.add_edge("investigador", "supervisor")
    grafo.add_edge("analista", "supervisor")
    grafo.add_edge("sintesis", END)
    return grafo.compile()


class Ejecucion(BaseModel):
    """Traza de un pedido: cada decisión del supervisor y cada aporte, en orden."""

    pedido: str
    respuesta_final: str
    recorrido: list[str] = Field(description="Nodos por los que pasó, en orden.")
    decisiones: list[Decision]
    aportes: list[Aporte]
    duracion_ms: float = Field(ge=0)


async def ejecutar(grafo: CompiledStateGraph, pedido: str) -> Ejecucion:
    inicio = time.perf_counter()
    logger.info("[usuario] %s", pedido)
    recorrido: list[str] = []
    final: dict[str, Any] = {}
    async for evento in grafo.astream(
        {"messages": [HumanMessage(pedido)]},
        {"recursion_limit": RECURSION_LIMIT},
        stream_mode=["updates", "values"],
    ):
        modo, datos = evento
        if modo == "updates":
            recorrido.extend(datos.keys())
        else:
            final = datos
    ejecucion = Ejecucion(
        pedido=pedido,
        respuesta_final=final.get("respuesta_final", ""),
        recorrido=recorrido,
        decisiones=final.get("decisiones", []),
        aportes=final.get("aportes", []),
        duracion_ms=(time.perf_counter() - inicio) * 1000,
    )
    logger.info(
        "[orquestador] terminado en %.1f s: %s",
        ejecucion.duracion_ms / 1000,
        " -> ".join(ejecucion.recorrido),
    )
    return ejecucion
