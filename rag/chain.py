"""Cadena RAG asíncrona: recuperar de ChromaDB, generar con el LLM y validar con Pydantic.

    {"pregunta"}
      | assign(fragmentos = recuperacion)            búsqueda de similitud en ChromaDB (top_k)
      | assign(contexto = formatear_contexto)        fragmentos -> texto con IDs F1..Fk
      | (
          {salida: prompt | modelo | revisar_corte | PydanticOutputParser,
           fragmentos, pregunta}
          | validar_respuesta                        citas reales + armado de RespuestaRAG
        ).with_retry()

La recuperación queda afuera del reintento: si ChromaDB falla, repetir la
consulta al LLM no lo arregla. Lo que se reintenta es la generación: JSON mal
formado, respuesta cortada por tope de tokens, citas a fragmentos que no se
recuperaron y errores transitorios de la API.
"""

import logging
import time
from functools import cache
from operator import itemgetter
from typing import Any
from uuid import UUID

from langchain_chroma import Chroma
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.runnables import (
    Runnable,
    RunnableLambda,
    RunnableParallel,
    RunnablePassthrough,
)
from pydantic import ValidationError

from pipeline.chain import (
    ERRORES_TRANSITORIOS,
    MOTIVOS_DE_CORTE,
    SalidaIncompletaError,
    crear_modelo,
)

from .config import ConfigRAG
from .ingesta import abrir_vectorstore
from .prompts import crear_prompt, formatear_contexto
from .schemas import (
    FragmentoRecuperado,
    Metricas,
    Referencia,
    RespuestaLLM,
    RespuestaRAG,
)

logger = logging.getLogger(__name__)

#: Lo que dispara un nuevo intento de generación.
ERRORES_REINTENTABLES: tuple[type[BaseException], ...] = (
    OutputParserException,
    ValidationError,
    *ERRORES_TRANSITORIOS,
)

LARGO_MAXIMO_PREGUNTA = 1000

# Nombres de los pasos, para que el observador los reconozca en los callbacks.
PASO_RECUPERACION = "recuperacion"
PASOS_DE_VALIDACION = frozenset({"revisar_corte", "PydanticOutputParser", "validar_respuesta"})


class RAGError(Exception):
    """La generación falló y no quedan reintentos. La causa original va en `__cause__`."""

    def __init__(self, message: str, *, intentos: int) -> None:
        super().__init__(message)
        self.intentos = intentos


# --- Recuperación ----------------------------------------------------------


async def recuperar(store: Chroma, pregunta: str, top_k: int) -> list[FragmentoRecuperado]:
    """Busca los `top_k` fragmentos más parecidos a la pregunta.

    El embedding de la pregunta se calcula con el mismo modelo con que se indexó
    (es el `embedding_function` del store). La búsqueda es asíncrona: LangChain
    la corre en un thread aparte y el event loop queda libre.
    """
    resultados = await store.asimilarity_search_with_relevance_scores(pregunta, k=top_k)
    fragmentos = [
        FragmentoRecuperado(
            id_fragmento=f"F{posicion}",
            chunk_id=documento.id or "",
            fuente=documento.metadata["fuente"],
            seccion=documento.metadata.get("seccion") or None,
            contenido=documento.page_content,
            similitud=similitud,
        )
        for posicion, (documento, similitud) in enumerate(resultados, start=1)
    ]
    logger.info(
        "recuperación: %d fragmento(s): %s",
        len(fragmentos),
        ", ".join(f"{f.id_fragmento}={f.fuente} ({f.similitud:.2f})" for f in fragmentos),
    )
    return fragmentos


# --- Validación ------------------------------------------------------------


def _revisar_corte(mensaje: BaseMessage) -> BaseMessage:
    """Descarta la respuesta si el proveedor la cortó por tope de tokens.

    Un JSON cortado casi siempre falla al parsear, pero no siempre: si faltaba
    solo el final de la respuesta, puede quedar un objeto válido e incompleto.
    """
    metadata = getattr(mensaje, "response_metadata", None) or {}
    motivo = metadata.get("stop_reason") or metadata.get("finish_reason")
    if motivo in MOTIVOS_DE_CORTE:
        raise SalidaIncompletaError(f"la respuesta se cortó por tope de tokens ({motivo})")
    return mensaje


def _validar_respuesta(entrada: dict[str, Any]) -> RespuestaRAG:
    """Revalida la salida contra lo recuperado y arma la respuesta con referencias reales."""
    salida: RespuestaLLM = entrada["salida"]
    por_id = {f.id_fragmento: f for f in entrada["fragmentos"]}

    # Segunda validación, ahora con contexto: el parser no sabe qué fragmentos se
    # recuperaron, así que no puede detectar una cita inventada. Esta sí.
    validada = RespuestaLLM.model_validate(
        salida.model_dump(), context={"ids_recuperados": set(por_id)}
    )
    respuesta = RespuestaRAG(
        pregunta=entrada["pregunta"],
        respuesta=validada.respuesta,
        encontrada=validada.encontrada,
        referencias=[Referencia.desde_fragmento(por_id[i]) for i in validada.fragmentos_citados],
    )
    logger.info(
        "validación OK: encontrada=%s, %d referencia(s)%s",
        respuesta.encontrada,
        len(respuesta.referencias),
        f" ({', '.join(r.id_fragmento for r in respuesta.referencias)})"
        if respuesta.referencias
        else "",
    )
    return respuesta


# --- Cadena ----------------------------------------------------------------


def construir_cadena_rag(
    store: Chroma,
    modelo: BaseChatModel,
    *,
    top_k: int = 4,
    intentos: int = 3,
    backoff: bool = True,
) -> Runnable[dict[str, str], RespuestaRAG]:
    """Arma la cadena LCEL completa. Recibe `{"pregunta": ...}` y devuelve `RespuestaRAG`.

    `intentos` cuenta el primero. `backoff=False` saca la espera entre intentos
    (útil en tests).
    """
    if intentos < 1:
        raise ValueError("intentos tiene que ser al menos 1")

    parser = PydanticOutputParser(pydantic_object=RespuestaLLM)
    prompt = crear_prompt(parser.get_format_instructions())

    async def _arecuperar(entrada: dict[str, Any]) -> list[FragmentoRecuperado]:
        return await recuperar(store, entrada["pregunta"], top_k)

    def _recuperar_sync(entrada: dict[str, Any]) -> list[FragmentoRecuperado]:
        raise RuntimeError("la cadena RAG es asíncrona: usá ainvoke / get_rag_response")

    recuperacion = RunnableLambda(_recuperar_sync, afunc=_arecuperar, name=PASO_RECUPERACION)

    generacion = RunnableParallel(
        salida=prompt | modelo | RunnableLambda(_revisar_corte, name="revisar_corte") | parser,
        fragmentos=itemgetter("fragmentos"),
        pregunta=itemgetter("pregunta"),
    ) | RunnableLambda(_validar_respuesta, name="validar_respuesta")

    generacion_resiliente = generacion.with_retry(
        retry_if_exception_type=ERRORES_REINTENTABLES,
        stop_after_attempt=intentos,
        wait_exponential_jitter=backoff,
        exponential_jitter_params={"initial": 1.0, "max": 10.0},
    )

    return (
        RunnablePassthrough.assign(fragmentos=recuperacion)
        | RunnablePassthrough.assign(
            contexto=RunnableLambda(
                lambda entrada: formatear_contexto(entrada["fragmentos"]),
                name="formatear_contexto",
            )
        )
        | generacion_resiliente
    )


@cache
def cadena_por_defecto() -> Runnable[dict[str, str], RespuestaRAG]:
    """Cadena con la colección de ./vectorstore y el proveedor del `.env`. Se crea una vez."""
    config = ConfigRAG()
    return construir_cadena_rag(abrir_vectorstore(config), crear_modelo(), top_k=config.top_k)


# --- Observabilidad --------------------------------------------------------


def _primera_linea(error: BaseException) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'objeto'}: {e['msg']}" for e in error.errors()
        )
    texto = str(error)
    return texto.splitlines()[0] if texto else type(error).__name__


class ObservadorRAG(BaseCallbackHandler):
    """Mide cada etapa y loguea cada intento de generación.

    `.with_retry()` no avisa cuándo reintenta, pero los callbacks se heredan a
    toda la cadena: este handler ve cada llamada al modelo y cada paso de
    validación que rechaza una salida.
    """

    run_inline = True

    def __init__(self) -> None:
        self.intentos = 0
        self.recuperacion_ms = 0.0
        self.generacion_ms = 0.0
        self.fragmentos = 0
        self._inicios: dict[UUID, float] = {}
        self._nombres: dict[UUID, str] = {}

    def _cerrar(self, run_id: UUID) -> float:
        inicio = self._inicios.pop(run_id, None)
        return 0.0 if inicio is None else (time.perf_counter() - inicio) * 1000

    # Pasos de la cadena

    def on_chain_start(
        self, serialized: dict[str, Any], inputs: Any, *, run_id: UUID, **kwargs: Any
    ) -> None:
        nombre = kwargs.get("name") or ""
        self._nombres[run_id] = nombre
        if nombre == PASO_RECUPERACION:
            self._inicios[run_id] = time.perf_counter()

    def on_chain_end(self, outputs: Any, *, run_id: UUID, **kwargs: Any) -> None:
        if self._nombres.pop(run_id, None) == PASO_RECUPERACION:
            ms = self._cerrar(run_id)
            self.recuperacion_ms += ms
            self.fragmentos = len(outputs) if isinstance(outputs, list) else 0
            logger.info("recuperación terminada en %.0f ms", ms)

    def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        nombre = self._nombres.pop(run_id, None)
        if nombre == PASO_RECUPERACION:
            self.recuperacion_ms += self._cerrar(run_id)
        elif nombre in PASOS_DE_VALIDACION:
            logger.warning(
                "intento %d: salida rechazada en %s: %s",
                self.intentos,
                nombre,
                _primera_linea(error),
            )

    # Llamadas al modelo

    def on_chat_model_start(
        self, serialized: dict[str, Any], messages: Any, *, run_id: UUID, **kwargs: Any
    ) -> None:
        self.intentos += 1
        self._inicios[run_id] = time.perf_counter()
        logger.info("intento %d: llamando al modelo", self.intentos)

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        ms = self._cerrar(run_id)
        self.generacion_ms += ms
        generacion = response.generations[0][0] if response.generations else None
        if not isinstance(generacion, ChatGeneration):
            return
        mensaje = generacion.message
        metadata = mensaje.response_metadata or {}
        uso = getattr(mensaje, "usage_metadata", None) or {}
        logger.info(
            "intento %d: respuesta del modelo en %.0f ms (fin: %s, tokens: %s entrada / %s salida)",
            self.intentos,
            ms,
            metadata.get("stop_reason") or metadata.get("finish_reason"),
            uso.get("input_tokens", "?"),
            uso.get("output_tokens", "?"),
        )

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self.generacion_ms += self._cerrar(run_id)
        reintentable = isinstance(error, ERRORES_TRANSITORIOS)
        logger.warning(
            "intento %d: la API falló (%s: %s)%s",
            self.intentos,
            type(error).__name__,
            _primera_linea(error),
            ", se reintenta" if reintentable else "",
        )


# --- Punto de entrada ------------------------------------------------------


async def get_rag_response(
    query: str,
    *,
    cadena: Runnable[dict[str, str], RespuestaRAG] | None = None,
) -> RespuestaRAG:
    """Responde `query` con la documentación indexada y devuelve la respuesta validada.

    Lanza `ValueError` si la pregunta está vacía o es demasiado larga,
    `BaseVectorialError` si no hay colección para consultar, y `RAGError` si la
    generación falla después de agotar los reintentos.
    """
    pregunta = " ".join(query.split())
    if not pregunta:
        raise ValueError("la pregunta está vacía")
    if len(pregunta) > LARGO_MAXIMO_PREGUNTA:
        raise ValueError(f"la pregunta supera los {LARGO_MAXIMO_PREGUNTA} caracteres")

    cadena = cadena or cadena_por_defecto()
    observador = ObservadorRAG()
    inicio = time.perf_counter()
    logger.info("consulta: %r", pregunta if len(pregunta) <= 100 else pregunta[:97] + "...")

    try:
        respuesta = await cadena.ainvoke(
            {"pregunta": pregunta},
            config={"callbacks": [observador], "run_name": "rag"},
        )
    except Exception as exc:
        logger.error(
            "consulta fallida tras %d intento(s): %s: %s",
            observador.intentos,
            type(exc).__name__,
            _primera_linea(exc),
        )
        raise RAGError(_primera_linea(exc), intentos=observador.intentos) from exc

    metricas = Metricas(
        recuperacion_ms=observador.recuperacion_ms,
        generacion_ms=observador.generacion_ms,
        total_ms=(time.perf_counter() - inicio) * 1000,
        intentos_llm=max(observador.intentos, 1),
        fragmentos_recuperados=observador.fragmentos,
    )
    logger.info(
        "consulta resuelta en %.0f ms (recuperación %.0f ms, LLM %.0f ms, %d intento(s)): %s",
        metricas.total_ms,
        metricas.recuperacion_ms,
        metricas.generacion_ms,
        metricas.intentos_llm,
        "respuesta encontrada" if respuesta.encontrada else "NO está en los documentos",
    )
    return respuesta.model_copy(update={"metricas": metricas})
