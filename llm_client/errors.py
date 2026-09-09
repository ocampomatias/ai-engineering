"""Jerarquía de errores propia.

Los SDKs de OpenAI y Anthropic lanzan excepciones distintas para el mismo
problema (por ejemplo, `openai.RateLimitError` vs `anthropic.RateLimitError`).
El resto de la aplicación no debería tener que conocer esa diferencia: cada
cliente traduce la excepción del proveedor a una de estas clases.
"""


class LLMError(Exception):
    """Base de todos los errores del cliente unificado."""

    #: Si es True, la capa de resiliencia puede reintentar la llamada.
    retryable: bool = False

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider

    def __str__(self) -> str:
        if self.provider:
            return f"[{self.provider}] {self.message}"
        return self.message


class ConfigurationError(LLMError):
    """Falta una API key, el proveedor no existe o la config es inválida."""


class AuthenticationError(LLMError):
    """API key ausente, inválida o sin permisos (401 / 403)."""


class ModelNotFoundError(LLMError):
    """El modelo pedido no existe o la cuenta no tiene acceso (404)."""


class InvalidRequestError(LLMError):
    """El proveedor rechazó los parámetros de la llamada (400 / 422)."""


class RateLimitError(LLMError):
    """Se superó el límite de tasa o de cuota (429). Reintentable."""

    retryable = True

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, provider=provider)
        #: Segundos sugeridos por el header `retry-after`, si vino.
        self.retry_after = retry_after


class NetworkError(LLMError):
    """Fallo de red o de conexión con la API. Reintentable."""

    retryable = True


class LLMTimeoutError(LLMError):
    """La llamada superó el timeout configurado. Reintentable."""

    retryable = True


class ProviderServerError(LLMError):
    """Error interno del proveedor (5xx). Reintentable."""

    retryable = True


class UnexpectedError(LLMError):
    """Cualquier otra falla no clasificada."""
