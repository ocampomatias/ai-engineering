"""Configuración del RAG en Pinecone, validada con Pydantic.

La key y el nombre del índice salen del `.env` (PINECONE_API_KEY, INDEX_NAME).
El resto tiene defaults razonables y se puede cambiar por variable de entorno
o al crear el objeto. Ingesta, recuperación y evaluación usan el mismo objeto:
el modelo de embeddings, el índice y el namespace tienen que coincidir.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from rag.config import MODELO_EMBEDDINGS

RAIZ = Path(__file__).resolve().parent.parent


class ConfigError(Exception):
    """Falta una variable de entorno obligatoria (por ejemplo, PINECONE_API_KEY)."""


class ConfigPinecone(BaseModel):
    """Infraestructura, ingesta y recuperación del RAG en la nube."""

    model_config = ConfigDict(frozen=True)

    # --- Pinecone -----------------------------------------------------------
    api_key: SecretStr | None = Field(
        default=None,
        description="PINECONE_API_KEY. SecretStr: no aparece si se imprime la configuración.",
    )
    indice: str = Field(
        default="tambor-docs",
        min_length=1,
        max_length=45,
        pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$",
        description="INDEX_NAME. Minúsculas, números y guiones: las reglas de nombres de Pinecone.",
    )
    namespace: str = Field(
        default="plataforma",
        min_length=1,
        description=(
            "Partición lógica dentro del índice. Separa este corpus de cualquier otro (u otro "
            "entorno) que viva en el mismo índice."
        ),
    )
    cloud: str = Field(default="aws", description="Nube del índice serverless.")
    region: str = Field(
        default="us-east-1",
        description="Región del índice serverless. us-east-1 es la del plan gratuito.",
    )
    metrica: str = Field(
        default="cosine",
        pattern=r"^(cosine|dotproduct|euclidean)$",
        description="Métrica del índice. El modelo de embeddings está entrenado para coseno.",
    )

    # --- Ingesta ------------------------------------------------------------
    carpeta_corpus: Path = Field(
        default=RAIZ / "corpus",
        description="Documentos fuente (.md, .pdf, .json) y su catálogo de metadatos.",
    )
    carpeta_cache_modelos: Path = Field(
        default=RAIZ / ".cache" / "fastembed",
        description="Caché del modelo de embeddings (la misma de la pre-entrega 3).",
    )
    modelo_embeddings: str = Field(
        default=MODELO_EMBEDDINGS,
        description="Modelo de embeddings local. El mismo para indexar y para consultar.",
    )
    chunk_size: int = Field(
        default=500,
        ge=300,
        le=800,
        description="Tokens por fragmento (tiktoken, cl100k_base).",
    )
    chunk_overlap: int = Field(default=60, ge=0, description="Tokens compartidos entre fragmentos.")
    tamano_lote: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Vectores por request de upsert. Pinecone acepta hasta 1000 o 2 MB por request.",
    )

    # --- Recuperación -------------------------------------------------------
    top_k: int = Field(default=5, ge=1, le=20, description="Fragmentos que devuelve una búsqueda.")
    candidatos: int = Field(
        default=10,
        ge=1,
        le=100,
        description=(
            "Fragmentos que trae cada recuperador (vectorial y BM25) antes de fusionarlos. Tiene "
            "que ser al menos top_k: la fusión reordena, no inventa candidatos."
        ),
    )
    peso_vectorial: float = Field(
        default=0.5,
        ge=0,
        le=1,
        description="Peso del recuperador vectorial en la fusión RRF. BM25 lleva 1 - este valor.",
    )
    rrf_c: int = Field(
        default=60,
        ge=1,
        description="Constante de Reciprocal Rank Fusion: 1 / (c + rank). 60 es el valor del paper.",
    )

    @model_validator(mode="after")
    def _coherencia(self) -> "ConfigPinecone":
        if self.chunk_overlap >= self.chunk_size // 2:
            raise ValueError(
                f"chunk_overlap ({self.chunk_overlap}) tiene que ser menor que la mitad de "
                f"chunk_size ({self.chunk_size})"
            )
        if self.candidatos < self.top_k:
            raise ValueError(
                f"candidatos ({self.candidatos}) tiene que ser al menos top_k ({self.top_k})"
            )
        return self

    @property
    def pesos(self) -> list[float]:
        """Pesos para el EnsembleRetriever, en el orden [vectorial, BM25]."""
        return [self.peso_vectorial, round(1 - self.peso_vectorial, 6)]

    def key(self) -> str:
        """La API key en texto plano, solo en el momento de usarla."""
        if self.api_key is None:
            raise ConfigError(
                "falta PINECONE_API_KEY en el .env. Se crea gratis en https://app.pinecone.io "
                "(API Keys) y se pega en el .env; ver .env.example."
            )
        return self.api_key.get_secret_value()


def cargar_config(env_file: str | Path | None = None, **overrides: object) -> ConfigPinecone:
    """Lee el `.env` y devuelve la configuración. `overrides` pisa lo que venga del entorno."""
    load_dotenv(dotenv_path=env_file, override=False)

    def env(nombre: str) -> str | None:
        valor = os.getenv(nombre, "").strip()
        return valor or None

    valores: dict[str, object] = {
        "api_key": env("PINECONE_API_KEY"),
        "indice": env("INDEX_NAME"),
        "namespace": env("PINECONE_NAMESPACE"),
        "cloud": env("PINECONE_CLOUD"),
        "region": env("PINECONE_REGION"),
    }
    valores = {k: v for k, v in valores.items() if v is not None}
    valores.update(overrides)
    return ConfigPinecone(**valores)
