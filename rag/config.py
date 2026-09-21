"""Parámetros del sistema RAG, validados con Pydantic.

Viven en un solo objeto porque la ingesta y la consulta tienen que coincidir:
misma carpeta, misma colección y, sobre todo, el mismo modelo de embeddings.
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RAIZ = Path(__file__).resolve().parent.parent

#: Modelo de embeddings local (ONNX, corre en CPU, sin API key). Es bilingüe
#: español-inglés y acepta hasta 8192 tokens de entrada, así que un chunk de 500
#: tokens entra entero. Uno como all-MiniLM-L6-v2 lo truncaría a 256.
MODELO_EMBEDDINGS = "jinaai/jina-embeddings-v2-base-es"


class ConfigRAG(BaseModel):
    """Configuración de ingesta y recuperación."""

    model_config = ConfigDict(frozen=True)

    carpeta_datos: Path = Field(
        default=RAIZ / "data",
        description="Carpeta con los documentos fuente (.md y .txt).",
    )
    carpeta_vectorstore: Path = Field(
        default=RAIZ / "vectorstore",
        description="Carpeta donde ChromaDB persiste la colección en disco.",
    )
    carpeta_cache_modelos: Path = Field(
        default=RAIZ / ".cache" / "fastembed",
        description="Dónde se guarda el modelo de embeddings descargado, para no bajarlo cada vez.",
    )
    coleccion: str = Field(
        default="tambor_docs",
        min_length=3,
        max_length=63,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*[a-zA-Z0-9]$",
        description="Nombre de la colección de ChromaDB (3 a 63 caracteres, reglas de Chroma).",
    )
    modelo_embeddings: str = Field(
        default=MODELO_EMBEDDINGS,
        description="Modelo de embeddings. El mismo para indexar y para consultar.",
    )
    chunk_size: int = Field(
        default=500,
        ge=500,
        le=2000,
        description="Tamaño máximo de cada fragmento, en tokens de tiktoken (cl100k_base).",
    )
    chunk_overlap: int = Field(
        default=50,
        ge=50,
        description="Tokens compartidos entre fragmentos consecutivos.",
    )
    top_k: int = Field(
        default=4,
        ge=3,
        le=5,
        description="Fragmentos que se recuperan por consulta. Entre 3 y 5, como pide la consigna.",
    )
    extensiones: tuple[str, ...] = Field(
        default=(".md", ".txt"),
        min_length=1,
        description="Extensiones de archivo que se ingestan.",
    )

    @field_validator("extensiones")
    @classmethod
    def _normalizar_extensiones(cls, valores: tuple[str, ...]) -> tuple[str, ...]:
        normalizadas = tuple(
            dict.fromkeys(
                v.strip().lower() if v.strip().startswith(".") else f".{v.strip().lower()}"
                for v in valores
                if v.strip()
            )
        )
        if not normalizadas:
            raise ValueError("hace falta al menos una extensión")
        return normalizadas

    @model_validator(mode="after")
    def _overlap_menor_que_chunk(self) -> "ConfigRAG":
        # Con un overlap de la mitad o más, cada fragmento repite más de lo que aporta
        # y el splitter puede no avanzar.
        if self.chunk_overlap >= self.chunk_size // 2:
            raise ValueError(
                f"chunk_overlap ({self.chunk_overlap}) tiene que ser menor que la mitad de "
                f"chunk_size ({self.chunk_size})"
            )
        return self
