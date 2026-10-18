"""Workers: toman trabajos de la cola de Redis, corren el grafo y actualizan el estado.

    PENDING -> RUNNING -> DONE
                       -> WAITING_APPROVAL -(POST /approve)-> PENDING -> RUNNING -> DONE
                       -> FAILED (excepción o timeout)

Corren como tareas de asyncio en el mismo proceso que la API. Como la cola y el
estado viven en Redis, pasar a un proceso aparte (o a Arq/Celery) no cambia
nada de lo de abajo.

Si el grafo falla, el trabajo pasa a FAILED con el error. Sin eso, el cliente
quedaría consultando para siempre un trabajo en RUNNING.
"""

import asyncio
import logging
import os
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from openinference.instrumentation import using_session

from orquestador import RECURSION_LIMIT

from .jobs import EstadoJob, Job, JobStore
from .observability import tracer

logger = logging.getLogger(__name__)

#: Tope de duración de cada ejecución del grafo; pasado eso, FAILED.
TIMEOUT_JOB_S = float(os.getenv("JOB_TIMEOUT_S", "300"))
#: RECURSION_LIMIT de la pre-entrega 6 más los dos nodos de aprobación y reembolso.
LIMITE_PASOS = RECURSION_LIMIT + 2


def _resultado(final: dict[str, Any]) -> dict[str, Any]:
    return {
        "respuesta_final": final.get("respuesta_final", ""),
        "resultado_accion": final.get("resultado_accion"),
        "revision": final["revision"].model_dump() if final.get("revision") else None,
        "decisiones": [d.model_dump() for d in final.get("decisiones", [])],
        "aportes": [a.model_dump() for a in final.get("aportes", [])],
    }


async def _ejecutar(grafo: CompiledStateGraph, job: Job, mensaje: dict[str, Any]) -> dict[str, Any]:
    """Corre (o reanuda) el grafo y devuelve los campos del trabajo a actualizar."""
    reanuda = "resume" in mensaje
    entrada = Command(resume=mensaje["resume"]) if reanuda else {"messages": [HumanMessage(job.pedido)]}
    config = {"configurable": {"thread_id": job.id}, "recursion_limit": LIMITE_PASOS}
    with tracer.start_as_current_span(
        "job.reanudar" if reanuda else "job", openinference_span_kind="chain"
    ) as span:
        span.set_input(mensaje["resume"] if reanuda else job.pedido)
        span.set_attribute("job.id", job.id)
        final = await asyncio.wait_for(grafo.ainvoke(entrada, config), TIMEOUT_JOB_S)
        if interrupciones := final.get("__interrupt__"):
            span.set_output("WAITING_APPROVAL")
            return {"estado": EstadoJob.WAITING_APPROVAL, "aprobacion": interrupciones[0].value}
        resultado = _resultado(final)
        span.set_output(resultado["respuesta_final"])
        return {"estado": EstadoJob.DONE, "resultado": resultado, "aprobacion": None}


async def procesar(store: JobStore, grafo: CompiledStateGraph, mensaje: dict[str, Any]) -> None:
    job_id = mensaje["job_id"]
    job = await store.obtener(job_id)
    if job is None:  # venció el TTL mientras esperaba en la cola
        logger.warning("[worker] el trabajo %s ya no existe", job_id)
        return
    await store.actualizar(job_id, estado=EstadoJob.RUNNING)
    try:
        with using_session(job_id):
            campos = await _ejecutar(grafo, job, mensaje)
    except Exception as exc:  # cualquier falla, del grafo o después, termina en FAILED y nunca en RUNNING
        logger.exception("[worker] %s falló", job_id)
        error = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        campos = {"estado": EstadoJob.FAILED, "error": error}
    await store.actualizar(job_id, **campos)
    logger.info("[worker] %s -> %s", job_id, campos["estado"])


async def correr_worker(store: JobStore, grafo: CompiledStateGraph, nombre: str) -> None:
    """Loop infinito de un worker. Se cancela al apagar la API."""
    logger.info("[%s] escuchando la cola", nombre)
    while True:
        try:
            mensaje = await store.desencolar()
            if mensaje is not None:
                await procesar(store, grafo, mensaje)
        except asyncio.CancelledError:
            raise
        except Exception:  # Redis caído un momento: no matar al worker
            logger.exception("[%s] error leyendo la cola; reintento en 2 s", nombre)
            await asyncio.sleep(2)
