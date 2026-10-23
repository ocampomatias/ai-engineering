"""Human-in-the-loop: los reembolsos no se ejecutan sin que una persona los apruebe.

Qué es "crítico": un pedido que pide reembolsar cobros puntuales (verbo de
reembolso + IDs cob_XXXXX). Es la única acción del sistema con efectos
secundarios: mueve plata. Lo decide `detectar_accion`, con reglas fijas y no
con el LLM, para que un modelo confundido no pueda saltearse la revisión.

    ... -> sintesis -(requiere_aprobacion)-> aprobacion -(rutear_revision)-> reembolso -> END
                    \\-> END                             \\-> END (rechazado)

- `aprobacion` llama a `interrupt()`: el grafo se detiene, el checkpoint queda
  en Redis y el worker marca el trabajo WAITING_APPROVAL con el análisis de la
  síntesis, para que quien revisa decida con los datos a la vista.
- `POST /tasks/{id}/approve` encola la respuesta; el worker reanuda el grafo
  con `Command(resume=...)` en el mismo thread_id.
- `reembolso` valida contra la base (solo cobros aprobados) y registra en Redis
  con HSETNX, así reanudar dos veces no reembolsa dos veces.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from agente.datos import BaseCobros, normalizar

from .jobs import Job

_VERBO_REEMBOLSO = re.compile(r"\b(reembols|reintegr|devolv|devuelv)\w*")
_ID_COBRO = re.compile(r"\bcob_[A-Z0-9]{5}\b")

RegistrarReembolso = Callable[[str, dict[str, Any]], Awaitable[bool]]


class Accion(BaseModel):
    tipo: Literal["reembolso"] = "reembolso"
    cobro_ids: list[str] = Field(min_length=1)


class Revision(BaseModel):
    """La respuesta de la persona que revisa. Es lo que recibe `POST /tasks/{id}/approve`."""

    aprobado: bool
    revisor: str = Field(min_length=1, max_length=80)
    comentario: str = Field(default="", max_length=500)


def detectar_accion(pedido: str) -> Accion | None:
    """Un reembolso si el pedido lo pide para cobros con ID explícito; si no, None."""
    if not _VERBO_REEMBOLSO.search(normalizar(pedido)):
        return None
    ids = list(dict.fromkeys(_ID_COBRO.findall(pedido)))  # sin repetidos, en orden
    return Accion(cobro_ids=ids) if ids else None


def _pedido(estado: dict[str, Any]) -> str:
    return next(str(m.content) for m in estado["messages"] if isinstance(m, HumanMessage))


def requiere_aprobacion(estado: dict[str, Any]) -> Literal["aprobacion", "__end__"]:
    return "aprobacion" if detectar_accion(_pedido(estado)) else END


async def aprobacion(estado: dict[str, Any]) -> dict[str, Any]:
    """El punto de interrupción. Al reanudar, el nodo corre de nuevo e `interrupt` devuelve la revisión."""
    accion = detectar_accion(_pedido(estado))
    assert accion is not None  # la arista solo llega acá si hay acción
    respuesta = interrupt(
        {
            "accion": accion.model_dump(),
            "analisis": estado.get("respuesta_final", ""),
            "pregunta": f"¿Aprobás reembolsar {', '.join(accion.cobro_ids)}?",
        }
    )
    return {"accion": accion, "revision": Revision.model_validate(respuesta)}


def rutear_revision(estado: dict[str, Any]) -> Literal["reembolso", "__end__"]:
    return "reembolso" if estado["revision"].aprobado else END


def crear_nodo_reembolso(registrar: RegistrarReembolso, base: BaseCobros | None = None) -> Any:  # noqa: ANN401
    base = base or BaseCobros()

    async def reembolso(estado: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
        revision: Revision = estado["revision"]
        lineas = []
        for cobro_id in estado["accion"].cobro_ids:
            cobro = await asyncio.to_thread(base.cobro, cobro_id)
            if cobro is None:
                lineas.append(f"{cobro_id}: no existe, no se reembolsa.")
            elif cobro.estado != "aprobado":
                lineas.append(f"{cobro_id}: está {cobro.estado}; solo se reembolsan cobros aprobados.")
            elif await registrar(
                cobro_id,
                {
                    "monto": cobro.monto,
                    "comercio_id": cobro.comercio_id,
                    "job_id": config["configurable"]["thread_id"],
                    "revisor": revision.revisor,
                    "fecha": datetime.now(UTC).isoformat(),
                },
            ):
                lineas.append(f"{cobro_id}: reembolso de {cobro.monto} registrado.")
            else:
                lineas.append(f"{cobro_id}: ya tenía un reembolso registrado, no se duplica.")
        return {"resultado_accion": "\n".join(lineas)}

    return reembolso


# --- Endpoint ----------------------------------------------------------------

router = APIRouter()


@router.post("/tasks/{job_id}/approve", status_code=status.HTTP_202_ACCEPTED)
async def aprobar(job_id: str, revision: Revision, request: Request) -> Job:
    store = request.app.state.store
    job = await store.obtener(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No existe ese trabajo.")
    reanudado = await store.reanudar(job_id, revision.model_dump())
    if reanudado is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"El trabajo no espera aprobación (estado: {job.estado})."
        )
    return reanudado
