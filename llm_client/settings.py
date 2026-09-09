"""Carga de configuración desde el entorno (.env), validada con Pydantic.

Se usa `python-dotenv` + un modelo de Pydantic en lugar de `pydantic-settings`
para no agregar dependencias fuera de las cuatro que pide la consigna
(openai, anthropic, pydantic, python-dotenv).
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .schemas import Provider

#: Modelos por defecto. Se sobrescriben con OPENAI_MODEL / ANTHROPIC_MODEL.
DEFAULT_MODELS: dict[Provider, str] = {
    Provider.OPENAI: "gpt-4o-mini",
    Provider.ANTHROPIC: "claude-opus-5",
}


class Settings(BaseModel):
    """Configuración de la aplicación."""

    model_config = ConfigDict(frozen=True)

    provider: Provider = Field(
        default=Provider.OPENAI,
        description="Proveedor activo. Es la 'variable de configuración' que elige el cliente.",
    )
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    openai_model: str = DEFAULT_MODELS[Provider.OPENAI]
    anthropic_model: str = DEFAULT_MODELS[Provider.ANTHROPIC]
    max_concurrency: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Tope de llamadas simultáneas (semáforo) en las operaciones en lote.",
    )

    def model_for(self, provider: Provider) -> str:
        return {
            Provider.OPENAI: self.openai_model,
            Provider.ANTHROPIC: self.anthropic_model,
        }[provider]

    def key_for(self, provider: Provider) -> SecretStr | None:
        return {
            Provider.OPENAI: self.openai_api_key,
            Provider.ANTHROPIC: self.anthropic_api_key,
        }[provider]

    def available_providers(self) -> list[Provider]:
        """Proveedores que tienen una API key cargada."""
        return [p for p in Provider if self.key_for(p) is not None]


def _secret(name: str) -> SecretStr | None:
    valor = os.getenv(name, "").strip()
    return SecretStr(valor) if valor else None


def load_settings(env_file: str | Path | None = None) -> Settings:
    """Lee el `.env` y devuelve la configuración validada.

    No falla si falta una API key: eso se reporta como error controlado en el
    momento en que se intenta usar ese proveedor.
    """
    load_dotenv(dotenv_path=env_file, override=False)

    provider_raw = os.getenv("LLM_PROVIDER", Provider.OPENAI.value).strip().lower()
    provider = Provider(provider_raw) if provider_raw in set(Provider) else Provider.OPENAI

    return Settings(
        provider=provider,
        openai_api_key=_secret("OPENAI_API_KEY"),
        anthropic_api_key=_secret("ANTHROPIC_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL", DEFAULT_MODELS[Provider.OPENAI]).strip()
        or DEFAULT_MODELS[Provider.OPENAI],
        anthropic_model=os.getenv("ANTHROPIC_MODEL", DEFAULT_MODELS[Provider.ANTHROPIC]).strip()
        or DEFAULT_MODELS[Provider.ANTHROPIC],
        max_concurrency=int(os.getenv("MAX_CONCURRENCY", "5")),
    )
