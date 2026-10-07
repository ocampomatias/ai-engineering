"""Tests del agente (pre-entrega 5). Sin API keys y sin red.

- El LLM es un chat model falso que devuelve, en orden, los mensajes de un
  guion (con o sin `tool_calls`) y guarda lo que recibió en cada llamada.
- La base de cobros, las herramientas, el ToolNode, el grafo y el checkpointer
  SQLite (en un archivo temporal) son los reales.
- La documentación se busca con el BM25 local sobre `corpus/`.

    python -m pytest -q tests/test_agente.py
"""

from __future__ import annotations

import json
import sqlite3
from itertools import count
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from agente import (
    PROMPT_SISTEMA,
    BaseCobros,
    BuscadorLocal,
    abrir_agente,
    construir_grafo,
    crear_herramientas,
)
from agente.datos import normalizar

# --- Dobles -----------------------------------------------------------------

_ids = count(1)


def llamada(nombre: str, /, **argumentos: Any) -> AIMessage:  # noqa: ANN401
    """Un mensaje del modelo que pide una herramienta."""
    return AIMessage(
        content="", tool_calls=[{"name": nombre, "args": argumentos, "id": f"call_{next(_ids)}"}]
    )


def respuesta(texto: str) -> AIMessage:
    return AIMessage(
        content=texto, usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
    )


class ModeloGuionado(BaseChatModel):
    """Devuelve los mensajes del guion en orden. Con `repetir`, pide esa herramienta para siempre."""

    guion: list[AIMessage] = []
    repetir: str | None = None
    recibidos: list[list[BaseMessage]] = []
    herramientas: list[str] = []

    @property
    def _llm_type(self) -> str:
        return "guionado"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ModeloGuionado:  # noqa: ANN401
        self.herramientas = [t.name for t in tools]
        return self

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:  # noqa: ANN401
        self.recibidos.append(list(messages))
        if self.repetir:
            mensaje = llamada(self.repetir, nombre="López")
        elif self.guion:
            mensaje = self.guion.pop(0)
        else:
            mensaje = respuesta("Fin del guion.")
        return ChatResult(generations=[ChatGeneration(message=mensaje)])


class BuscadorQueFalla:
    nombre = "roto"

    def buscar(self, consulta: str, k: int) -> list[Any]:
        raise ConnectionError("sin red")


@pytest.fixture(scope="module")
def base() -> BaseCobros:
    return BaseCobros()


@pytest.fixture(scope="module")
def herramientas(base: BaseCobros) -> dict[str, Any]:
    return {h.name: h for h in crear_herramientas(base, BuscadorLocal())}


async def ejecutar(herramientas: dict[str, Any], nombre: str, /, **argumentos: Any) -> ToolMessage:  # noqa: ANN401
    """Ejecuta una herramienta como lo hace el grafo: a través del ToolNode.

    El ToolNode necesita el runtime de LangGraph, así que va adentro de un grafo de un nodo.
    """
    grafo = StateGraph(MessagesState)
    grafo.add_node("herramientas", ToolNode(list(herramientas.values())))
    grafo.add_edge(START, "herramientas")
    resultado = await grafo.compile().ainvoke({"messages": [llamada(nombre, **argumentos)]})
    return resultado["messages"][-1]


# --- Base de datos ---------------------------------------------------------


def test_normalizar() -> None:
    assert normalizar("  Ferretería LÓPEZ ") == "ferreteria lopez"


def test_buscar_comercios_sin_tildes_y_por_palabras(base: BaseCobros) -> None:
    assert [c.id for c in base.buscar_comercios("lopez")] == [102, 104]
    assert [c.id for c in base.buscar_comercios("ferreteria LOPEZ")] == [102]
    assert [c.id for c in base.buscar_comercios("lopez farmacia")] == [104]
    assert base.buscar_comercios("   ") == []


def test_listar_cobros_ordena_filtra_y_limita(base: BaseCobros) -> None:
    cobros = base.listar_cobros(102)
    assert [c.fecha for c in cobros] == sorted((c.fecha for c in cobros), reverse=True)
    assert cobros[0].id == "cob_7F3K2" and cobros[0].monto == "45.990,00 ARS"
    assert [c.id for c in base.listar_cobros(102, "rechazado")] == ["cob_7F3K2"]
    assert len(base.listar_cobros(102, limite=2)) == 2


def test_cobro_inexistente(base: BaseCobros) -> None:
    assert base.cobro("cob_00000") is None
    assert base.comercio(999) is None


def test_la_entrada_nunca_es_sql(base: BaseCobros) -> None:
    assert base.buscar_comercios("'; DROP TABLE comercios; --") == []
    assert base.cobro("x' OR '1'='1") is None
    assert len(base.nombres_comercios()) == 5


# --- Herramientas ----------------------------------------------------------


def test_herramientas_tienen_descripcion_y_esquema(herramientas: dict[str, Any]) -> None:
    assert set(herramientas) == {
        "buscar_comercio", "listar_cobros", "consultar_cobro", "buscar_documentacion"
    }
    for h in herramientas.values():
        assert len(h.description) > 100, h.name
        assert h.args_schema is not None


async def test_buscar_comercio_ambiguo_avisa(herramientas: dict[str, Any]) -> None:
    resultado = await ejecutar(herramientas, "buscar_comercio", nombre="López")
    datos = json.loads(resultado.content)
    assert [c["id"] for c in datos["comercios"]] == [102, 104]
    assert "preguntale" in datos["aviso"]


async def test_buscar_comercio_inexistente_sugiere(herramientas: dict[str, Any]) -> None:
    resultado = await ejecutar(herramientas, "buscar_comercio", nombre="Libreria Andinna")
    assert resultado.status == "error"
    assert "Librería Andina" in resultado.content


async def test_listar_cobros_de_comercio_inexistente(herramientas: dict[str, Any]) -> None:
    resultado = await ejecutar(herramientas, "listar_cobros", comercio_id=999)
    assert resultado.status == "error"
    assert "buscar_comercio" in resultado.content


async def test_listar_cobros_sin_resultados(herramientas: dict[str, Any]) -> None:
    resultado = await ejecutar(herramientas, "listar_cobros", comercio_id=105, estado="rechazado")
    datos = json.loads(resultado.content)
    assert datos["cobros"] == [] and "aviso" in datos


async def test_argumentos_invalidos_vuelven_como_error(herramientas: dict[str, Any]) -> None:
    resultado = await ejecutar(herramientas, "consultar_cobro", cobro_id="7F3K2")
    assert resultado.status == "error"
    assert "cobro_id" in resultado.content
    resultado = await ejecutar(herramientas, "listar_cobros", comercio_id=102, estado="fallido")
    assert resultado.status == "error"


async def test_consultar_cobro(herramientas: dict[str, Any]) -> None:
    datos = json.loads((await ejecutar(herramientas, "consultar_cobro", cobro_id="cob_7F3K2")).content)
    assert datos["codigo_error"] == "TMB-2031" and datos["comercio"] == "Ferretería López"
    assert datos["intentos"] == 3


async def test_buscar_documentacion_local(herramientas: dict[str, Any]) -> None:
    resultado = await ejecutar(herramientas, "buscar_documentacion", consulta="TMB-2031", k=2)
    fragmentos = json.loads(resultado.content)["fragmentos"]
    assert fragmentos[0]["seccion"] == "TMB-2031"
    assert "3-D Secure" in fragmentos[0]["texto"]


async def test_buscar_documentacion_caida_no_corta_el_grafo(base: BaseCobros) -> None:
    herramientas = {h.name: h for h in crear_herramientas(base, BuscadorQueFalla())}
    resultado = await ejecutar(herramientas, "buscar_documentacion", consulta="TMB-2031")
    assert resultado.status == "error"
    assert "no está disponible" in resultado.content


# --- Grafo -----------------------------------------------------------------


def test_estructura_del_grafo(base: BaseCobros) -> None:
    grafo = construir_grafo(ModeloGuionado(), crear_herramientas(base, BuscadorLocal()))
    estructura = grafo.get_graph()
    assert {"agente", "herramientas"} <= set(estructura.nodes)
    aristas = {(a.source, a.target, a.conditional) for a in estructura.edges}
    assert ("__start__", "agente", False) in aristas
    assert ("herramientas", "agente", False) in aristas  # el ciclo
    assert ("agente", "herramientas", True) in aristas  # tools_condition
    assert ("agente", END, True) in aristas


@pytest.fixture
def memoria(tmp_path: Path) -> Path:
    return tmp_path / "memoria.sqlite"


async def test_ciclo_react_multipaso(memoria: Path) -> None:
    modelo = ModeloGuionado(
        guion=[
            llamada("buscar_comercio", nombre="Ferretería López"),
            llamada("listar_cobros", comercio_id=102, limite=1),
            llamada("buscar_documentacion", consulta="TMB-2031"),
            respuesta("El cobro cob_7F3K2 falló por 3-D Secure (TMB-2031)."),
        ]
    )
    async with abrir_agente(memoria, modelo=modelo, buscador="local") as agente:
        turno = await agente.preguntar("¿Por qué falló el último cobro de Ferretería López?", "t1")

    assert [p.herramienta for p in turno.pasos if p.tipo == "llamada"] == [
        "buscar_comercio", "listar_cobros", "buscar_documentacion"
    ]
    assert [p.tipo for p in turno.pasos] == ["llamada", "resultado"] * 3 + ["respuesta"]
    assert turno.llamadas_herramientas == 3
    assert turno.respuesta.startswith("El cobro cob_7F3K2")
    assert turno.mensajes_en_memoria == 8  # pregunta + 3 x (llamada, resultado) + respuesta
    assert turno.tokens_entrada == 100 and not turno.cortado_por_limite
    # El modelo recibe el prompt de sistema y, en la 4.a llamada, los 3 resultados.
    assert modelo.recibidos[0][0].content == PROMPT_SISTEMA
    assert sum(isinstance(m, ToolMessage) for m in modelo.recibidos[3]) == 3
    assert modelo.herramientas == [
        "buscar_comercio", "listar_cobros", "consultar_cobro", "buscar_documentacion"
    ]


async def test_sin_herramientas_termina_directo(memoria: Path) -> None:
    modelo = ModeloGuionado(guion=[respuesta("¡Hola! Puedo ayudarte con cobros.")])
    async with abrir_agente(memoria, modelo=modelo, buscador="local") as agente:
        turno = await agente.preguntar("Hola", "t1")
    assert turno.llamadas_herramientas == 0
    assert [p.tipo for p in turno.pasos] == ["respuesta"]


async def test_error_de_herramienta_y_segundo_intento(memoria: Path) -> None:
    modelo = ModeloGuionado(
        guion=[
            llamada("buscar_comercio", nombre="Libreria Andinna"),
            llamada("buscar_comercio", nombre="Librería Andina"),
            respuesta("Es la Librería Andina, ID 101."),
        ]
    )
    async with abrir_agente(memoria, modelo=modelo, buscador="local") as agente:
        turno = await agente.preguntar("¿Qué ID tiene la Libreria Andinna?", "t1")
    resultados = [p for p in turno.pasos if p.tipo == "resultado"]
    assert [r.error for r in resultados] == [True, False]
    # El modelo vio el error (con la sugerencia) antes del segundo intento.
    error_visto = [m for m in modelo.recibidos[1] if isinstance(m, ToolMessage)][-1]
    assert "Librería Andina" in error_visto.content


async def test_memoria_persiste_entre_procesos(memoria: Path) -> None:
    async with abrir_agente(
        memoria, modelo=ModeloGuionado(guion=[respuesta("Anotado: comercio 102.")]), buscador="local"
    ) as agente:
        await agente.preguntar("Estoy revisando el comercio 102.", "caso")

    # "Otro proceso": se abre de nuevo el archivo, con un agente nuevo.
    modelo = ModeloGuionado(guion=[respuesta("Seguimos con el comercio 102.")])
    async with abrir_agente(memoria, modelo=modelo, buscador="local") as agente:
        turno = await agente.preguntar("¿De qué comercio estábamos hablando?", "caso")
        otro = await agente.preguntar("¿De qué comercio estábamos hablando?", "otro-thread")

    contenidos = [m.content for m in modelo.recibidos[0]]
    assert "Estoy revisando el comercio 102." in contenidos
    assert turno.mensajes_en_memoria == 4
    assert otro.mensajes_en_memoria == 2  # otro thread_id, otra memoria
    assert all("102" not in str(m.content) for m in modelo.recibidos[1][1:-1])

    with sqlite3.connect(memoria) as conexion:
        threads = {f[0] for f in conexion.execute("SELECT DISTINCT thread_id FROM checkpoints")}
    assert threads == {"caso", "otro-thread"}


async def test_recursion_limit_corta_y_deja_el_thread_usable(memoria: Path) -> None:
    async with abrir_agente(
        memoria, modelo=ModeloGuionado(repetir="buscar_comercio"), buscador="local", recursion_limit=6
    ) as agente:
        turno = await agente.preguntar("López", "t1")
        assert turno.cortado_por_limite
        assert "límite de pasos" in turno.respuesta

        # Cada llamada a herramienta del historial tiene su resultado: la API lo exige.
        historial = await agente.historial("t1")
        pedidas = {c["id"] for m in historial if isinstance(m, AIMessage) for c in m.tool_calls}
        respondidas = {m.tool_call_id for m in historial if isinstance(m, ToolMessage)}
        assert pedidas == respondidas

    async with abrir_agente(
        memoria, modelo=ModeloGuionado(guion=[respuesta("Ok.")]), buscador="local"
    ) as agente:
        siguiente = await agente.preguntar("Probemos de nuevo", "t1")
    assert siguiente.respuesta == "Ok."


async def test_historial_largo_se_recorta_para_el_modelo(memoria: Path) -> None:
    modelo = ModeloGuionado(guion=[respuesta("Ok.")])
    relleno = "palabra " * 1500  # unos 2000 tokens por mensaje
    async with abrir_agente(memoria, modelo=modelo, buscador="local") as agente:
        await agente.grafo.aupdate_state(
            agente._config("t1"),
            {"messages": [m for i in range(6) for m in (HumanMessage(f"{i} {relleno}"), AIMessage(relleno))]},
            as_node="agente",
        )
        turno = await agente.preguntar("¿Seguís ahí?", "t1")

    enviados = modelo.recibidos[0]
    assert turno.mensajes_en_memoria == 14  # el estado guarda todo
    assert len(enviados) < 14  # al modelo le llega lo último
    assert isinstance(enviados[1], HumanMessage)  # después del sistema, arranca en el usuario
    assert enviados[-1].content == "¿Seguís ahí?"
