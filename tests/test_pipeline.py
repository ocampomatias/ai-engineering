"""Tests del pipeline de extracción. Sin API keys: el LLM es un modelo falso con guion.

    python -m pytest -q tests/test_pipeline.py
"""

import logging
from typing import Any

import anthropic
import httpx
import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import SecretStr, ValidationError

from llm_client import Provider, Settings
from llm_client.errors import ConfigurationError
from pipeline import (
    EntidadesTecnicas,
    ExtraccionError,
    NivelCriticidad,
    construir_cadena,
    crear_modelo,
    crear_prompt,
    process_text,
)

VALIDO = {
    "tecnologias": ["FastAPI", "Redis", "PostgreSQL"],
    "nivel_de_criticidad": "alta",
    "resumen_tecnico": "API con caché en Redis y persistencia en PostgreSQL que satura conexiones.",
}


class ModeloFalso(BaseChatModel):
    """Chat model que devuelve, en orden, lo que dice el guion.

    Si el paso es una excepción, la lanza: así se simula una falla de la API.
    """

    guion: list[Any]
    llamadas: int = 0

    @property
    def _llm_type(self) -> str:
        return "falso"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ModeloFalso":  # type: ignore[override]
        return self

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        paso = self.guion[self.llamadas]
        self.llamadas += 1
        if isinstance(paso, BaseException):
            raise paso
        return ChatResult(generations=[ChatGeneration(message=paso)])


def tool_call(args: dict[str, Any], **metadata: Any) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "EntidadesTecnicas", "args": args, "id": "call_1"}],
        response_metadata=metadata or {"stop_reason": "tool_use"},
    )


def cadena_con(*guion: Any, intentos: int = 3) -> tuple[ModeloFalso, Any]:
    modelo = ModeloFalso(guion=list(guion))
    return modelo, construir_cadena(modelo, intentos=intentos, backoff=False)


# --- Esquema ---------------------------------------------------------------


def test_esquema_acepta_el_ejemplo_de_la_consigna():
    entidades = EntidadesTecnicas(**VALIDO)
    assert entidades.nivel_de_criticidad is NivelCriticidad.ALTA


@pytest.mark.parametrize("tecnologias", [[], ["", "   "]])
def test_esquema_rechaza_tecnologias_vacias(tecnologias):
    with pytest.raises(ValidationError):
        EntidadesTecnicas(**{**VALIDO, "tecnologias": tecnologias})


def test_esquema_saca_repetidos_y_espacios():
    entidades = EntidadesTecnicas(**{**VALIDO, "tecnologias": [" Redis ", "redis", "Kafka"]})
    assert entidades.tecnologias == ["Redis", "Kafka"]


@pytest.mark.parametrize("valor", ["urgente", "crítica", 3])
def test_esquema_rechaza_criticidad_fuera_del_enum(valor):
    with pytest.raises(ValidationError):
        EntidadesTecnicas(**{**VALIDO, "nivel_de_criticidad": valor})


def test_esquema_normaliza_mayusculas_de_criticidad():
    assert EntidadesTecnicas(**{**VALIDO, "nivel_de_criticidad": " Media "}).nivel_de_criticidad == "media"


def test_esquema_rechaza_resumen_corto_y_campos_extra():
    with pytest.raises(ValidationError):
        EntidadesTecnicas(**{**VALIDO, "resumen_tecnico": "corto"})
    with pytest.raises(ValidationError):
        EntidadesTecnicas(**VALIDO, confianza=0.9)


# --- Prompt ----------------------------------------------------------------


def test_prompt_solo_espera_el_texto():
    assert crear_prompt().input_variables == ["texto"]


def test_prompt_incluye_texto_e_instrucciones_de_formato():
    mensajes = crear_prompt().format_messages(texto="Timeout en {Redis}")
    assert "nivel_de_criticidad" in mensajes[0].content
    assert "Timeout en {Redis}" in mensajes[1].content


# --- Cadena y resiliencia --------------------------------------------------


async def test_process_text_devuelve_objeto_validado():
    modelo, cadena = cadena_con(tool_call(VALIDO))
    resultado = await process_text("La API en FastAPI se cae.", cadena=cadena)
    assert isinstance(resultado, EntidadesTecnicas)
    assert resultado.tecnologias == ["FastAPI", "Redis", "PostgreSQL"]
    assert modelo.llamadas == 1


async def test_reintenta_si_la_salida_no_cumple_el_esquema(caplog):
    malo = {**VALIDO, "nivel_de_criticidad": "urgentísima"}
    modelo, cadena = cadena_con(tool_call(malo), tool_call(VALIDO))
    with caplog.at_level(logging.INFO, logger="pipeline"):
        resultado = await process_text("texto", cadena=cadena)
    assert resultado.nivel_de_criticidad == "alta"
    assert modelo.llamadas == 2
    assert "no cumple el esquema" in caplog.text


async def test_reintenta_si_falta_un_campo():
    incompleto = {"tecnologias": ["Redis"]}
    modelo, cadena = cadena_con(tool_call(incompleto), tool_call(VALIDO))
    await process_text("texto", cadena=cadena)
    assert modelo.llamadas == 2


async def test_reintenta_si_el_modelo_contesta_texto_en_vez_de_la_estructura():
    modelo, cadena = cadena_con(AIMessage(content="No sé"), tool_call(VALIDO))
    await process_text("texto", cadena=cadena)
    assert modelo.llamadas == 2


@pytest.mark.parametrize("metadata", [{"stop_reason": "max_tokens"}, {"finish_reason": "length"}])
async def test_descarta_respuesta_truncada_aunque_parezca_valida(metadata):
    # Los args validan, pero el proveedor avisó que cortó por tokens: no se confía.
    modelo, cadena = cadena_con(tool_call(VALIDO, **metadata), tool_call(VALIDO))
    await process_text("texto", cadena=cadena)
    assert modelo.llamadas == 2


async def test_agota_los_reintentos_y_devuelve_error_controlado():
    malo = tool_call({"tecnologias": []})
    modelo, cadena = cadena_con(malo, malo, malo, intentos=3)
    with pytest.raises(ExtraccionError) as info:
        await process_text("texto", cadena=cadena)
    assert modelo.llamadas == 3
    assert info.value.intentos == 3


async def test_reintenta_error_transitorio_de_red():
    caida = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    modelo, cadena = cadena_con(caida, tool_call(VALIDO))
    await process_text("texto", cadena=cadena)
    assert modelo.llamadas == 2


async def test_no_reintenta_errores_permanentes():
    modelo, cadena = cadena_con(RuntimeError("bug"), tool_call(VALIDO))
    with pytest.raises(ExtraccionError) as info:
        await process_text("texto", cadena=cadena)
    assert modelo.llamadas == 1
    assert isinstance(info.value.__cause__, RuntimeError)


async def test_texto_vacio_no_llama_al_modelo():
    modelo, cadena = cadena_con(tool_call(VALIDO))
    with pytest.raises(ValueError):
        await process_text("   ", cadena=cadena)
    assert modelo.llamadas == 0


async def test_abatch_procesa_varios_textos():
    modelo, cadena = cadena_con(tool_call(VALIDO), tool_call(VALIDO))
    resultados = await cadena.abatch([{"texto": "uno"}, {"texto": "dos"}], config={"max_concurrency": 1})
    assert len(resultados) == 2
    assert modelo.llamadas == 2


# --- Modelo ----------------------------------------------------------------


def test_crear_modelo_sin_key_es_error_de_configuracion():
    with pytest.raises(ConfigurationError):
        crear_modelo(Settings(provider=Provider.ANTHROPIC), Provider.ANTHROPIC)


def test_crear_modelo_desactiva_los_reintentos_del_sdk():
    settings = Settings(provider=Provider.ANTHROPIC, anthropic_api_key=SecretStr("sk-test"))
    modelo = crear_modelo(settings)
    assert isinstance(modelo, ChatAnthropic)
    assert modelo.max_retries == 0
    assert "sk-test" not in repr(modelo)
