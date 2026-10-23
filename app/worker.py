"""Workers: toman trabajos de la cola de Redis, corren el grafo y actualizan el estado.

    PENDING -> RUNNING -> DONE
                       -> WAITING_APPROVAL -(POST /approve)-> PENDING -> RUNNING -> DONE
                       -> FAILED (excepción o timeout)

Si la API se cae a mitad de un trabajo, el mensaje sigue en "en proceso" (app/jobs.py),
vuelve a la cola al arrancar y `_ejecutar` retoma desde el último checkpoint.

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
from openinference.instrumentation import using_attributes
from opentelemetry.trace import Status, StatusCode

from orquestador import RECURSION_LIMIT

from .hitl import detectar_accion
from .jobs import EstadoJob, Job, JobStore
from .observability import anotar_firma, tracer

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
    """Corre, reanuda o retoma el grafo y devuelve los campos del trabajo a actualizar.

    Decide el checkpoint del `thread_id`, no el tipo de mensaje. Así una sola regla cubre el
    pedido nuevo, la firma y la recuperación tras una caída:

    - interrupción pendiente y el mensaje trae `resume`: la firma, `Command(resume=...)`;
    - interrupción pendiente sin `resume`: vuelve a esperar la firma, sin llamar al LLM;
    - quedó a mitad de camino (`next`): retoma desde el último paso guardado;
    - terminó pero el estado del trabajo no se guardó: DONE con esos valores, sin LLM;
    - sin checkpoint: arranca de cero con el pedido.
    """
    config = {"configurable": {"thread_id": job.id}, "recursion_limit": LIMITE_PASOS}
    snap = await grafo.aget_state(config)
    resume = mensaje.get("resume")
    if snap.interrupts and resume is None:
        logger.info("[worker] %s sigue esperando la firma", job.id)
        return {"estado": EstadoJob.WAITING_APPROVAL, "aprobacion": job.aprobacion or snap.interrupts[0].value}
    # En Phoenix: sesión = trabajo, etiqueta = tipo de pedido, usuario = quien firmó.
    atributos = {
        "session_id": job.id,
        "tags": ["reembolso" if detectar_accion(job.pedido) else "consulta"],
        "metadata": {"job_id": job.id},
    }
    if snap.interrupts:
        nombre, entrada, entrada_span = "firma", Command(resume=resume), resume
        atributos["user_id"] = resume["revisor"]
    elif snap.next:
        nombre, entrada, entrada_span = "reanudacion", None, {"siguiente": list(snap.next)}
        atributos["tags"].append("recuperado")
        logger.info("[worker] %s reanuda desde el checkpoint (siguiente: %s)", job.id, ", ".join(snap.next))
    elif snap.values:
        logger.info("[worker] %s ya había terminado en el checkpoint: se guarda sin volver a correr", job.id)
        return await _terminado(job, resume, _resultado(snap.values))
    else:
        nombre, entrada, entrada_span = "pedido", {"messages": [HumanMessage(job.pedido)]}, job.pedido

    with using_attributes(**atributos), tracer.start_as_current_span(nombre, openinference_span_kind="chain") as span:
        span.set_input(entrada_span)
        # durability="sync": cada paso queda guardado antes de seguir; al retomar no se paga dos veces un nodo.
        final = await asyncio.wait_for(grafo.ainvoke(entrada, config, durability="sync"), TIMEOUT_JOB_S)
        span.set_status(Status(StatusCode.OK))  # si el grafo falla, el span queda en ERROR solo
        if interrupciones := final.get("__interrupt__"):
            span.set_output("Esperando firma humana")
            # El id del span viaja con la aprobación para anotar ahí la decisión de quien firme.
            span_id = format(span.get_span_context().span_id, "016x")
            return {"estado": EstadoJob.WAITING_APPROVAL, "aprobacion": {**interrupciones[0].value, "span_id": span_id}}
        resultado = _resultado(final)
        span.set_output(resultado["respuesta_final"])
    return await _terminado(job, resume, resultado)


async def _terminado(job: Job, resume: dict[str, Any] | None, resultado: dict[str, Any]) -> dict[str, Any]:
    """DONE; si fue una firma, la decisión queda anotada en el span que la pidió."""
    if resume is not None and job.aprobacion and job.aprobacion.get("span_id"):
        await anotar_firma(job.aprobacion["span_id"], resume)
    return {"estado": EstadoJob.DONE, "resultado": resultado, "aprobacion": None}


#: Estados en los que un mensaje repetido no tiene nada que hacer.
_TERMINADOS = {EstadoJob.DONE, EstadoJob.FAILED}


async def procesar(store: JobStore, grafo: CompiledStateGraph, mensaje: dict[str, Any]) -> None:
    job_id = mensaje["job_id"]
    job = await store.obtener(job_id)
    if job is None:  # venció el TTL mientras esperaba en la cola
        logger.warning("[worker] el trabajo %s ya no existe", job_id)
        return
    # Idempotencia: un mensaje recuperado o duplicado de un trabajo ya resuelto no lo vuelve a correr.
    if job.estado in _TERMINADOS or (job.estado == EstadoJob.WAITING_APPROVAL and "resume" not in mensaje):
        logger.info("[worker] %s ya está en %s: descarto el mensaje repetido", job_id, job.estado)
        return
    await store.actualizar(job_id, estado=EstadoJob.RUNNING)
    try:
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
            item = await store.desencolar()
            if item is not None:
                crudo, mensaje = item
                await procesar(store, grafo, mensaje)
                # Recién con el estado final guardado. Si se cancela antes (apagado), el mensaje
                # queda en "en proceso" y se recupera al arrancar.
                await store.confirmar(crudo)
        except asyncio.CancelledError:
            raise
        except Exception:  # Redis caído un momento: no matar al worker
            logger.exception("[%s] error leyendo la cola; reintento en 2 s", nombre)
            await asyncio.sleep(2)
