"""Interfaz común a todos los proveedores.

`BaseLLMClient` define el contrato: dos métodos asíncronos, `generate` (una
respuesta completa) y `stream` (un generador asíncrono de fragmentos). Toda la
lógica de negocio de la aplicación habla con esta interfaz, nunca con el SDK de
un proveedor.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence

from .schemas import ChatMessage, ModelConfig, ModelResponse, Provider, StreamChunk


class BaseLLMClient(ABC):
    """Clase base abstracta de un cliente de LLM asíncrono."""

    #: Cada subclase declara a qué proveedor pertenece.
    provider: Provider

    @abstractmethod
    async def generate(
        self,
        messages: Sequence[ChatMessage],
        config: ModelConfig,
    ) -> ModelResponse:
        """Devuelve la respuesta completa del modelo.

        No debe lanzar excepciones del SDK hacia afuera: las traduce a la
        jerarquía de `llm_client.errors`.
        """

    @abstractmethod
    def stream(
        self,
        messages: Sequence[ChatMessage],
        config: ModelConfig,
    ) -> AsyncIterator[StreamChunk]:
        """Generador asíncrono de fragmentos de texto.

        Se declara como método normal (no `async def`) que devuelve un
        `AsyncIterator`: así la subclase puede implementarlo con `yield` y el
        llamador siempre hace `async for chunk in client.stream(...)`.
        """

    @abstractmethod
    async def aclose(self) -> None:
        """Cierra el cliente HTTP subyacente."""

    async def __aenter__(self) -> "BaseLLMClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
