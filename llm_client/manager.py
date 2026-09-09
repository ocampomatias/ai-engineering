"""Fábrica de clientes y capa de resiliencia.

`AsyncLLMManager` es la única clase que necesita usar el resto de la aplicación.

Elige el cliente concreto según `LLM_PROVIDER`, con un patrón Factory. Envuelve
cada llamada en un timeout y un semáforo, y reintenta con backoff exponencial
cuando el error es transitorio.

`generate()` siempre devuelve un `ModelResponse`. Si falló, `response.ok` es
False y `response.error` dice qué pasó: el llamador nunca ve una excepción del
SDK.
"""

import asyncio
import logging
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

from pydantic import SecretStr

from . import errors
from .base import BaseLLMClient
from .providers import AnthropicClient, OpenAIClient
from .schemas import (
    ChatMessage,
    ErrorInfo,
    ModelConfig,
    ModelResponse,
    Provider,
    StreamChunk,
)
from .settings import Settings, load_settings

logger = logging.getLogger(__name__)

#: Registro del patrón Factory: proveedor -> clase concreta. Agregar un
#: proveedor nuevo es agregar una entrada acá, sin tocar nada más.
CLIENT_REGISTRY: dict[Provider, type[BaseLLMClient]] = {
    Provider.OPENAI: OpenAIClient,
    Provider.ANTHROPIC: AnthropicClient,
}

#: Base del backoff exponencial, en segundos.
BACKOFF_BASE_SECONDS = 1.0
#: Tope del backoff, para que un 429 largo no deje el proceso colgado.
BACKOFF_MAX_SECONDS = 30.0


class AsyncLLMManager:
    """Punto de entrada único al cliente unificado de LLMs.

    Ejemplo mínimo::

        async with AsyncLLMManager() as manager:
            respuesta = await manager.generate("¿Qué es la entropía?")
            print(respuesta.text)
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        provider: Provider | str | None = None,
    ) -> None:
        self.settings = settings or load_settings()

        if provider is not None:
            self.provider = Provider(provider)
        else:
            self.provider = self.settings.provider

        if self.provider not in CLIENT_REGISTRY:
            raise errors.ConfigurationError(f"proveedor no soportado: {self.provider}")

        # Los clientes se crean una sola vez y se reutilizan (cachean la
        # conexión HTTP). Se instancian de forma diferida: pedir Anthropic no
        # debería exigir tener también una key de OpenAI.
        self._clients: dict[Provider, BaseLLMClient] = {}
        self._semaphore = asyncio.Semaphore(self.settings.max_concurrency)

    # -- Fábrica -----------------------------------------------------------

    def get_client(self, provider: Provider | str | None = None) -> BaseLLMClient:
        """Devuelve el cliente del proveedor pedido, creándolo si hace falta."""
        elegido = Provider(provider) if provider is not None else self.provider

        if elegido not in self._clients:
            clase = CLIENT_REGISTRY.get(elegido)
            if clase is None:
                raise errors.ConfigurationError(f"proveedor no soportado: {elegido}")
            api_key: SecretStr | None = self.settings.key_for(elegido)
            self._clients[elegido] = clase(api_key)  # type: ignore[call-arg]

        return self._clients[elegido]

    def build_config(
        self,
        provider: Provider | None = None,
        **overrides: object,
    ) -> ModelConfig:
        """Arma un `ModelConfig` con el modelo por defecto del proveedor.

        Cualquier parámetro se puede sobrescribir; Pydantic valida los rangos.
        """
        elegido = provider or self.provider
        parametros: dict[str, object] = {"model": self.settings.model_for(elegido)}
        parametros.update(overrides)
        return ModelConfig(**parametros)  # type: ignore[arg-type]

    # -- Resiliencia -------------------------------------------------------

    @staticmethod
    def _espera(intento: int, error: errors.LLMError) -> float:
        """Backoff exponencial con jitter; respeta `retry-after` si el proveedor lo mandó."""
        sugerido = getattr(error, "retry_after", None)
        if sugerido:
            return min(float(sugerido), BACKOFF_MAX_SECONDS)
        exponencial = BACKOFF_BASE_SECONDS * (2**intento)
        return min(exponencial + random.uniform(0, 0.5), BACKOFF_MAX_SECONDS)

    async def _con_reintentos(
        self,
        operacion: Callable[[], Awaitable[ModelResponse]],
        config: ModelConfig,
        descripcion: str,
    ) -> tuple[ModelResponse | None, ErrorInfo | None]:
        """Ejecuta una corrutina con timeout, semáforo y reintentos.

        Devuelve `(resultado, None)` si salió bien, o `(None, ErrorInfo)` si se
        agotaron los reintentos. Nunca propaga la excepción.
        """
        ultimo: errors.LLMError | None = None

        for intento in range(config.max_retries + 1):
            try:
                # El semáforo es lo que evita el 429 cuando salen muchas juntas.
                async with self._semaphore:
                    async with asyncio.timeout(config.timeout_seconds):
                        resultado = await operacion()
                return resultado, None

            except TimeoutError:
                ultimo = errors.LLMTimeoutError(
                    f"la llamada superó los {config.timeout_seconds}s",
                    provider=self.provider.value,
                )
            except errors.LLMError as exc:
                ultimo = exc
            except Exception as exc:  # red de seguridad
                ultimo = errors.UnexpectedError(repr(exc), provider=self.provider.value)

            if not ultimo.retryable or intento == config.max_retries:
                break

            demora = self._espera(intento, ultimo)
            logger.warning(
                "%s falló (%s). Reintento %d/%d en %.1fs",
                descripcion,
                ultimo,
                intento + 1,
                config.max_retries,
                demora,
            )
            await asyncio.sleep(demora)

        assert ultimo is not None
        return None, ErrorInfo(
            type=type(ultimo).__name__,
            message=str(ultimo),
            provider=ultimo.provider,
            retryable=ultimo.retryable,
            attempts=intento + 1,
        )

    # -- API pública -------------------------------------------------------

    async def generate(
        self,
        prompt: str | Sequence[ChatMessage],
        *,
        provider: Provider | str | None = None,
        config: ModelConfig | None = None,
        **overrides: object,
    ) -> ModelResponse:
        """Genera una respuesta completa.

        `prompt` puede ser un string (se convierte en un mensaje de usuario) o
        una lista de `ChatMessage` para una conversación con historial.
        """
        elegido = Provider(provider) if provider is not None else self.provider
        mensajes = self._normalizar(prompt)
        cfg = config or self.build_config(elegido, **overrides)

        try:
            cliente = self.get_client(elegido)
        except errors.LLMError as exc:
            return self._respuesta_de_error(elegido, cfg, exc)

        resultado, error = await self._con_reintentos(
            lambda: cliente.generate(mensajes, cfg),
            cfg,
            f"generate({elegido.value})",
        )
        if error is not None or resultado is None:
            return ModelResponse(provider=elegido, model=cfg.model, error=error)
        return resultado

    async def stream(
        self,
        prompt: str | Sequence[ChatMessage],
        *,
        provider: Provider | str | None = None,
        config: ModelConfig | None = None,
        **overrides: object,
    ) -> AsyncIterator[StreamChunk]:
        """Generador asíncrono de fragmentos de texto.

        Emite `StreamChunk(type="delta")` por cada fragmento, cierra con
        `type="done"` y, si algo falla, con `type="error"`. No lanza
        excepciones: un consumidor puede cerrar el stream con un mensaje en
        pantalla en vez de cortarse a la mitad.

        El streaming no se reintenta automáticamente: si ya se emitieron
        fragmentos al usuario, repetir la llamada duplicaría texto. Quien
        consuma el stream decide si vuelve a pedirlo.
        """
        elegido = Provider(provider) if provider is not None else self.provider
        mensajes = self._normalizar(prompt)
        cfg = config or self.build_config(elegido, **overrides)

        try:
            cliente = self.get_client(elegido)
        except errors.LLMError as exc:
            yield self._chunk_de_error(elegido, cfg, exc)
            return

        try:
            async with self._semaphore:
                async with asyncio.timeout(cfg.timeout_seconds):
                    async for chunk in cliente.stream(mensajes, cfg):
                        yield chunk
        except TimeoutError:
            yield self._chunk_de_error(
                elegido,
                cfg,
                errors.LLMTimeoutError(
                    f"el streaming superó los {cfg.timeout_seconds}s",
                    provider=elegido.value,
                ),
            )
        except errors.LLMError as exc:
            yield self._chunk_de_error(elegido, cfg, exc)
        except Exception as exc:
            yield self._chunk_de_error(
                elegido, cfg, errors.UnexpectedError(repr(exc), provider=elegido.value)
            )

    async def stream_text(
        self,
        prompt: str | Sequence[ChatMessage],
        **kwargs: object,
    ) -> AsyncIterator[str]:
        """Atajo: emite solo los fragmentos de texto, ignorando los de control."""
        async for chunk in self.stream(prompt, **kwargs):  # type: ignore[arg-type]
            if chunk.type == "delta":
                yield chunk.delta

    async def generate_many(
        self,
        prompts: Sequence[str | Sequence[ChatMessage]],
        **kwargs: object,
    ) -> list[ModelResponse]:
        """Dispara varios prompts concurrentemente, con el semáforo como tope.

        `return_exceptions=True` no hace falta acá: `generate()` ya devuelve el
        error dentro del `ModelResponse`, así que un prompt fallido no tumba el
        lote.
        """
        tareas = [self.generate(p, **kwargs) for p in prompts]  # type: ignore[arg-type]
        return await asyncio.gather(*tareas)

    async def compare_providers(
        self,
        prompt: str,
        providers: Sequence[Provider] | None = None,
        **kwargs: object,
    ) -> dict[Provider, ModelResponse]:
        """Manda el mismo prompt a varios proveedores en paralelo y compara."""
        objetivo = list(providers or self.settings.available_providers())
        inicio = time.perf_counter()
        respuestas = await asyncio.gather(
            *(self.generate(prompt, provider=p, **kwargs) for p in objetivo)  # type: ignore[arg-type]
        )
        logger.info(
            "comparación de %d proveedores en %.2fs",
            len(objetivo),
            time.perf_counter() - inicio,
        )
        return dict(zip(objetivo, respuestas, strict=True))

    # -- Auxiliares --------------------------------------------------------

    @staticmethod
    def _normalizar(prompt: str | Sequence[ChatMessage]) -> list[ChatMessage]:
        if isinstance(prompt, str):
            return [ChatMessage.user(prompt)]
        return list(prompt)

    @staticmethod
    def _respuesta_de_error(
        provider: Provider,
        config: ModelConfig,
        exc: errors.LLMError,
    ) -> ModelResponse:
        return ModelResponse(
            provider=provider,
            model=config.model,
            error=ErrorInfo(
                type=type(exc).__name__,
                message=str(exc),
                provider=exc.provider,
                retryable=exc.retryable,
            ),
        )

    @staticmethod
    def _chunk_de_error(
        provider: Provider,
        config: ModelConfig,
        exc: errors.LLMError,
    ) -> StreamChunk:
        return StreamChunk(
            type="error",
            provider=provider,
            model=config.model,
            error=ErrorInfo(
                type=type(exc).__name__,
                message=str(exc),
                provider=exc.provider,
                retryable=exc.retryable,
            ),
        )

    # -- Ciclo de vida -----------------------------------------------------

    async def aclose(self) -> None:
        """Cierra todos los clientes HTTP abiertos."""
        await asyncio.gather(
            *(cliente.aclose() for cliente in self._clients.values()),
            return_exceptions=True,
        )
        self._clients.clear()

    async def __aenter__(self) -> "AsyncLLMManager":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
