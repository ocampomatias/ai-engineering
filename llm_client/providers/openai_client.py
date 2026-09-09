"""Cliente asíncrono de OpenAI."""

import logging
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import openai
from openai import AsyncOpenAI
from pydantic import SecretStr

from .. import errors
from ..base import BaseLLMClient
from ..schemas import (
    ChatMessage,
    ErrorInfo,
    ModelConfig,
    ModelResponse,
    Provider,
    StreamChunk,
    TokenUsage,
)

logger = logging.getLogger(__name__)


def translate_openai_error(exc: Exception) -> errors.LLMError:
    """Traduce una excepción del SDK de OpenAI a la jerarquía propia."""
    nombre = Provider.OPENAI.value

    if isinstance(exc, openai.AuthenticationError):
        return errors.AuthenticationError(
            "API key de OpenAI inválida o ausente", provider=nombre
        )
    if isinstance(exc, openai.PermissionDeniedError):
        return errors.AuthenticationError(
            "la API key no tiene permisos para este recurso", provider=nombre
        )
    if isinstance(exc, openai.NotFoundError):
        return errors.ModelNotFoundError(
            f"modelo o endpoint inexistente: {exc}", provider=nombre
        )
    if isinstance(exc, openai.RateLimitError):
        return errors.RateLimitError(
            "limite de tasa o cuota agotada en OpenAI",
            provider=nombre,
            retry_after=_retry_after_de(exc),
        )
    if isinstance(exc, openai.BadRequestError | openai.UnprocessableEntityError):
        return errors.InvalidRequestError(str(exc), provider=nombre)
    if isinstance(exc, openai.APITimeoutError):
        return errors.LLMTimeoutError("timeout de la API de OpenAI", provider=nombre)
    if isinstance(exc, openai.APIConnectionError):
        return errors.NetworkError(f"fallo de conexion con OpenAI: {exc}", provider=nombre)
    if isinstance(exc, openai.APIStatusError):
        if exc.status_code >= 500:
            return errors.ProviderServerError(
                f"error interno de OpenAI ({exc.status_code})", provider=nombre
            )
        return errors.InvalidRequestError(
            f"OpenAI respondio {exc.status_code}: {exc}", provider=nombre
        )
    return errors.UnexpectedError(f"error no clasificado: {exc!r}", provider=nombre)


def _retry_after_de(exc: Exception) -> float | None:
    """Lee el header `retry-after` de la respuesta, si vino."""
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


class OpenAIClient(BaseLLMClient):
    """Implementación de `BaseLLMClient` sobre `AsyncOpenAI`.

    El SDK se instancia con `max_retries=0` a propósito: los reintentos los
    maneja `AsyncLLMManager`, para que exista una sola política de resiliencia,
    visible y testeable.
    """

    provider = Provider.OPENAI

    def __init__(self, api_key: SecretStr | None, *, timeout: float = 60.0) -> None:
        if api_key is None or not api_key.get_secret_value():
            raise errors.ConfigurationError(
                "falta OPENAI_API_KEY en el entorno", provider=Provider.OPENAI.value
            )
        self._client = AsyncOpenAI(
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
        """Arma el cuerpo de la request en el formato de OpenAI.

        En OpenAI el system prompt es un mensaje más de la lista, con
        `role="system"`, al principio. (En Anthropic es un parámetro aparte.)
        """
        payload_messages: list[dict[str, str]] = []
        if config.system_prompt:
            payload_messages.append({"role": "system", "content": config.system_prompt})
        payload_messages += [{"role": m.role.value, "content": m.content} for m in messages]

        payload: dict[str, Any] = {
            "model": config.model,
            "messages": payload_messages,
            # Los modelos actuales usan max_completion_tokens; max_tokens quedó
            # deprecado en la API de Chat Completions.
            "max_completion_tokens": config.max_tokens,
        }
        if config.temperature is not None:
            payload["temperature"] = config.temperature
        if config.top_p is not None:
            payload["top_p"] = config.top_p
        return payload

    # -- Generación --------------------------------------------------------

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        config: ModelConfig,
    ) -> ModelResponse:
        inicio = time.perf_counter()
        try:
            # `await` cede el control del event loop mientras espera la red.
            # Usar el cliente síncrono acá bloquearía todo el proceso.
            respuesta = await self._client.chat.completions.create(
                **self._build_payload(messages, config)
            )
        except Exception as exc:
            raise translate_openai_error(exc) from exc

        eleccion = respuesta.choices[0]
        uso = respuesta.usage
        return ModelResponse(
            provider=self.provider,
            model=respuesta.model,
            text=eleccion.message.content or "",
            finish_reason=eleccion.finish_reason,
            usage=TokenUsage(
                input_tokens=getattr(uso, "prompt_tokens", 0) or 0,
                output_tokens=getattr(uso, "completion_tokens", 0) or 0,
            ),
            latency_ms=(time.perf_counter() - inicio) * 1000,
        )

    # -- Streaming ---------------------------------------------------------

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        config: ModelConfig,
    ) -> AsyncIterator[StreamChunk]:
        """Recorre el stream del SDK con `async for` y emite cada fragmento con `yield`."""
        payload = self._build_payload(messages, config)
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}

        uso = TokenUsage()
        try:
            stream = await self._client.chat.completions.create(**payload)
            async for chunk in stream:
                if getattr(chunk, "usage", None) is not None:
                    uso = TokenUsage(
                        input_tokens=chunk.usage.prompt_tokens or 0,
                        output_tokens=chunk.usage.completion_tokens or 0,
                    )
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    yield StreamChunk(
                        type="delta",
                        delta=delta,
                        provider=self.provider,
                        model=config.model,
                    )
        except Exception as exc:
            traducido = translate_openai_error(exc)
            logger.warning("streaming de OpenAI interrumpido: %s", traducido)
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
            raise translate_openai_error(exc) from exc

    async def aclose(self) -> None:
        await self._client.close()
