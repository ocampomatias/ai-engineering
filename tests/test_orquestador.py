"""Tests del orquestador multi-agente (pre-entrega 6). Sin API keys y sin red.

- El supervisor es un modelo falso cuya salida estructurada sale de un guion
  de `Decision`. La síntesis y los especialistas usan el modelo guionado de
  test_agente.py (con o sin `tool_calls`).
- El estado, el grafo, el ruteo, `create_react_agent` y las herramientas son
  los reales.

    python -m pytest -q tests/test_orquestador.py
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import anthropic
import httpx
import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError
from test_agente import ModeloGuionado, llamada, respuesta

from orquestador import Aporte, Decision, construir_orquestador, ejecutar, rutear
from orquestador.agents import MAX_DELEGACIONES
from orquestador.agents.analyst_agent import (
    Estadisticas,
    ResultadoCalculo,
    calcular,
    calcular_estadisticas,
    estadisticas_cobros,
    evaluar_expresion,
    parsear_monto,
)
from orquestador.agents.especialista import armar_tarea, crear_especialista

# --- Dobles -----------------------------------------------------------------


def decision(siguiente: str, instruccion: str = "", motivo: str = "test") -> Decision:
    return Decision(evaluacion="ok", siguiente=siguiente, instruccion=instruccion, motivo=motivo)


class SupervisorGuionado(BaseChatModel):
    """`with_structured_output` devuelve las decisiones del guion; `invoke` responde la síntesis."""

    decisiones: list[Decision] = []
    vistos: list[str] = []
    respuesta_sintesis: str = "Respuesta final."

    @property
    def _llm_type(self) -> str:
        return "supervisor-guionado"

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        def decidir(mensajes: list[BaseMessage]) -> Decision:
            self.vistos.append(str(mensajes[-1].content))
            return self.decisiones.pop(0) if self.decisiones else decision("sintesis")

        return RunnableLambda(decidir)

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:  # noqa: ANN401
        self.vistos.append(str(messages[-1].content))
        return ChatResult(generations=[ChatGeneration(message=respuesta(self.respuesta_sintesis))])


def estado(**campos: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"messages": [HumanMessage("pedido")], "aportes": [], "decisiones": [], **campos}


# --- Estado ----------------------------------------------------------------


def test_decision_exige_instruccion_para_delegar() -> None:
    with pytest.raises(ValidationError, match="instrucción"):
        Decision(evaluacion="x", siguiente="investigador", instruccion=" ", motivo="x")
    assert decision("sintesis").instruccion == ""


def test_decision_rechaza_destinos_desconocidos() -> None:
    with pytest.raises(ValidationError):
        Decision(evaluacion="x", siguiente="programador", instruccion="y", motivo="x")


def test_aporte_valida_agente_y_contenido() -> None:
    with pytest.raises(ValidationError):
        Aporte(agente="supervisor", instruccion="x", contenido="y", herramientas=[], ms=0)
    with pytest.raises(ValidationError):
        Aporte(agente="analista", instruccion="x", contenido="", herramientas=[], ms=0)


def test_rutear_devuelve_lo_que_decidio_el_supervisor() -> None:
    assert rutear(estado(siguiente="analista")) == "analista"


# --- Analista: herramientas de cómputo ---------------------------------------


def test_parsear_monto() -> None:
    assert parsear_monto("45.990,00 ARS") == (Decimal("45990.00"), "ARS")
    assert parsear_monto("56,00 USD") == (Decimal("56.00"), "USD")
    for malo in ("45990 ARS", "45.990 ARS", "1.2.3,00 ARS", "10,00 pesos"):
        with pytest.raises(ValueError):
            parsear_monto(malo)


def test_estadisticas_cobros() -> None:
    resultado = json.loads(
        estadisticas_cobros.invoke(
            {
                "cobros": [
                    {"id": "cob_7F3K2", "monto": "45.990,00 ARS", "estado": "rechazado", "codigo_error": "TMB-2031"},
                    {"id": "cob_7F1A9", "monto": "12.500,00 ARS", "estado": "en_revision", "codigo_error": "TMB-2012"},
                    {"id": "cob_7E8Q4", "monto": "8.700,00 ARS", "estado": "aprobado"},
                    {"id": "cob_7F0C3", "monto": "56,00 USD", "estado": "rechazado", "codigo_error": "TMB-2004"},
                ]
            }
        )
    )
    assert resultado["cobros"] == 4
    assert resultado["tasa_rechazo"] == "50,0 %"
    assert resultado["tasa_no_cobrados"] == "75,0 %"
    assert resultado["montos_por_moneda"]["ARS"]["total"] == "67.190,00 ARS"
    assert resultado["montos_por_moneda"]["USD"] == {"total": "56,00 USD", "rechazado": "56,00 USD"}
    assert resultado["codigos_error"] == {"TMB-2031": 1, "TMB-2012": 1, "TMB-2004": 1}


def test_estadisticas_rechaza_datos_invalidos() -> None:
    with pytest.raises(ValidationError):
        estadisticas_cobros.invoke({"cobros": [{"id": "cob_7F3K2", "monto": "45990", "estado": "rechazado"}]})
    repetido = {"id": "cob_7F3K2", "monto": "1,00 ARS", "estado": "aprobado"}
    with pytest.raises(ValidationError, match="repetidos"):
        estadisticas_cobros.invoke({"cobros": [repetido, repetido]})


def test_calcular_sin_eval() -> None:
    assert evaluar_expresion("(45990 + 12500) * 0.1") == Decimal("5849.0")
    assert evaluar_expresion("-3 + 10 / 4") == Decimal("-0.5")
    assert json.loads(calcular.invoke({"expresion": "5800 * 1"}))["resultado"] == "5800"
    for peligrosa in ("__import__('os').system('dir')", "2 ** 999999", "abs(-1)", "x + 1", "1 / 0"):
        with pytest.raises(ValueError):
            evaluar_expresion(peligrosa)


def test_calcular_devuelve_el_error_al_modelo() -> None:
    calcular.handle_tool_error = True
    assert "no permitido" in calcular.invoke({"expresion": "abs(-1)"})


def test_las_salidas_del_analista_validan_con_su_modelo() -> None:
    cobros = [
        {"id": "cob_7F3K2", "monto": "45.990,00 ARS", "estado": "rechazado", "codigo_error": "TMB-2031"},
        {"id": "cob_7E8Q4", "monto": "8.700,00 ARS", "estado": "aprobado"},
    ]
    estadisticas = Estadisticas.model_validate_json(estadisticas_cobros.invoke({"cobros": cobros}))
    assert estadisticas.por_estado == {"rechazado": 1, "aprobado": 1}
    resultado = ResultadoCalculo.model_validate_json(calcular.invoke({"expresion": "1 + 1"}))
    assert resultado.resultado == "2"
    for modelo in (Estadisticas, ResultadoCalculo):
        assert all(c.description for c in modelo.model_fields.values()), modelo.__name__


# --- Especialistas -----------------------------------------------------------


def test_armar_tarea_pasa_solo_lo_necesario() -> None:
    aportes = [
        Aporte(agente="investigador", instruccion="i", contenido="cob_7F3K2 rechazado", herramientas=[], ms=1),
        Aporte(agente="investigador", instruccion="i", contenido="falló", herramientas=[], error=True, ms=1),
        Aporte(agente="analista", instruccion="a", contenido="mi cuenta vieja", herramientas=[], ms=1),
    ]
    tarea = armar_tarea("analista", estado(instruccion="Calculá la tasa", aportes=aportes))
    assert "Calculá la tasa" in tarea and "cob_7F3K2 rechazado" in tarea
    assert "falló" not in tarea  # los aportes con error no se pasan
    assert "mi cuenta vieja" not in tarea  # ni los propios
    assert "pedido" not in tarea  # ni el historial completo


async def test_especialista_usa_herramientas_y_devuelve_aporte() -> None:
    modelo = ModeloGuionado(
        guion=[
            llamada("calcular", expresion="2 + 2"),
            respuesta("El resultado es 4."),
        ]
    )
    from orquestador.agents.analyst_agent import herramientas_analista

    nodo = crear_especialista("analista", modelo, herramientas_analista(), "prompt")
    cambios = await nodo(estado(instruccion="Sumá 2 + 2"))
    aporte = cambios["aportes"][0]
    assert aporte.agente == "analista" and aporte.herramientas == ["calcular"]
    assert aporte.contenido == "El resultado es 4." and not aporte.error
    assert cambios["messages"][0].name == "analista"


async def test_especialista_que_falla_no_rompe_el_grafo() -> None:
    class ModeloRoto(ModeloGuionado):
        def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:  # noqa: ANN401
            raise ConnectionError("sin red")

    nodo = crear_especialista("investigador", ModeloRoto(), [], "prompt")
    aporte = (await nodo(estado(instruccion="Buscá algo")))["aportes"][0]
    assert aporte.error and "ConnectionError" in aporte.contenido


class ModeloQueFalla(ModeloGuionado):
    """Lanza los errores de la lista, uno por llamada, y después sigue el guion."""

    errores: list[Any] = []
    llamadas: int = 0

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:  # noqa: ANN401
        self.llamadas += 1
        if self.errores:
            raise self.errores.pop(0)
        return super()._generate(*args, **kwargs)


_PEDIDO_ANTHROPIC = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


async def test_especialista_reintenta_un_error_transitorio() -> None:
    from orquestador.agents.analyst_agent import herramientas_analista

    modelo = ModeloQueFalla(
        errores=[anthropic.APIConnectionError(request=_PEDIDO_ANTHROPIC)],
        guion=[respuesta("Listo.")],
    )
    nodo = crear_especialista("analista", modelo, herramientas_analista(), "prompt")
    aporte = (await nodo(estado(instruccion="Sumá 2 + 2")))["aportes"][0]
    assert not aporte.error and aporte.contenido == "Listo."
    assert modelo.llamadas == 2


async def test_especialista_no_reintenta_una_key_invalida() -> None:
    from orquestador.agents.analyst_agent import herramientas_analista

    key_invalida = anthropic.AuthenticationError(
        "invalid x-api-key", response=httpx.Response(401, request=_PEDIDO_ANTHROPIC), body=None
    )
    modelo = ModeloQueFalla(errores=[key_invalida], guion=[respuesta("no debería llegar")])
    nodo = crear_especialista("analista", modelo, herramientas_analista(), "prompt")
    aporte = (await nodo(estado(instruccion="Sumá 2 + 2")))["aportes"][0]
    assert aporte.error and "AuthenticationError" in aporte.contenido
    assert modelo.llamadas == 1


def test_el_reembolso_reintenta_solo_caidas_de_redis() -> None:
    from langgraph.checkpoint.memory import InMemorySaver

    from app.graph import REDIS_TRANSITORIO, construir_grafo

    async def registrar(cobro_id: str, datos: dict[str, Any]) -> bool:
        return True

    grafo = construir_grafo(
        InMemorySaver(),
        registrar,
        SupervisorGuionado(),
        investigador=fake_especialista("investigador", "x"),
        analista=fake_especialista("analista", "x"),
    )
    politica = grafo.builder.nodes["reembolso"].retry_policy
    assert politica.max_attempts == 3 and politica.retry_on == REDIS_TRANSITORIO
    assert grafo.builder.nodes["supervisor"].retry_policy is None


# --- Grafo -----------------------------------------------------------------


def fake_especialista(nombre: str, contenido: str) -> Any:  # noqa: ANN401
    async def nodo(e: dict[str, Any]) -> dict[str, Any]:
        return {
            "aportes": [Aporte(agente=nombre, instruccion=e["instruccion"], contenido=contenido, herramientas=["x"], ms=1)]
        }

    return nodo


def test_estructura_del_grafo() -> None:
    grafo = construir_orquestador(SupervisorGuionado(), ModeloGuionado(), buscador="local")
    aristas = {(a.source, a.target, a.conditional) for a in grafo.get_graph().edges}
    assert {("supervisor", d, True) for d in ("investigador", "analista", "sintesis")} <= aristas
    assert ("investigador", "supervisor", False) in aristas
    assert ("analista", "supervisor", False) in aristas
    assert ("sintesis", "__end__", False) in aristas
    assert "supervisor -.-> analista" in grafo.get_graph().draw_mermaid()


async def test_flujo_investigador_analista_sintesis() -> None:
    supervisor = SupervisorGuionado(
        decisiones=[
            decision("investigador", "Traé los cobros de Ferretería López"),
            decision("analista", "Calculá la tasa de rechazo"),
            decision("sintesis"),
        ]
    )
    grafo = construir_orquestador(
        supervisor,
        investigador=fake_especialista("investigador", "5 cobros, 1 rechazado"),
        analista=fake_especialista("analista", "tasa de rechazo 20 %"),
    )
    ejecucion = await ejecutar(grafo, "¿Qué tasa de rechazo tiene Ferretería López?")
    assert ejecucion.recorrido == ["supervisor", "investigador", "supervisor", "analista", "supervisor", "sintesis"]
    assert [a.agente for a in ejecucion.aportes] == ["investigador", "analista"]
    assert [d.siguiente for d in ejecucion.decisiones] == ["investigador", "analista", "sintesis"]
    assert ejecucion.respuesta_final == "Respuesta final."
    # El supervisor vio el aporte del investigador antes de mandar al analista.
    assert "5 cobros, 1 rechazado" in supervisor.vistos[1]
    # La síntesis recibió los dos aportes.
    assert "tasa de rechazo 20 %" in supervisor.vistos[-1]


async def test_supervisor_pide_refinar() -> None:
    supervisor = SupervisorGuionado(
        decisiones=[
            decision("investigador", "Traé los cobros"),
            decision("investigador", "Faltan los códigos de error: buscalos en la documentación"),
            decision("sintesis"),
        ]
    )
    grafo = construir_orquestador(
        supervisor,
        investigador=fake_especialista("investigador", "datos"),
        analista=fake_especialista("analista", "x"),
    )
    ejecucion = await ejecutar(grafo, "pedido")
    assert [a.instruccion for a in ejecucion.aportes] == [
        "Traé los cobros",
        "Faltan los códigos de error: buscalos en la documentación",
    ]


async def test_limite_de_delegaciones_fuerza_la_sintesis() -> None:
    supervisor = SupervisorGuionado(
        decisiones=[decision("investigador", f"Intento {i}") for i in range(20)]
    )
    grafo = construir_orquestador(
        supervisor,
        investigador=fake_especialista("investigador", "nunca alcanza"),
        analista=fake_especialista("analista", "x"),
    )
    ejecucion = await ejecutar(grafo, "pedido imposible")
    assert len(ejecucion.aportes) == MAX_DELEGACIONES
    assert ejecucion.decisiones[-1].siguiente == "sintesis"
    assert "Límite" in ejecucion.decisiones[-1].motivo
    assert ejecucion.recorrido[-1] == "sintesis"


async def test_sin_especialistas_si_no_hacen_falta() -> None:
    grafo = construir_orquestador(
        SupervisorGuionado(decisiones=[decision("sintesis")]),
        investigador=fake_especialista("investigador", "x"),
        analista=fake_especialista("analista", "x"),
    )
    ejecucion = await ejecutar(grafo, "Hola")
    assert ejecucion.recorrido == ["supervisor", "sintesis"] and ejecucion.aportes == []
