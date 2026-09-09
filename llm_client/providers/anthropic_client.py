"""Cliente asíncrono de Anthropic."""

import logging
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import anthropic
from anthropic import AsyncAnthropic
from pydantic import SecretStr

from .. import errors
from ..base import BaseLLMClient
from ..schemas import (
    ChatMessage,
    ErrorInfo,
    ModelConfig,
    ModelResponse,
    Provider,
    Role,
    StreamChunk,
    TokenUsage,
)

logger = logging.getLogger(__name__)


def translate_anthropic_error(exc: Exception) -> errors.LLMError:
    """Traduce una excepción del SDK de Anthropic a la jerarquía propia."""
    nombre = Provider.ANTHROPIC.value

    if isinstance(exc, anthropic.AuthenticationError):
        return errors.AuthenticationError(
            "API key de Anthropic inválida o ausente", provider=nombre
        )
    if isinstance(exc, anthropic.PermissionDeniedError):
        return errors.AuthenticationError(
            "la API key no tiene permisos para este recurso", provider=nombre
        )
    if isinstance(exc, anthropic.NotFoundError):
        return errors.ModelNotFoundError(
            f"modelo o endpoint inexistente: {exc}", provider=nombre
        )
    if isinstance(exc, anthropic.RateLimitError):
        return errors.RateLimitError(
            "limite de tasa o cuota agotada en Anthropic",
            provider=nombre,
            retry_after=_retry_after_de(exc),
        )
    if isinstance(exc, anthropic.BadRequestError | anthropic.UnprocessableEntityError):
        return errors.InvalidRequestError(str(exc), provider=nombre)
    if isinstance(exc, anthropic.APITimeoutError):
        return errors.LLMTimeoutError("timeout de la API de Anthropic", provider=nombre)
    if isinstance(exc, anthropic.APIConnectionError):
        return errors.NetworkError(f"fallo de conexion con Anthropic: {exc}", provider=nombre)
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= 500:
            return errors.ProviderServerError(
                f"error interno de Anthropic ({exc.status_code})", provider=nombre
            )
        return errors.InvalidRequestError(
            f"Anthropic respondio {exc.status_code}: {exc}", provider=nombre
        )
    return errors.UnexpectedError(f"error no clasificado: {exc!r}", provider=nombre)


def _retry_after_de(exc: Exception) -> float | None:
    respuesta = getattr(exc, "response", None)
    if respuesta is None:
        return None
    crudo = getattr(respuesta, "headers", {}).get("retry-after")
    if not crudo:
        return None
    try:
        return float(crudo)
    except (TypeError, ValueError):
        return None


class AnthropicClient(BaseLLMClient):
    """Implementación de `BaseLLMClient` sobre `AsyncAnthropic`.

    Diferencias con OpenAI que esta clase absorbe:

    * El system prompt es un parámetro propio (`system=`), no un mensaje con
      `role="system"` dentro de la lista.
    * La respuesta viene como una lista de bloques de contenido; hay que
      quedarse con los de tipo `text` (un modelo con razonamiento también emite
      bloques `thinking`).
    * Los modelos actuales quitaron los parámetros de sampling: el SDK ya no
      expone `temperature` ni `top_p`. Si el llamador los define, se ignoran y
      se avisa por log, en vez de que la API devuelva un 400.
    * `max_tokens` es obligatorio.
    """

    provider = Provider.ANTHROPIC

    def __init__(self, api_key: SecretStr | None, *, timeout: float = 60.0) -> None:
        if api_key is None or not api_key.get_secret_value():
            raise errors.ConfigurationError(
                "falta ANTHROPIC_API_KEY en el entorno", provider=Provider.ANTHROPIC.value
            )
        self._client = AsyncAnthropic(
            api_key=api_key.get_secret_value(),
            timeout=timeout,
            max_retries=0,
        )

    # -- Traducción de formato ---------------------------------------------

    def _build_payload(
        self,
        messages: Sequence[ChatMessage],
        config: ModelConfig,
    ) -> dict[str, Any]:
        # Un `role="system"` suelto en la lista de mensajes rompe la API de
        # Anthropic: se extrae y se manda por el parámetro `system`.
        del_sistema = [m.content for m in messages if m.role is Role.SYSTEM]
        conversacion = [
            {"role": m.role.value, "content": m.content}
            for m in messages
            if m.role is not Role.SYSTEM
        ]

        partes_system = ([config.system_prompt] if config.system_prompt else []) + del_sistema

        if config.temperature is not None or config.top_p is not None:
            logger.debug(
                "Anthropic ignora temperature/top_p: los modelos actuales quitaron "
                "los parametros de sampling. Valores recibidos: temperature=%s, top_p=%s",
                config.temperature,
                config.top_p,
            )

        payload: dict[str, Any] = {
            "model": config.model,
            "messages": conversacion,
            "max_tokens": config.max_tokens,
        }
        if partes_system:
            payload["system"] = "\n\n".join(partes_system)
        return payload

    @staticmethod
    def _extraer_texto(bloques: Sequence[Any]) -> str:
        """Concatena solo los bloques de tipo `text` de la respuesta."""
        return "".join(
            bloque.text for bloque in bloques if getattr(bloque, "type", None) == "text"
        )

    # -- Generación --------------------------------------------------------

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        config: ModelConfig,
    ) -> ModelResponse:
        inicio = time.perf_counter()
        try:
            respuesta = await self._client.messages.create(
                **self._build_payload(messages, config)
            )
        except Exception as exc:
            raise translate_anthropic_error(exc) from exc

        return ModelResponse(
            provider=self.provider,
            model=respuesta.model,
            text=self._extraer_texto(respuesta.content),
            finish_reason=respuesta.stop_reason,
            usage=TokenUsage(
                input_tokens=respuesta.usage.input_tokens or 0,
                output_tokens=respuesta.usage.output_tokens or 0,
            ),
            latency_ms=(time.perf_counter() - inicio) * 1000,
        )

    # -- Streaming ---------------------------------------------------------

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        config: ModelConfig,
    ) -> AsyncIterator[StreamChunk]:
        """Recorre `stream.text_stream` con `async for` y emite cada fragmento.

        `text_stream` entrega únicamente los deltas de texto: los bloques de
        razonamiento quedan fuera, que es lo que se quiere mostrar al usuario.
        """
        payload = self._build_payload(messages, config)
        uso = TokenUsage()
        try:
            async with self._client.messages.stream(**payload) as stream:
                async for texto in stream.text_stream:
                    yield StreamChunk(
                        type="delta",
                        delta=texto,
                        provider=self.provider,
                        model=config.model,
                    )
                final = await stream.get_final_message()
                uso = TokenUsage(
                    input_tokens=final.usage.input_tokens or 0,
                    output_tokens=final.usage.output_tokens or 0,
                )
        except Exception as exc:
            traducido = translate_anthropic_error(exc)
            logger.warning("streaming de Anthropic interrumpido: %s", traducido)
            yield StreamChunk(
                type="error",
                provider=self.provider,
                model=config.model,
                error=ErrorInfo(
                    type=type(traducido).__name__,
                    message=traducido.message,
                    provider=self.provider.value,
                    retryable=traducido.retryable,
                ),
            )
            return

        yield StreamChunk(
            type="done",
            provider=self.provider,
            model=config.model,
            usage=uso,
        )

    async def list_models(self) -> list[str]:
        """IDs de modelos habilitados para esta API key."""
        try:
            pagina = await self._client.models.list()
            return sorted(modelo.id for modelo in pagina.data)
        except Exception as exc:
            raise translate_anthropic_error(exc) from exc

    async def aclose(self) -> None:
        await self._client.close()
