"""Esquemas del sistema RAG, validados con Pydantic v2.

Hay dos niveles:

- `RespuestaLLM` es lo que el modelo tiene que devolver. La lee el
  `PydanticOutputParser` de la cadena, y sus `description` son lo que el modelo
  ve como instrucciones de formato.
- `RespuestaRAG` es lo que recibe quien llama a `get_rag_response`. Las
  referencias no las escribe el modelo: el modelo solo cita IDs de fragmento
  (`F1`, `F2`...) y la cadena los reemplaza por la fuente real, la sección y la
  similitud que devolvió ChromaDB. Así una referencia no puede ser inventada.

Además de tipos y rangos, hay validaciones propias: normalización de citas,
coherencia entre `encontrada`, la respuesta y las citas, y (con contexto de
validación) que el modelo solo cite fragmentos que realmente se recuperaron.
"""

import re
from pathlib import PurePath
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

#: Respuesta canónica cuando el contexto no alcanza. La consigna pide que el
#: modelo diga "No lo sé"; el esquema la fija para que sea fácil de detectar.
NO_LO_SE = "No lo sé."

_PATRON_ID = re.compile(r"^F[1-9]\d*$")
_EXTENSIONES_FUENTE = (".md", ".txt")


def _normalizar_id(valor: object) -> object:
    """Acepta variantes que los modelos suelen escribir: 'f2', ' F2 ', '[F2]'."""
    if isinstance(valor, str):
        return valor.strip().strip("[]()").strip().upper()
    return valor


def _es_no_lo_se(texto: str) -> bool:
    return texto.strip().rstrip(".!").strip().casefold() == NO_LO_SE.rstrip(".").casefold()


# --- Salida del modelo -----------------------------------------------------


class RespuestaLLM(BaseModel):
    """Respuesta del modelo a una pregunta, fundamentada en los fragmentos del contexto."""

    model_config = ConfigDict(extra="forbid")

    encontrada: bool = Field(
        description=(
            "true si los fragmentos del CONTEXTO contienen la información para responder. false "
            "si no la contienen, aunque sepas la respuesta por otro lado."
        ),
    )
    respuesta: str = Field(
        min_length=1,
        max_length=2000,
        description=(
            "Respuesta en español, basada solo en el CONTEXTO. Si encontrada es false, tiene que "
            f"ser exactamente '{NO_LO_SE}'"
        ),
    )
    fragmentos_citados: list[str] = Field(
        default_factory=list,
        max_length=5,
        description=(
            "IDs de los fragmentos del CONTEXTO que usaste para responder, por ejemplo "
            "['F1', 'F3']. Vacía si encontrada es false."
        ),
    )

    @field_validator("respuesta")
    @classmethod
    def _recortar_respuesta(cls, valor: str) -> str:
        valor = valor.strip()
        if not valor:
            raise ValueError("la respuesta no puede estar vacía")
        return valor

    @field_validator("fragmentos_citados", mode="before")
    @classmethod
    def _normalizar_citas(cls, valores: object) -> object:
        if isinstance(valores, str):  # "F1, F3" en vez de una lista
            valores = [v for v in re.split(r"[,\s]+", valores) if v]
        if isinstance(valores, list):
            return list(dict.fromkeys(_normalizar_id(v) for v in valores))  # sin repetidos
        return valores

    @field_validator("fragmentos_citados")
    @classmethod
    def _citas_con_formato_y_existentes(cls, valores: list[str], info: ValidationInfo) -> list[str]:
        malos = [v for v in valores if not _PATRON_ID.match(v)]
        if malos:
            raise ValueError(
                f"IDs de fragmento con formato inválido: {malos} (se espera F1, F2...)"
            )

        # Con contexto de validación, cada cita tiene que ser un fragmento que se
        # le pasó al modelo. Citar 'F7' cuando hubo 4 es una referencia inventada.
        validos = (info.context or {}).get("ids_recuperados")
        if validos is not None:
            inventados = [v for v in valores if v not in validos]
            if inventados:
                raise ValueError(
                    f"el modelo citó fragmentos que no estaban en el contexto: {inventados} "
                    f"(recuperados: {sorted(validos)})"
                )
        return valores

    @model_validator(mode="after")
    def _coherencia(self) -> Self:
        if self.encontrada:
            if _es_no_lo_se(self.respuesta):
                raise ValueError("encontrada es true pero la respuesta es 'No lo sé'")
            if not self.fragmentos_citados:
                raise ValueError("una respuesta encontrada tiene que citar al menos un fragmento")
        else:
            if self.fragmentos_citados:
                raise ValueError("si encontrada es false no se pueden citar fragmentos")
            if not _es_no_lo_se(self.respuesta):
                raise ValueError(
                    f"si encontrada es false la respuesta tiene que ser '{NO_LO_SE}', "
                    f"no {self.respuesta[:60]!r}"
                )
            # "no lo sé", "No lo sé!" y "No lo sé." son lo mismo: se guarda la forma canónica.
            self.respuesta = NO_LO_SE
        return self


# --- Recuperación ----------------------------------------------------------


class FragmentoRecuperado(BaseModel):
    """Un fragmento que devolvió ChromaDB para una consulta, con su similitud."""

    model_config = ConfigDict(frozen=True)

    id_fragmento: str = Field(
        pattern=_PATRON_ID.pattern,
        description="ID corto que ve el modelo en el prompt: F1 es el más parecido a la pregunta.",
    )
    chunk_id: str = Field(min_length=1, description="ID del fragmento en la colección de ChromaDB.")
    fuente: str = Field(min_length=1, description="Nombre del archivo de /data de donde sale.")
    seccion: str | None = Field(
        default=None, description="Último título de Markdown antes del fragmento, si lo hay."
    )
    contenido: str = Field(min_length=1, description="Texto del fragmento.")
    similitud: float = Field(
        ge=0.0,
        le=1.0,
        description="Similitud coseno con la pregunta, de 0 (nada que ver) a 1 (idéntico).",
    )

    @field_validator("similitud", mode="before")
    @classmethod
    def _acotar_similitud(cls, valor: object) -> object:
        # Por redondeo, Chroma puede devolver 1.0000001 o -0.0000001 para textos
        # idénticos u opuestos. Es el mismo valor, no un error.
        if isinstance(valor, float) and -1e-6 < valor < 0:
            return 0.0
        if isinstance(valor, float) and 1 < valor < 1 + 1e-6:
            return 1.0
        return valor


# --- Respuesta final -------------------------------------------------------


class Referencia(BaseModel):
    """Fuente que respalda una respuesta. Sale de ChromaDB, no del modelo."""

    model_config = ConfigDict(extra="forbid")

    id_fragmento: str = Field(
        pattern=_PATRON_ID.pattern, description="ID con el que el modelo citó el fragmento."
    )
    fuente: str = Field(min_length=1, description="Archivo de /data que contiene el fragmento.")
    seccion: str | None = Field(default=None, description="Sección del documento, si la hay.")
    similitud: float = Field(
        ge=0.0, le=1.0, description="Similitud coseno entre la pregunta y el fragmento."
    )
    extracto: str = Field(
        min_length=1,
        max_length=300,
        description="Primeros caracteres del fragmento, para ubicarlo sin abrir el archivo.",
    )

    @field_validator("fuente")
    @classmethod
    def _fuente_es_archivo_de_datos(cls, valor: str) -> str:
        # Solo el nombre del archivo: una ruta absoluta en la respuesta filtraría la
        # estructura de carpetas de la máquina donde corre el sistema.
        nombre = PurePath(valor).name
        if nombre != valor:
            raise ValueError(
                f"la fuente tiene que ser un nombre de archivo, no una ruta: {valor!r}"
            )
        if not nombre.lower().endswith(_EXTENSIONES_FUENTE):
            raise ValueError(f"la fuente tiene que ser un .md o .txt: {valor!r}")
        return nombre

    @field_validator("similitud")
    @classmethod
    def _redondear_similitud(cls, valor: float) -> float:
        return round(valor, 4)

    @classmethod
    def desde_fragmento(cls, fragmento: FragmentoRecuperado) -> "Referencia":
        extracto = " ".join(fragmento.contenido.split())
        if len(extracto) > 300:
            extracto = extracto[:297].rstrip() + "..."
        return cls(
            id_fragmento=fragmento.id_fragmento,
            fuente=fragmento.fuente,
            seccion=fragmento.seccion,
            similitud=fragmento.similitud,
            extracto=extracto,
        )


class Metricas(BaseModel):
    """Tiempos y conteos de una ejecución de `get_rag_response`."""

    model_config = ConfigDict(extra="forbid")

    recuperacion_ms: float = Field(ge=0, description="Tiempo de la búsqueda en ChromaDB.")
    generacion_ms: float = Field(
        ge=0, description="Tiempo sumado de las llamadas al LLM (incluye reintentos)."
    )
    total_ms: float = Field(ge=0, description="Tiempo total de punta a punta.")
    intentos_llm: int = Field(
        ge=1, description="Llamadas al LLM. Más de 1 significa que hubo reintentos."
    )
    fragmentos_recuperados: int = Field(ge=0, description="Fragmentos que se le pasaron al modelo.")

    @field_validator("recuperacion_ms", "generacion_ms", "total_ms")
    @classmethod
    def _redondear(cls, valor: float) -> float:
        return round(valor, 1)

    @model_validator(mode="after")
    def _total_cubre_las_partes(self) -> Self:
        # Las etapas corren una después de la otra: el total no puede ser menor que
        # su suma. Si lo es, el cronómetro está mal puesto. Se tolera 1 ms por redondeo.
        partes = self.recuperacion_ms + self.generacion_ms
        if self.total_ms + 1 < partes:
            raise ValueError(
                f"total_ms ({self.total_ms}) es menor que recuperación + generación ({partes})"
            )
        return self


class RespuestaRAG(BaseModel):
    """Salida de `get_rag_response`: respuesta, si se encontró y de dónde sale."""

    model_config = ConfigDict(extra="forbid")

    pregunta: str = Field(
        min_length=1, description="La pregunta tal como llegó, sin espacios extra."
    )
    respuesta: str = Field(min_length=1, description=f"Respuesta fundamentada, o '{NO_LO_SE}'.")
    encontrada: bool = Field(description="Si los documentos contenían la respuesta.")
    referencias: list[Referencia] = Field(
        default_factory=list,
        description="Fragmentos citados por el modelo, con su fuente. Vacía si no se encontró.",
    )
    metricas: Metricas | None = Field(
        default=None, description="Tiempos de la ejecución. None si la cadena se usó suelta."
    )

    @field_validator("pregunta")
    @classmethod
    def _recortar_pregunta(cls, valor: str) -> str:
        return " ".join(valor.split())

    @model_validator(mode="after")
    def _coherencia(self) -> Self:
        if self.encontrada and not self.referencias:
            raise ValueError("una respuesta encontrada necesita al menos una referencia")
        if not self.encontrada and (self.referencias or self.respuesta != NO_LO_SE):
            raise ValueError(
                f"sin respuesta encontrada tiene que ser '{NO_LO_SE}' y sin referencias"
            )
        ids = [r.id_fragmento for r in self.referencias]
        if len(ids) != len(set(ids)):
            raise ValueError(f"referencias repetidas: {ids}")
        return self


# --- Ingesta ---------------------------------------------------------------


class ResumenIngesta(BaseModel):
    """Qué hizo una corrida de ingesta."""

    model_config = ConfigDict(extra="forbid")

    documentos: int = Field(ge=0, description="Archivos leídos de /data.")
    fragmentos: int = Field(ge=0, description="Fragmentos que tiene que haber en la colección.")
    agregados: int = Field(ge=0, description="Fragmentos nuevos o modificados que se indexaron.")
    eliminados: int = Field(ge=0, description="Fragmentos viejos que se borraron de la colección.")
    reiniciada: bool = Field(description="Si se borró la colección entera antes de indexar.")
    duracion_ms: float = Field(ge=0, description="Tiempo total de la ingesta.")

    @field_validator("duracion_ms")
    @classmethod
    def _redondear(cls, valor: float) -> float:
        return round(valor, 1)

    @property
    def sin_cambios(self) -> bool:
        return self.agregados == 0 and self.eliminados == 0

    @model_validator(mode="after")
    def _agregados_no_superan_total(self) -> Self:
        if self.agregados > self.fragmentos:
            raise ValueError("no se pueden agregar más fragmentos de los que hay")
        return self
