"""El orquestador de la pre-entrega 6, con checkpointer de Redis y el paso de aprobación humana.

    START -> supervisor -(rutear)-> investigador | analista -> supervisor
                        -(rutear)-> sintesis -(requiere_aprobacion)-> aprobacion -> reembolso -> END
                                             \\-> END

Los nodos del supervisor, los especialistas y la síntesis son los mismos de
`orquestador/`. Lo nuevo:

- El checkpointer (en producción, `AsyncRedisSaver`) guarda el estado después
  de cada paso con `thread_id = job_id`. Es lo que permite pausar en
  `aprobacion` y reanudar horas después, en otro proceso.
- Dos nodos al final (`app/hitl.py`): el punto de interrupción y el reembolso.
  El reembolso se reintenta ante una caída momentánea de Redis: es seguro porque
  `HSETNX` no registra dos veces el mismo cobro.
"""

from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.redis.jsonplus_redis import JsonPlusRedisSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy

from agente.documentacion import ModoBuscador
from orquestador import Aporte, Decision, EstadoOrquestador, rutear
from orquestador.agents import crear_analista, crear_investigador, crear_sintesis, crear_supervisor
from pipeline.chain import crear_modelo

from .hitl import (
    Accion,
    RegistrarReembolso,
    Revision,
    aprobacion,
    crear_nodo_reembolso,
    requiere_aprobacion,
    rutear_revision,
)
from .jobs import REDIS_TRANSITORIO


#: Los modelos de Pydantic que viajan en el estado, como los identifica el serializador
#: de Redis (módulo partido en puntos + clase).
TIPOS_DEL_ESTADO = [
    (*clase.__module__.split("."), clase.__name__) for clase in (Aporte, Decision, Accion, Revision)
]


def serializador_redis() -> JsonPlusRedisSerializer:
    """El serializador de AsyncRedisSaver, autorizado a reconstruir los modelos del estado.

    Guarda el estado como JSON. Al leerlo, solo reconstruye las clases de la lista; las
    demás vuelven como el sobre crudo `{"lc": 2, "type": "constructor", ...}`. Sin esto,
    al reanudar después de la aprobación, `Aporte` y `Decision` llegaban como dicts.
    """
    return JsonPlusRedisSerializer(allowed_json_modules=TIPOS_DEL_ESTADO)


class EstadoProduccion(EstadoOrquestador):
    accion: Accion
    revision: Revision
    resultado_accion: str


def construir_grafo(
    checkpointer: BaseCheckpointSaver,
    registrar_reembolso: RegistrarReembolso,
    modelo: BaseChatModel | None = None,
    *,
    investigador: Any = None,  # noqa: ANN401 - nodos inyectables para los tests
    analista: Any = None,  # noqa: ANN401
    buscador: ModoBuscador = "auto",
) -> CompiledStateGraph:
    modelo = modelo or crear_modelo(max_tokens=1024)

    grafo = StateGraph(EstadoProduccion)
    grafo.add_node("supervisor", crear_supervisor(modelo))
    grafo.add_node("investigador", investigador or crear_investigador(modelo, buscador))
    grafo.add_node("analista", analista or crear_analista(modelo))
    grafo.add_node("sintesis", crear_sintesis(modelo))
    grafo.add_node("aprobacion", aprobacion)
    grafo.add_node(
        "reembolso",
        crear_nodo_reembolso(registrar_reembolso),
        retry_policy=RetryPolicy(max_attempts=3, retry_on=REDIS_TRANSITORIO),
    )

    grafo.add_edge(START, "supervisor")
    grafo.add_conditional_edges("supervisor", rutear)
    grafo.add_edge("investigador", "supervisor")
    grafo.add_edge("analista", "supervisor")
    grafo.add_conditional_edges("sintesis", requiere_aprobacion)
    grafo.add_conditional_edges("aprobacion", rutear_revision)
    grafo.add_edge("reembolso", END)
    return grafo.compile(checkpointer=checkpointer)
