"""Cadena LCEL de extracción de entidades técnicas.

    prompt | (modelo.with_structured_output(EntidadesTecnicas) | validar_salida).with_retry()

1. `crear_prompt()` arma los mensajes a partir de `{"texto": ...}`.
2. `with_structured_output(..., include_raw=True)` fuerza al modelo a llenar el
   esquema y devuelve tres cosas: el mensaje crudo, el objeto parseado y el
   error de parseo si lo hubo.
3. `_validar_salida` mira el mensaje crudo antes de confiar en el objeto: si el
   proveedor cortó por tope de tokens, la salida se descarta aunque parezca
   válida. Si no validó, lanza una excepción.
4. `.with_retry()` vuelve a correr los pasos 2 y 3 ante JSON mal formado,
   incompleto o errores transitorios de la API. El prompt queda afuera del
   reintento: si falla, es un error de programación y repetirlo no lo arregla.
"""

import logging
import time
from functools import cache
from typing import Any

import anthropic
import openai
from langchain_anthropic import ChatAnthropic
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from llm_client.errors import ConfigurationError
from llm_client.schemas import Provider
from llm_client.settings import Settings, load_settings

from .prompts import crear_prompt
from .schemas import EntidadesTecnicas

logger = logging.getLogger(__name__)

#: Valores de `stop_reason` (Anthropic) y `finish_reason` (OpenAI) que indican
#: que la respuesta se cortó por tope de tokens.
MOTIVOS_DE_CORTE = frozenset({"max_tokens", "length"})

#: Fallas de la API que se arreglan solas esperando: rate limit, red, timeout, 5xx.
#: Un 401, 404 o 400 no está acá: reintentarlos solo gasta cuota.
ERRORES_TRANSITORIOS: tuple[type[BaseException], ...] = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,  # incluye APITimeoutError
    anthropic.InternalServerError,
    anthropic.OverloadedError,  # 529: no hereda de InternalServerError
    anthropic.ServiceUnavailableError,
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.InternalServerError,
)

#: Todo lo que dispara un reintento: salida que no respeta el esquema, salida
#: cortada, o una falla transitoria de la API.
ERRORES_REINTENTABLES: tuple[type[BaseException], ...] = (
    OutputParserException,
    ValidationError,
    *ERRORES_TRANSITORIOS,
)


class SalidaIncompletaError(OutputParserException):
    """El modelo cortó la respuesta o no devolvió la estructura pedida."""


class ExtraccionError(Exception):
    """La extracción falló y no quedan reintentos. La causa original va en `__cause__`."""

    def __init__(self, message: str, *, intentos: int) -> None:
        super().__init__(message)
        self.intentos = intentos


# --- Modelo ----------------------------------------------------------------


def crear_modelo(
    settings: Settings | None = None,
    provider: Provider | str | None = None,
    *,
    max_tokens: int = 1024,
    timeout: float = 60.0,
) -> BaseChatModel:
    """Crea el chat model de LangChain con la misma configuración del módulo 1.

    Proveedor, API key y modelo salen del `.env` a través de `load_settings()`.
    Los SDKs quedan con `max_retries=0`: la política de reintentos vive en un
    solo lugar, el `.with_retry()` de la cadena.
    """
    settings = settings or load_settings()
    elegido = Provider(provider) if provider is not None else settings.provider

    api_key = settings.key_for(elegido)
    if api_key is None:
        raise ConfigurationError(
            f"falta la API key de {elegido.value} en el entorno", provider=elegido.value
        )

    if elegido is Provider.ANTHROPIC:
        # Sin temperature: anthropic 1.x ya no la acepta (ver README del módulo 1).
        return ChatAnthropic(
            model=settings.model_for(elegido),
            api_key=api_key,
            max_tokens=max_tokens,
            timeout=timeout,
            max_retries=0,
        )
    return ChatOpenAI(
        model=settings.model_for(elegido),
        api_key=api_key,
        max_tokens=max_tokens,
        temperature=0,
        timeout=timeout,
        max_retries=0,
    )


# --- Validación ------------------------------------------------------------


def _validar_salida(resultado: dict[str, Any]) -> EntidadesTecnicas:
    """Decide si la salida estructurada se acepta. Si no, lanza para que la cadena reintente."""
    crudo = resultado["raw"]
    metadata = getattr(crudo, "response_metadata", None) or {}
    motivo = metadata.get("stop_reason") or metadata.get("finish_reason")

    # Primero el corte: un objeto a medias puede pasar la validación por casualidad
    # (por ejemplo, si solo faltaba cerrar el resumen) y no hay que confiar en él.
    if motivo in MOTIVOS_DE_CORTE:
        logger.warning("validación: respuesta cortada por tope de tokens (%s), se descarta", motivo)
        raise SalidaIncompletaError(f"la respuesta se cortó por tope de tokens ({motivo})")

    error = resultado.get("parsing_error")
    if error is not None:
        logger.warning("validación: la salida no cumple el esquema: %s", _primera_linea(error))
        raise OutputParserException(f"la salida no cumple el esquema: {error}") from error

    entidades = resultado.get("parsed")
    if entidades is None:
        logger.warning("validación: el modelo contestó sin llenar la estructura")
        raise SalidaIncompletaError("el modelo no devolvió la estructura pedida")

    logger.info(
        "validación OK: %d tecnología(s), criticidad %s",
        len(entidades.tecnologias),
        entidades.nivel_de_criticidad.value,
    )
    return entidades


def _primera_linea(error: BaseException) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'objeto'}: {e['msg']}" for e in error.errors()
        )
    return str(error).splitlines()[0] if str(error) else type(error).__name__


# --- Cadena ----------------------------------------------------------------


def construir_cadena(
    modelo: BaseChatModel,
    *,
    intentos: int = 3,
    backoff: bool = True,
) -> Runnable[dict[str, str], EntidadesTecnicas]:
    """Arma `prompt | modelo estructurado | validación`, con reintento.

    `intentos` cuenta el primero: con 3 hay un intento y dos reintentos.
    `backoff=False` saca la espera entre intentos (útil en tests).
    """
    if intentos < 1:
        raise ValueError("intentos tiene que ser al menos 1")

    extraccion = modelo.with_structured_output(
        EntidadesTecnicas,
        method="function_calling",
        include_raw=True,
    ) | RunnableLambda(_validar_salida, name="validar_salida")

    extraccion_resiliente = extraccion.with_retry(
        retry_if_exception_type=ERRORES_REINTENTABLES,
        stop_after_attempt=intentos,
        wait_exponential_jitter=backoff,
        exponential_jitter_params={"initial": 1.0, "max": 10.0},
    )

    return crear_prompt() | extraccion_resiliente


@cache
def cadena_por_defecto() -> Runnable[dict[str, str], EntidadesTecnicas]:
    """Cadena con el proveedor del `.env`. Se crea una vez y se reutiliza."""
    return construir_cadena(crear_modelo())


# --- Observabilidad --------------------------------------------------------


class ObservadorDeExtraccion(BaseCallbackHandler):
    """Loguea cada llamada al modelo. Como cada reintento es una llamada nueva, cuenta intentos.

    `.with_retry()` no avisa cuándo reintenta, pero los callbacks de LangChain se
    heredan a toda la cadena, así que este handler ve cada intento.
    """

    run_inline = True

    def __init__(self) -> None:
        self.intentos = 0

    def on_chat_model_start(self, serialized: dict[str, Any], messages: Any, **kwargs: Any) -> None:
        self.intentos += 1
        logger.info("intento %d: llamando al modelo", self.intentos)

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        generacion = response.generations[0][0] if response.generations else None
        if not isinstance(generacion, ChatGeneration):
            return
        mensaje = generacion.message
        metadata = mensaje.response_metadata or {}
        uso = getattr(mensaje, "usage_metadata", None) or {}
        logger.info(
            "intento %d: respuesta recibida (fin: %s, tokens: %s entrada / %s salida)",
            self.intentos,
            metadata.get("stop_reason") or metadata.get("finish_reason"),
            uso.get("input_tokens", "?"),
            uso.get("output_tokens", "?"),
        )

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        reintentable = isinstance(error, ERRORES_TRANSITORIOS)
        logger.warning(
            "intento %d: la API falló (%s: %s)%s",
            self.intentos,
            type(error).__name__,
            _primera_linea(error),
            ", se reintenta" if reintentable else "",
        )


# --- Punto de entrada ------------------------------------------------------


async def process_text(
    text: str,
    *,
    cadena: Runnable[dict[str, str], EntidadesTecnicas] | None = None,
) -> EntidadesTecnicas:
    """Extrae entidades técnicas de `text` y devuelve el objeto validado.

    Lanza `ValueError` si el texto está vacío, y `ExtraccionError` si la cadena
    falla después de agotar los reintentos (o ante un error que no se reintenta).
    """
    texto = text.strip()
    if not texto:
        raise ValueError("el texto de entrada está vacío")

    cadena = cadena or cadena_por_defecto()
    observador = ObservadorDeExtraccion()
    inicio = time.perf_counter()
    logger.info("procesando texto de %d caracteres", len(texto))

    try:
        entidades = await cadena.ainvoke(
            {"texto": texto},
            config={"callbacks": [observador], "run_name": "extraccion_entidades"},
        )
    except Exception as exc:
        logger.error(
            "extracción fallida tras %d intento(s): %s: %s",
            observador.intentos,
            type(exc).__name__,
            _primera_linea(exc),
        )
        raise ExtraccionError(_primera_linea(exc), intentos=observador.intentos) from exc

    logger.info(
        "extracción terminada en %.0f ms, %d intento(s)",
        (time.perf_counter() - inicio) * 1000,
        observador.intentos,
    )
    return entidades
