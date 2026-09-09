"""Contratos de datos del cliente unificado, validados con Pydantic v2.

Todo lo que entra y sale de los clientes pasa por estos modelos. El objetivo es
evitar el "error de diccionarios anidados": nadie escribe
`resp["choices"][0]["message"]["content"]` a mano, porque cada proveedor
devuelve una forma distinta y esa expresión revienta en cuanto se cambia de
proveedor.
"""

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class Provider(StrEnum):
    """Proveedores soportados. Es el valor de la variable de configuración."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class Role(StrEnum):
    """Roles válidos en una conversación."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ChatMessage(BaseModel):
    """Un mensaje de la conversación, independiente del proveedor."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Role
    content: str = Field(min_length=1, description="Texto del mensaje. No puede estar vacío.")

    @field_validator("content")
    @classmethod
    def _sin_espacios_vacios(cls, v: str) -> str:
        limpio = v.strip()
        if not limpio:
            raise ValueError("el contenido no puede ser solo espacios en blanco")
        return limpio

    @classmethod
    def system(cls, content: str) -> "ChatMessage":
        return cls(role=Role.SYSTEM, content=content)

    @classmethod
    def user(cls, content: str) -> "ChatMessage":
        return cls(role=Role.USER, content=content)

    @classmethod
    def assistant(cls, content: str) -> "ChatMessage":
        return cls(role=Role.ASSISTANT, content=content)


class ModelConfig(BaseModel):
    """Parámetros de generación, con sus rangos validados.

    `temperature` se valida en el rango amplio 0-2 (el de OpenAI). Anthropic ya
    no acepta parámetros de sampling en sus modelos actuales, así que su cliente
    ignora el valor de forma explícita y documentada en lugar de fallar. Esa es
    exactamente la clase de diferencia que la capa de abstracción tiene que
    absorber.
    """

    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, description="ID del modelo, ej. 'claude-opus-5'.")
    temperature: Annotated[float, Field(ge=0.0, le=2.0)] | None = Field(
        default=None,
        description="Aleatoriedad de la respuesta, de 0 a 2. None deja el default del proveedor.",
    )
    max_tokens: Annotated[int, Field(ge=1, le=128_000)] = Field(
        default=1024,
        description="Tope de tokens de la respuesta.",
    )
    top_p: Annotated[float, Field(ge=0.0, le=1.0)] | None = Field(
        default=None,
        description="Nucleus sampling. Mutuamente excluyente con temperature por convención.",
    )
    system_prompt: str | None = Field(
        default=None,
        description="Instrucción de sistema. Cada cliente la ubica donde su API la espera.",
    )
    timeout_seconds: Annotated[float, Field(gt=0, le=600)] = Field(
        default=60.0,
        description="Timeout por llamada, aplicado con asyncio.timeout.",
    )
    max_retries: Annotated[int, Field(ge=0, le=10)] = Field(
        default=3,
        description="Reintentos ante rate limit, red o 5xx.",
    )

    @model_validator(mode="after")
    def _no_ambos_sampling(self) -> "ModelConfig":
        if self.temperature is not None and self.top_p is not None:
            raise ValueError(
                "definí temperature o top_p, no ambos: los proveedores recomiendan usar uno solo"
            )
        return self


class TokenUsage(BaseModel):
    """Consumo de tokens normalizado entre proveedores."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ErrorInfo(BaseModel):
    """Error devuelto de forma estructurada, en lugar de una excepción suelta."""

    model_config = ConfigDict(frozen=True)

    type: str = Field(description="Nombre de la clase de error, ej. 'RateLimitError'.")
    message: str
    provider: str | None = None
    retryable: bool = False
    attempts: int = Field(default=1, description="Intentos realizados antes de rendirse.")


class ModelResponse(BaseModel):
    """Respuesta unificada. Siempre se devuelve un objeto de este tipo.

    Si `ok` es False, `error` explica qué pasó y `text` queda vacío: el
    programa que llama no se rompe, decide qué hacer.
    """

    model_config = ConfigDict(frozen=True)

    provider: Provider
    model: str
    text: str = ""
    finish_reason: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: float = 0.0
    error: ErrorInfo | None = None
    raw: dict[str, Any] | None = Field(
        default=None,
        exclude=True,
        description="Payload crudo del proveedor, útil para depurar. No se serializa.",
    )

    @property
    def ok(self) -> bool:
        return self.error is None


class StreamChunk(BaseModel):
    """Fragmento emitido por el generador asíncrono de streaming.

    El generador nunca lanza una excepción hacia afuera: si algo falla, emite un
    último chunk con `type="error"`. Así un consumidor (una API web, una CLI)
    puede cerrar el stream con un mensaje en vez de cortarse a la mitad.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal["delta", "done", "error"] = "delta"
    delta: str = ""
    provider: Provider | None = None
    model: str | None = None
    usage: TokenUsage | None = None
    error: ErrorInfo | None = None

    @property
    def is_terminal(self) -> bool:
        return self.type in ("done", "error")


class ProviderCredentials(BaseModel):
    """API keys envueltas en SecretStr para que no se filtren en logs ni tracebacks."""

    model_config = ConfigDict(frozen=True)

    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None

    def key_for(self, provider: Provider) -> SecretStr | None:
        return {
            Provider.OPENAI: self.openai_api_key,
            Provider.ANTHROPIC: self.anthropic_api_key,
        }[provider]
