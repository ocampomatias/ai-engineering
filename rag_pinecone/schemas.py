"""Esquemas del RAG en Pinecone.

`MetadatosFragmento` es la interfaz de metadatos: todos los fragmentos se suben
con exactamente estos campos y estos tipos, vengan de un Markdown, un PDF o un
JSON. Si un campo cambia de nombre en un proceso y no en otro (`fecha` en uno,
`actualizado` en otro), los filtros dejan de encontrar documentos sin dar
ningún error. Con `extra="forbid"` ese cambio falla al ingestar.
"""

import re
from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Categoria = Literal["arquitectura", "procesos", "operaciones", "seguridad", "producto", "comercial"]
Audiencia = Literal["interna", "comercios"]
TipoDocumento = Literal["markdown", "pdf", "json"]

_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

#: Clave de metadatos donde va el texto del fragmento. PineconeVectorStore la
#: saca de los metadatos y la usa como `page_content`.
CLAVE_TEXTO = "texto"


class MetadatosDocumento(BaseModel):
    """Lo que el catálogo (`corpus/_metadatos.json`) dice de cada archivo."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    categoria: Categoria = Field(description="Área a la que pertenece el documento.")
    audiencia: Audiencia = Field(description="Quién puede leerlo: el equipo o los comercios.")
    etiquetas: tuple[str, ...] = Field(
        min_length=1, description="Temas del documento, en minúsculas y con guiones."
    )
    actualizado: date = Field(description="Fecha de la última revisión del documento.")

    @field_validator("etiquetas")
    @classmethod
    def _etiquetas_slug(cls, valores: tuple[str, ...]) -> tuple[str, ...]:
        malas = [v for v in valores if not _SLUG.match(v)]
        if malas:
            raise ValueError(f"etiquetas que no son slugs (minúsculas y guiones): {malas}")
        return tuple(dict.fromkeys(valores))


class MetadatosFragmento(BaseModel):
    """Metadatos de cada vector en Pinecone. El texto original viaja acá adentro.

    Guardar el texto en los metadatos evita una segunda consulta a otra base
    para recuperar el contenido: la búsqueda ya devuelve lo que va al LLM.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str = Field(pattern=r"^[a-z0-9-]+#[0-9a-f]{12}$", description="ID del vector.")
    documento_id: str = Field(pattern=_SLUG.pattern, description="Nombre del archivo sin extensión.")
    fuente: str = Field(description="Nombre del archivo, con extensión.")
    tipo: TipoDocumento
    categoria: Categoria
    audiencia: Audiencia
    etiquetas: list[str]
    actualizado: int = Field(
        ge=19000101,
        le=29991231,
        description=(
            "Fecha como AAAAMMDD. Es un número y no un string porque Pinecone solo compara "
            "números con $gt / $lt: así se puede filtrar 'actualizado después de tal fecha'."
        ),
    )
    pagina: int | None = Field(default=None, ge=1, description="Página del PDF. Solo en PDFs.")
    seccion: str = Field(description="Título de la sección, o el código de error en el JSON.")
    chunk: int = Field(ge=0, description="Posición del fragmento dentro del documento.")
    total_chunks: int = Field(ge=1)
    tokens: int = Field(ge=1, description="Tamaño del fragmento en tokens de tiktoken.")
    texto: str = Field(min_length=1, description="Contenido del fragmento.")

    @model_validator(mode="after")
    def _coherencia(self) -> Self:
        if not self.chunk_id.startswith(f"{self.documento_id}#"):
            raise ValueError("el chunk_id tiene que empezar con el documento_id")
        if self.chunk >= self.total_chunks:
            raise ValueError(f"chunk {self.chunk} fuera de rango (total {self.total_chunks})")
        if (self.tipo == "pdf") != (self.pagina is not None):
            raise ValueError("la página va en los PDFs y solo en los PDFs")
        return self

    def para_pinecone(self) -> dict[str, object]:
        """Los metadatos como los acepta Pinecone, que no admite null: sin página se omite."""
        return self.model_dump(exclude_none=True)


class ResumenIngesta(BaseModel):
    """Qué hizo una corrida de la ingesta."""

    indice: str
    namespace: str
    documentos: int = Field(ge=0)
    fragmentos: int = Field(ge=0, description="Fragmentos que tiene el corpus actual.")
    agregados: int = Field(ge=0, description="Fragmentos nuevos subidos a Pinecone.")
    eliminados: int = Field(ge=0, description="Fragmentos viejos borrados de Pinecone.")
    lotes: int = Field(ge=0, description="Requests de upsert que se hicieron.")
    reiniciado: bool = False
    duracion_ms: float = Field(ge=0)

    @property
    def sin_cambios(self) -> bool:
        return self.agregados == 0 and self.eliminados == 0
