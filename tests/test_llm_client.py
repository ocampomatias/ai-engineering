"""Tests del cliente unificado. No se necesitan API keys: todo va con dobles.

    python -m pytest -q
"""

import asyncio

import pytest
from pydantic import SecretStr, ValidationError

from llm_client import (
    AsyncLLMManager,
    ChatMessage,
    ModelConfig,
    ModelResponse,
    Provider,
    Role,
    Settings,
    StreamChunk,
    TokenUsage,
)
from llm_client import errors
from llm_client.base import BaseLLMClient
from llm_client.manager import CLIENT_REGISTRY
from llm_client.providers.anthropic_client import AnthropicClient
from llm_client.providers.openai_client import OpenAIClient

# --- Validación con Pydantic ---------------------------------------------


def test_chat_message_rechaza_contenido_vacio():
    with pytest.raises(ValidationError):
        ChatMessage(role=Role.USER, content="")
    with pytest.raises(ValidationError):
        ChatMessage(role=Role.USER, content="   ")


def test_chat_message_rechaza_rol_invalido():
    with pytest.raises(ValidationError):
        ChatMessage(role="moderator", content="hola")  # type: ignore[arg-type]


def test_chat_message_recorta_espacios():
    assert ChatMessage.user("  hola  ").content == "hola"


@pytest.mark.parametrize("valor", [-0.1, 2.1, 5])
def test_temperature_fuera_de_rango(valor):
    with pytest.raises(ValidationError):
        ModelConfig(model="m", temperature=valor)


@pytest.mark.parametrize("valor", [0.0, 1.0, 2.0])
def test_temperature_en_rango(valor):
    assert ModelConfig(model="m", temperature=valor).temperature == valor


@pytest.mark.parametrize("valor", [0, -5, 200_000])
def test_max_tokens_fuera_de_rango(valor):
    with pytest.raises(ValidationError):
        ModelConfig(model="m", max_tokens=valor)


def test_temperature_y_top_p_juntos_es_error():
    with pytest.raises(ValidationError):
        ModelConfig(model="m", temperature=0.5, top_p=0.9)


def test_config_rechaza_campos_desconocidos():
    with pytest.raises(ValidationError):
        ModelConfig(model="m", temperatura=0.5)  # type: ignore[call-arg]


def test_usage_suma_total():
    assert TokenUsage(input_tokens=10, output_tokens=7).total_tokens == 17


# --- Secretos -------------------------------------------------------------


def test_api_key_no_se_filtra_en_el_repr():
    settings = Settings(openai_api_key=SecretStr("sk-super-secreta-123"))
    assert "sk-super-secreta-123" not in repr(settings)
    assert "sk-super-secreta-123" not in str(settings)
    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "sk-super-secreta-123"


# --- Traducción de formato por proveedor ---------------------------------


def test_openai_pone_el_system_como_mensaje():
    cliente = OpenAIClient(SecretStr("sk-test"))
    payload = cliente._build_payload(
        [ChatMessage.user("hola")],
        ModelConfig(model="gpt-x", system_prompt="sos un bot", temperature=0.3),
    )
    assert payload["messages"][0] == {"role": "system", "content": "sos un bot"}
    assert payload["max_completion_tokens"] == 1024
    assert payload["temperature"] == 0.3


def test_anthropic_saca_el_system_de_la_lista_de_mensajes():
    cliente = AnthropicClient(SecretStr("sk-test"))
    payload = cliente._build_payload(
        [ChatMessage.system("sos un bot"), ChatMessage.user("hola")],
        ModelConfig(model="claude-x", max_tokens=50),
    )
    assert payload["system"] == "sos un bot"
    assert payload["messages"] == [{"role": "user", "content": "hola"}]
    assert payload["max_tokens"] == 50


def test_anthropic_ignora_temperature_sin_fallar():
    """Los modelos actuales de Anthropic no aceptan sampling: se descarta, no revienta."""
    cliente = AnthropicClient(SecretStr("sk-test"))
    payload = cliente._build_payload(
        [ChatMessage.user("hola")],
        ModelConfig(model="claude-x", temperature=1.5),
    )
    assert "temperature" not in payload
    assert "top_p" not in payload


def test_falta_api_key_es_error_de_configuracion():
    with pytest.raises(errors.ConfigurationError):
        OpenAIClient(None)
    with pytest.raises(errors.ConfigurationError):
        AnthropicClient(SecretStr(""))


# --- Dobles de prueba ----------------------------------------------------


class ClienteFalso(BaseLLMClient):
    """Cliente controlable: falla las primeras N veces y después responde."""

    provider = Provider.OPENAI

    def __init__(self, *, fallos: int = 0, error: Exception | None = None) -> None:
        self.fallos_restantes = fallos
        self.error = error or errors.RateLimitError("429 simulado", provider="openai")
        self.llamadas = 0
        self.cerrado = False

    async def generate(self, messages, config) -> ModelResponse:
        self.llamadas += 1
        if self.fallos_restantes > 0:
            self.fallos_restantes -= 1
            raise self.error
        return ModelResponse(
            provider=self.provider,
            model=config.model,
            text="respuesta simulada",
            usage=TokenUsage(input_tokens=3, output_tokens=2),
        )

    async def stream(self, messages, config):
        self.llamadas += 1
        if self.fallos_restantes > 0:
            self.fallos_restantes -= 1
            raise self.error
        for pieza in ("la ", "entropía ", "mide ", "el desorden."):
            yield StreamChunk(type="delta", delta=pieza, provider=self.provider)
        yield StreamChunk(
            type="done",
            provider=self.provider,
            usage=TokenUsage(input_tokens=3, output_tokens=4),
        )

    async def aclose(self) -> None:
        self.cerrado = True


@pytest.fixture
def sin_espera(monkeypatch):
    """Anula el backoff para que los tests de reintento corran instantáneos."""
    dormir_real = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda *_a, **_k: dormir_real(0))


def manager_con(cliente: BaseLLMClient) -> AsyncLLMManager:
    """Manager con un cliente falso ya inyectado, sin tocar el entorno."""
    settings = Settings(
        provider=Provider.OPENAI,
        openai_api_key=SecretStr("sk-test"),
        max_concurrency=3,
    )
    manager = AsyncLLMManager(settings)
    manager._clients[Provider.OPENAI] = cliente
    return manager


# --- Asincronía y streaming ---------------------------------------------


@pytest.mark.asyncio
async def test_generate_devuelve_respuesta_unificada():
    manager = manager_con(ClienteFalso())
    respuesta = await manager.generate("¿Qué es la entropía?")
    assert respuesta.ok
    assert respuesta.text == "respuesta simulada"
    assert respuesta.usage.total_tokens == 5


@pytest.mark.asyncio
async def test_stream_emite_deltas_y_cierra_con_done():
    manager = manager_con(ClienteFalso())
    chunks = [c async for c in manager.stream("dale")]

    assert [c.type for c in chunks] == ["delta"] * 4 + ["done"]
    texto = "".join(c.delta for c in chunks if c.type == "delta")
    assert texto == "la entropía mide el desorden."
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.output_tokens == 4


@pytest.mark.asyncio
async def test_stream_text_devuelve_solo_strings():
    manager = manager_con(ClienteFalso())
    piezas = [t async for t in manager.stream_text("dale")]
    assert piezas == ["la ", "entropía ", "mide ", "el desorden."]


@pytest.mark.asyncio
async def test_generate_no_bloquea_el_event_loop():
    """Mientras una llamada espera, el loop tiene que poder avanzar otra tarea."""
    orden: list[str] = []

    class ClienteLento(ClienteFalso):
        async def generate(self, messages, config):
            await asyncio.sleep(0.05)
            orden.append("llm")
            return await super().generate(messages, config)

    async def otra_tarea():
        await asyncio.sleep(0.01)
        orden.append("otra")

    manager = manager_con(ClienteLento())
    await asyncio.gather(manager.generate("hola"), otra_tarea())

    assert orden == ["otra", "llm"], "la llamada al LLM bloqueó el event loop"


# --- Resiliencia ---------------------------------------------------------


@pytest.mark.asyncio
async def test_reintenta_ante_rate_limit_y_termina_bien(sin_espera):
    cliente = ClienteFalso(fallos=2)
    manager = manager_con(cliente)

    respuesta = await manager.generate("hola", max_retries=3)

    assert respuesta.ok
    assert cliente.llamadas == 3  # 2 fallos + 1 exitosa


@pytest.mark.asyncio
async def test_devuelve_error_estructurado_al_agotar_reintentos(sin_espera):
    cliente = ClienteFalso(fallos=99)
    manager = manager_con(cliente)

    respuesta = await manager.generate("hola", max_retries=2)

    assert not respuesta.ok
    assert respuesta.error is not None
    assert respuesta.error.type == "RateLimitError"
    assert respuesta.error.retryable is True
    assert respuesta.error.attempts == 3
    assert respuesta.text == ""  # sin crash, sin texto inventado


@pytest.mark.asyncio
async def test_no_reintenta_errores_permanentes():
    cliente = ClienteFalso(
        fallos=99,
        error=errors.AuthenticationError("key inválida", provider="openai"),
    )
    manager = manager_con(cliente)

    respuesta = await manager.generate("hola", max_retries=5)

    assert not respuesta.ok
    assert cliente.llamadas == 1, "un 401 no se reintenta"
    assert respuesta.error is not None
    assert respuesta.error.retryable is False


@pytest.mark.asyncio
async def test_timeout_devuelve_error_controlado():
    class ClienteColgado(ClienteFalso):
        async def generate(self, messages, config):
            await asyncio.sleep(5)
            raise AssertionError("no debería llegar acá")

    manager = manager_con(ClienteColgado())
    respuesta = await manager.generate("hola", timeout_seconds=0.05, max_retries=0)

    assert not respuesta.ok
    assert respuesta.error is not None
    assert respuesta.error.type == "LLMTimeoutError"


@pytest.mark.asyncio
async def test_error_en_streaming_cierra_con_chunk_de_error():
    cliente = ClienteFalso(fallos=1)
    manager = manager_con(cliente)

    chunks = [c async for c in manager.stream("hola")]

    assert len(chunks) == 1
    assert chunks[0].type == "error"
    assert chunks[0].error is not None
    assert chunks[0].error.type == "RateLimitError"


@pytest.mark.asyncio
async def test_falta_de_api_key_no_rompe_el_programa():
    manager = AsyncLLMManager(Settings(provider=Provider.OPENAI))
    respuesta = await manager.generate("hola")

    assert not respuesta.ok
    assert respuesta.error is not None
    assert respuesta.error.type == "ConfigurationError"


@pytest.mark.asyncio
async def test_un_prompt_fallido_no_tumba_el_lote(sin_espera):
    class ClienteIntermitente(ClienteFalso):
        async def generate(self, messages, config):
            self.llamadas += 1
            if "malo" in messages[0].content:
                raise errors.InvalidRequestError("400 simulado", provider="openai")
            return ModelResponse(provider=self.provider, model=config.model, text="ok")

    manager = manager_con(ClienteIntermitente())
    respuestas = await manager.generate_many(["bueno 1", "malo", "bueno 2"])

    assert [r.ok for r in respuestas] == [True, False, True]


# --- Intercambiabilidad --------------------------------------------------


def test_el_registro_cubre_todos_los_proveedores():
    assert set(CLIENT_REGISTRY) == set(Provider)


def test_la_fabrica_devuelve_la_clase_correcta():
    settings = Settings(
        openai_api_key=SecretStr("sk-a"),
        anthropic_api_key=SecretStr("sk-b"),
    )
    manager = AsyncLLMManager(settings, provider=Provider.OPENAI)

    assert isinstance(manager.get_client(), OpenAIClient)
    assert isinstance(manager.get_client(Provider.ANTHROPIC), AnthropicClient)
    # Ambos cumplen la misma interfaz: eso es la intercambiabilidad.
    assert isinstance(manager.get_client(Provider.ANTHROPIC), BaseLLMClient)


def test_cambiar_de_proveedor_no_cambia_la_interfaz():
    for provider, clase in CLIENT_REGISTRY.items():
        assert issubclass(clase, BaseLLMClient)
        assert clase.provider is provider


def test_proveedor_desconocido_es_error():
    with pytest.raises(ValueError):
        AsyncLLMManager(Settings(), provider="cohere")


def test_build_config_toma_el_modelo_del_proveedor():
    settings = Settings(
        openai_api_key=SecretStr("sk-a"),
        openai_model="gpt-demo",
        anthropic_model="claude-demo",
    )
    manager = AsyncLLMManager(settings, provider=Provider.OPENAI)

    assert manager.build_config().model == "gpt-demo"
    assert manager.build_config(Provider.ANTHROPIC).model == "claude-demo"


@pytest.mark.asyncio
async def test_aclose_cierra_los_clientes():
    cliente = ClienteFalso()
    manager = manager_con(cliente)
    async with manager:
        pass
    assert cliente.cerrado is True
