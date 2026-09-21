"""Tests del sistema RAG. Sin API keys y sin descargar modelos.

- Los embeddings son una bolsa de palabras con hashing: deterministas, y los
  textos que comparten palabras quedan cerca, que es lo que hace falta para
  probar la recuperación.
- El LLM es un chat model falso que devuelve, en orden, lo que dice el guion.
- ChromaDB es real, persistido en una carpeta temporal.

    python -m pytest -q tests/test_rag.py
"""

import asyncio
import json
import logging
import math
import re
import unicodedata
import zlib
from pathlib import Path
from typing import Any

import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ValidationError

from rag import (
    NO_LO_SE,
    BaseVectorialError,
    ConfigRAG,
    IngestaError,
    Metricas,
    RAGError,
    Referencia,
    RespuestaLLM,
    RespuestaRAG,
    abrir_vectorstore,
    cargar_documentos,
    construir_cadena_rag,
    fragmentar,
    get_rag_response,
    ingestar,
    limpiar_texto,
)
from rag.ingesta import id_de_fragmento

DOCUMENTOS = {
    "despliegues.md": (
        "# Despliegues\n\n## Ventanas\n\nLos viernes se despliega solo entre las 10:00 y las "
        "15:00. Fuera de ese horario no se despliega a producción.\n\n## Canary\n\nEl canary "
        "arranca con el 5 % del tráfico durante 30 minutos."
    ),
    "secretos.txt": (
        "Los secretos se rotan cada 90 días de forma automática. Si un secreto se expone, se rota "
        "en el momento."
    ),
}


class EmbeddingsDePrueba(Embeddings):
    """Bolsa de palabras en 256 dimensiones, normalizada."""

    def _vector(self, texto: str) -> list[float]:
        texto = unicodedata.normalize("NFKD", texto.lower())
        vector = [0.0] * 256
        for palabra in re.findall(r"[a-z0-9]{3,}", texto.encode("ascii", "ignore").decode()):
            vector[zlib.crc32(palabra.encode()) % 256] += 1.0
        norma = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norma for v in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class ModeloFalso(BaseChatModel):
    """Chat model que devuelve, en orden, lo que dice el guion, y guarda lo que recibió."""

    guion: list[Any]
    llamadas: int = 0
    recibidos: list[Any] = []

    @property
    def _llm_type(self) -> str:
        return "falso"

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        self.recibidos.append(messages)
        paso = self.guion[self.llamadas]
        self.llamadas += 1
        if isinstance(paso, BaseException):
            raise paso
        return ChatResult(generations=[ChatGeneration(message=paso)])


def json_msg(datos: dict[str, Any], **metadata: Any) -> AIMessage:
    return AIMessage(
        content=json.dumps(datos, ensure_ascii=False),
        response_metadata=metadata or {"stop_reason": "end_turn"},
    )


ENCONTRADA = {
    "encontrada": True,
    "respuesta": "No: los viernes solo se despliega hasta las 15:00.",
    "fragmentos_citados": ["F1"],
}
NO_ENCONTRADA = {"encontrada": False, "respuesta": NO_LO_SE, "fragmentos_citados": []}


@pytest.fixture
def config(tmp_path: Path) -> ConfigRAG:
    datos = tmp_path / "data"
    datos.mkdir()
    for nombre, texto in DOCUMENTOS.items():
        (datos / nombre).write_text(texto, encoding="utf-8")
    (datos / "ignorado.pdf").write_text("no es texto", encoding="utf-8")
    return ConfigRAG(
        carpeta_datos=datos,
        carpeta_vectorstore=tmp_path / "vectorstore",
        coleccion="prueba",
        modelo_embeddings="bolsa-de-palabras",
    )


@pytest.fixture
def store(config: ConfigRAG):
    ingestar(config, embeddings=EmbeddingsDePrueba())
    return abrir_vectorstore(config, EmbeddingsDePrueba())


def cadena_con(store: Any, *guion: Any, intentos: int = 3) -> tuple[ModeloFalso, Any]:
    modelo = ModeloFalso(guion=list(guion), recibidos=[])
    return modelo, construir_cadena_rag(store, modelo, top_k=3, intentos=intentos, backoff=False)


# --- Configuración ---------------------------------------------------------


def test_config_por_defecto_cumple_la_consigna():
    config = ConfigRAG()
    assert (config.chunk_size, config.chunk_overlap) == (500, 50)
    assert 3 <= config.top_k <= 5
    assert config.carpeta_datos.name == "data"


@pytest.mark.parametrize(
    "cambios",
    [
        {"chunk_size": 400},
        {"chunk_overlap": 20},
        {"chunk_size": 500, "chunk_overlap": 250},
        {"top_k": 8},
    ],
)
def test_config_rechaza_valores_fuera_de_la_consigna(cambios):
    with pytest.raises(ValidationError):
        ConfigRAG(**cambios)


def test_config_normaliza_extensiones():
    assert ConfigRAG(extensiones=("MD", ".txt", "md")).extensiones == (".md", ".txt")


# --- Limpieza y fragmentación ----------------------------------------------


def test_limpieza_normaliza_saltos_espacios_y_control():
    sucio = "\ufeffTítulo  \r\n\r\n\r\n\r\nTexto\u00a0con\x00 ruido\t\n"
    assert limpiar_texto(sucio) == "Título\n\nTexto con ruido"


def test_carga_solo_md_y_txt_y_toma_el_titulo(config):
    documentos = cargar_documentos(config)
    assert [d.metadata["fuente"] for d in documentos] == ["despliegues.md", "secretos.txt"]
    assert documentos[0].metadata["titulo"] == "Despliegues"
    assert documentos[1].metadata["titulo"] == "secretos"


def test_carga_falla_si_no_hay_documentos(tmp_path):
    (tmp_path / "vacia").mkdir()
    with pytest.raises(IngestaError):
        cargar_documentos(ConfigRAG(carpeta_datos=tmp_path / "vacia"))
    with pytest.raises(IngestaError):
        cargar_documentos(ConfigRAG(carpeta_datos=tmp_path / "no-existe"))


def test_fragmentos_respetan_el_tope_de_tokens_y_llevan_seccion(config):
    largo = "\n\n".join(
        f"## Sección {i}\n\n" + " ".join(f"palabra{i}x{j}" for j in range(120)) for i in range(8)
    )
    (config.carpeta_datos / "largo.md").write_text(f"# Largo\n\n{largo}", encoding="utf-8")

    fragmentos = [
        f
        for f in fragmentar(cargar_documentos(config), config)
        if f.metadata["fuente"] == "largo.md"
    ]

    assert len(fragmentos) > 1
    assert all(f.metadata["tokens"] <= config.chunk_size for f in fragmentos)
    assert all(f.metadata["seccion"].startswith(("Sección", "Largo")) for f in fragmentos)
    assert [f.metadata["chunk"] for f in fragmentos] == list(range(len(fragmentos)))


def test_ids_de_fragmento_son_deterministas():
    assert id_de_fragmento("a.md", "hola") == id_de_fragmento("a.md", "hola")
    assert id_de_fragmento("a.md", "hola") != id_de_fragmento("a.md", "chau")
    assert id_de_fragmento("a.md", "hola") != id_de_fragmento("b.md", "hola")


# --- Persistencia en ChromaDB ----------------------------------------------


def test_ingesta_es_idempotente(config):
    primera = ingestar(config, embeddings=EmbeddingsDePrueba())
    segunda = ingestar(config, embeddings=EmbeddingsDePrueba())

    assert primera.agregados == primera.fragmentos > 0
    assert segunda.sin_cambios
    assert segunda.fragmentos == primera.fragmentos


def test_ingesta_reindexa_solo_el_archivo_que_cambio(config):
    ingestar(config, embeddings=EmbeddingsDePrueba())
    (config.carpeta_datos / "secretos.txt").write_text(
        "Los secretos se rotan cada 60 días.", encoding="utf-8"
    )

    resumen = ingestar(config, embeddings=EmbeddingsDePrueba())

    assert (resumen.agregados, resumen.eliminados) == (1, 1)
    guardados = abrir_vectorstore(config, EmbeddingsDePrueba()).get(include=["documents"])
    assert any("60 días" in d for d in guardados["documents"])
    assert not any("90 días" in d for d in guardados["documents"])


def test_ingesta_con_reinicio_reconstruye_la_coleccion(config):
    ingestar(config, embeddings=EmbeddingsDePrueba())
    resumen = ingestar(config, embeddings=EmbeddingsDePrueba(), reiniciar=True)
    assert resumen.reiniciada and resumen.agregados == resumen.fragmentos


def test_consultar_sin_coleccion_es_un_error_claro(config):
    with pytest.raises(BaseVectorialError, match="ingestar.py"):
        abrir_vectorstore(config, EmbeddingsDePrueba())


def test_consultar_con_otro_modelo_de_embeddings_es_un_error(config):
    ingestar(config, embeddings=EmbeddingsDePrueba())
    otro = config.model_copy(update={"modelo_embeddings": "otro-modelo"})
    with pytest.raises(BaseVectorialError, match="otro-modelo"):
        abrir_vectorstore(otro, EmbeddingsDePrueba())


# --- Esquemas --------------------------------------------------------------


def test_respuesta_normaliza_citas():
    r = RespuestaLLM(encontrada=True, respuesta="Sí.", fragmentos_citados=["f1", "[F2]", " F1 "])
    assert r.fragmentos_citados == ["F1", "F2"]
    assert RespuestaLLM(
        encontrada=True, respuesta="Sí.", fragmentos_citados="F1, F3"
    ).fragmentos_citados == [
        "F1",
        "F3",
    ]


@pytest.mark.parametrize(
    "datos",
    [
        {"encontrada": True, "respuesta": "Sí.", "fragmentos_citados": []},
        {"encontrada": True, "respuesta": "No lo sé", "fragmentos_citados": ["F1"]},
        {"encontrada": False, "respuesta": NO_LO_SE, "fragmentos_citados": ["F1"]},
        {"encontrada": False, "respuesta": "Creo que fue en 1996.", "fragmentos_citados": []},
        {"encontrada": True, "respuesta": "Sí.", "fragmentos_citados": ["fragmento uno"]},
        {"encontrada": True, "respuesta": "   ", "fragmentos_citados": ["F1"]},
        {**NO_ENCONTRADA, "confianza": 0.9},
    ],
)
def test_respuesta_rechaza_combinaciones_incoherentes(datos):
    with pytest.raises(ValidationError):
        RespuestaLLM(**datos)


def test_no_lo_se_se_guarda_en_forma_canonica():
    assert RespuestaLLM(encontrada=False, respuesta=" no lo sé ").respuesta == NO_LO_SE


def test_con_contexto_rechaza_citas_a_fragmentos_no_recuperados():
    datos = {"encontrada": True, "respuesta": "Sí.", "fragmentos_citados": ["F1", "F7"]}
    RespuestaLLM.model_validate(datos)  # sin contexto no puede saberlo
    with pytest.raises(ValidationError, match="F7"):
        RespuestaLLM.model_validate(datos, context={"ids_recuperados": {"F1", "F2", "F3"}})


@pytest.mark.parametrize("fuente", ["C:/Users/yo/data/a.md", "../a.md", "a.pdf"])
def test_referencia_solo_acepta_nombres_de_archivo_de_datos(fuente):
    with pytest.raises(ValidationError):
        Referencia(id_fragmento="F1", fuente=fuente, similitud=0.5, extracto="texto")


def test_metricas_rechazan_un_total_menor_que_sus_partes():
    with pytest.raises(ValidationError):
        Metricas(
            recuperacion_ms=100,
            generacion_ms=900,
            total_ms=500,
            intentos_llm=1,
            fragmentos_recuperados=3,
        )


def test_respuesta_rag_exige_referencias_si_se_encontro():
    with pytest.raises(ValidationError):
        RespuestaRAG(pregunta="¿x?", respuesta="Sí.", encontrada=True)
    with pytest.raises(ValidationError):
        RespuestaRAG(pregunta="¿x?", respuesta="Inventado.", encontrada=False)


# --- Cadena y get_rag_response ---------------------------------------------


async def test_respuesta_encontrada_con_referencias_reales(store, caplog):
    modelo, cadena = cadena_con(store, json_msg(ENCONTRADA))
    caplog.set_level(logging.INFO, logger="rag")

    r = await get_rag_response("¿Se puede desplegar un viernes a las 16?", cadena=cadena)

    assert r.encontrada and r.respuesta == ENCONTRADA["respuesta"]
    assert [ref.fuente for ref in r.referencias] == ["despliegues.md"]
    assert r.referencias[0].seccion == "Despliegues"
    assert 0 < r.referencias[0].similitud <= 1
    assert r.metricas is not None and r.metricas.intentos_llm == 1
    assert r.metricas.fragmentos_recuperados == 2  # hay 2 fragmentos en total, top_k=3
    assert r.metricas.total_ms >= r.metricas.recuperacion_ms + r.metricas.generacion_ms - 1
    # Lo que pidió la corrección de la pre-entrega 2: validación y tiempos en el log.
    assert "validación OK" in caplog.text
    assert re.search(r"consulta resuelta en \d+ ms \(recuperación \d+ ms, LLM \d+ ms", caplog.text)


async def test_el_prompt_lleva_los_fragmentos_y_la_pregunta(store):
    modelo, cadena = cadena_con(store, json_msg(NO_ENCONTRADA))
    await get_rag_response("¿Cada cuánto se rotan los secretos?", cadena=cadena)

    sistema, humano = modelo.recibidos[0]
    assert NO_LO_SE in sistema.content and "fragmentos_citados" in sistema.content
    assert "[F1] fuente: secretos.txt" in humano.content  # el más parecido va primero
    assert "90 días" in humano.content
    assert humano.content.rstrip().endswith("PREGUNTA: ¿Cada cuánto se rotan los secretos?")


async def test_pregunta_trampa_devuelve_no_lo_se_sin_referencias(store):
    _, cadena = cadena_con(store, json_msg({**NO_ENCONTRADA, "respuesta": "no lo sé"}))
    r = await get_rag_response("¿Qué CDN usan?", cadena=cadena)
    assert not r.encontrada
    assert r.respuesta == NO_LO_SE
    assert r.referencias == []


@pytest.mark.parametrize(
    "fallo",
    [
        AIMessage(content="Claro, los viernes hasta las 15."),  # texto en vez de JSON
        json_msg({**ENCONTRADA, "fragmentos_citados": ["F9"]}),  # cita inventada
        json_msg({**ENCONTRADA, "fragmentos_citados": []}),  # encontrada sin citas
        json_msg(ENCONTRADA, stop_reason="max_tokens"),  # cortada por tope de tokens
        AIMessage(content='{"encontrada": true, "respuesta": "Los vier'),  # JSON cortado
    ],
)
async def test_reintenta_ante_salida_invalida(store, fallo, caplog):
    modelo, cadena = cadena_con(store, fallo, json_msg(ENCONTRADA))
    caplog.set_level(logging.WARNING, logger="rag")

    r = await get_rag_response("¿Viernes?", cadena=cadena)

    assert r.encontrada
    assert modelo.llamadas == 2 and r.metricas.intentos_llm == 2
    assert "intento 1: salida rechazada" in caplog.text


async def test_reintenta_ante_error_transitorio_de_la_api(store):
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    caida = anthropic.APIConnectionError(request=request)
    modelo, cadena = cadena_con(store, caida, json_msg(ENCONTRADA))
    assert (await get_rag_response("¿Viernes?", cadena=cadena)).encontrada
    assert modelo.llamadas == 2


async def test_error_permanente_no_se_reintenta(store):
    modelo, cadena = cadena_con(store, ValueError("bug"), json_msg(ENCONTRADA))
    with pytest.raises(RAGError) as info:
        await get_rag_response("¿Viernes?", cadena=cadena)
    assert info.value.intentos == 1 and modelo.llamadas == 1


async def test_se_agotan_los_reintentos_con_error_controlado(store):
    basura = AIMessage(content="no es JSON")
    modelo, cadena = cadena_con(store, basura, basura, intentos=2)
    with pytest.raises(RAGError) as info:
        await get_rag_response("¿Viernes?", cadena=cadena)
    assert info.value.intentos == 2
    assert isinstance(info.value.__cause__, Exception)


@pytest.mark.parametrize("pregunta", ["", "   ", "x" * 1001])
async def test_rechaza_preguntas_vacias_o_enormes(store, pregunta):
    _, cadena = cadena_con(store)
    with pytest.raises(ValueError):
        await get_rag_response(pregunta, cadena=cadena)


async def test_consultas_en_paralelo(store):
    _, cadena = cadena_con(store, *[json_msg(NO_ENCONTRADA)] * 4)
    respuestas = await asyncio.gather(
        *(get_rag_response(f"¿Pregunta {i}?", cadena=cadena) for i in range(4))
    )
    assert len(respuestas) == 4 and all(r.respuesta == NO_LO_SE for r in respuestas)


def test_la_cadena_es_asincrona(store):
    _, cadena = cadena_con(store, json_msg(ENCONTRADA))
    with pytest.raises(RuntimeError, match="asíncrona"):
        cadena.invoke({"pregunta": "¿Viernes?"})
