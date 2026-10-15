"""Supervisor y síntesis: el que decide quién trabaja y el que redacta la respuesta final.

El supervisor es un director de tráfico, no un ejecutor: no tiene herramientas
y no responde la pregunta. En cada vuelta lee el pedido y los aportes, evalúa
el último con una rúbrica y devuelve una `Decision` validada por Pydantic:
a quién le toca (`investigador`, `analista` o `sintesis`) y con qué
instrucción. Si un aporte no alcanza, puede devolverle la tarea al mismo
especialista con una corrección.

Condición de parada: después de `MAX_DELEGACIONES` delegaciones, la decisión
pasa a ser `sintesis` sin consultar al modelo. Así un supervisor que nunca
queda conforme no gasta API sin fin.
"""

import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable

from pipeline.chain import ERRORES_REINTENTABLES

from ..state import Aporte, Decision, EstadoOrquestador

logger = logging.getLogger(__name__)

#: Tope de delegaciones a especialistas por pedido (incluye correcciones).
MAX_DELEGACIONES = 6

PROMPT_SUPERVISOR = f"""\
Sos el supervisor de un equipo de soporte de Tambor, una plataforma de cobros (empresa \
ficticia). No ejecutás tareas: decidís quién trabaja y validás lo que entregan.

Especialistas:
- investigador: consulta la base de comercios y cobros y la documentación interna (códigos \
de error, SLA, procedimientos). No calcula.
- analista: hace cálculos y validaciones (conteos, porcentajes, sumas de montos) sobre datos \
que ya trajo el investigador. No consulta fuentes.
- sintesis: redacta la respuesta final con los aportes. Elegila cuando ya esté todo.

Rúbrica para evaluar el último aporte:
1. ¿Responde exactamente la instrucción que se le dio?
2. ¿Los datos son concretos y verificables (IDs de cobro, montos, códigos), sin suposiciones?
3. Si hubo un error o falta algo, ¿se puede corregir con otra instrucción más precisa?
4. Con todos los aportes, ¿se cubren todas las partes del pedido del usuario?

Reglas:
- Los datos los consigue el investigador antes de que el analista calcule.
- Si un aporte no pasa la rúbrica, devolvé la tarea al mismo especialista diciendo qué faltó.
- No le pidas a un especialista algo que no está en su rol.
- Si el pedido no necesita algún especialista, no lo uses.
- No metas en las instrucciones supuestos o definiciones que el usuario no dio (por ejemplo, \
qué estados cuentan como "sin cobrar"): pedile al especialista que use las definiciones de sus \
herramientas y que diga cuál usó.
- Hay un máximo de {MAX_DELEGACIONES} delegaciones; si los aportes ya alcanzan, pasá a sintesis.\
"""

PROMPT_SINTESIS = """\
Redactás la respuesta final para el equipo de Atención a Comercios de Tambor (empresa ficticia), \
a partir de los aportes de los especialistas. Usá solo lo que está en los aportes: no agregues \
datos, montos ni códigos que no aparezcan. Si una parte del pedido no se pudo resolver, decilo. \
Respondé en español, breve y concreto, citando IDs de cobro y códigos de error.\
"""


def _pedido(estado: EstadoOrquestador) -> str:
    return next(str(m.content) for m in estado["messages"] if isinstance(m, HumanMessage))


def _resumen_aportes(aportes: list[Aporte]) -> str:
    if not aportes:
        return "(todavía no hay aportes)"
    return "\n\n".join(
        f"#{i} [{a.agente}]{' [ERROR]' if a.error else ''} instrucción: {a.instruccion}\n"
        f"herramientas: {', '.join(a.herramientas) or 'ninguna'}\nresultado: {a.contenido}"
        for i, a in enumerate(aportes, start=1)
    )


def crear_supervisor(modelo: BaseChatModel) -> Any:  # noqa: ANN401 - nodo async del grafo
    decisor: Runnable = modelo.with_structured_output(Decision).with_retry(
        retry_if_exception_type=ERRORES_REINTENTABLES, stop_after_attempt=3
    )

    async def supervisor(estado: EstadoOrquestador) -> dict[str, Any]:
        aportes = estado.get("aportes", [])
        if len(aportes) >= MAX_DELEGACIONES:
            decision = Decision(
                evaluacion="Se llegó al máximo de delegaciones.",
                siguiente="sintesis",
                motivo=f"Límite de {MAX_DELEGACIONES} delegaciones: se sintetiza con lo que hay.",
            )
        else:
            decision = await decisor.ainvoke(
                [
                    SystemMessage(PROMPT_SUPERVISOR),
                    HumanMessage(
                        f"PEDIDO DEL USUARIO:\n{_pedido(estado)}\n\nAPORTES HASTA AHORA:\n"
                        f"{_resumen_aportes(aportes)}\n\n¿Quién interviene ahora?"
                    ),
                ]
            )
        logger.info(
            "[supervisor] evalúa: %s | -> %s: %s (%s)",
            " ".join(decision.evaluacion.split()),
            decision.siguiente,
            decision.instruccion or "-",
            decision.motivo,
        )
        return {
            "decisiones": [decision],
            "siguiente": decision.siguiente,
            "instruccion": decision.instruccion,
            "messages": [
                AIMessage(f"-> {decision.siguiente}: {decision.instruccion}", name="supervisor")
            ],
        }

    return supervisor


def crear_sintesis(modelo: BaseChatModel) -> Any:  # noqa: ANN401 - nodo async del grafo
    redactor = modelo.with_retry(
        retry_if_exception_type=ERRORES_REINTENTABLES, stop_after_attempt=3
    ) | StrOutputParser()

    async def sintesis(estado: EstadoOrquestador) -> dict[str, Any]:
        respuesta = await redactor.ainvoke(
            [
                SystemMessage(PROMPT_SINTESIS),
                HumanMessage(
                    f"PEDIDO:\n{_pedido(estado)}\n\nAPORTES:\n"
                    f"{_resumen_aportes(estado.get('aportes', []))}"
                ),
            ]
        )
        logger.info("[sintesis] responde: %s", " ".join(respuesta.split())[:300])
        return {"respuesta_final": respuesta, "messages": [AIMessage(respuesta, name="sintesis")]}

    return sintesis
