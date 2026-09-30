"""Métricas de recuperación sobre un golden set. Funciones puras, sin Pinecone.

Para cada pregunta del golden set se sabe de antemano qué documento tiene la
respuesta (`documento_id_esperado`) y un texto que aparece en el fragmento
exacto (`contiene`). Con los k fragmentos recuperados:

- Recall@k: 1 si al menos un fragmento es del documento esperado. Como cada
  pregunta tiene un solo documento relevante, es la fracción de preguntas para
  las que el contexto necesario llega al LLM.
- Precision@k: fracción de los k fragmentos que son del documento esperado. Se
  divide por k aunque se hayan recuperado menos: un hueco no es un acierto.
- MRR: 1 / posición del primer fragmento del documento esperado (0 si no está).
  Distingue "llegó primero" de "llegó quinto", que Recall@k no ve.
- Acierto de fragmento@k: 1 si alguno de los k fragmentos contiene el texto
  exacto de la respuesta. Es más exigente que Recall@k: el documento puede
  estar y el fragmento que responde no.
"""

import re
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PreguntaGolden(BaseModel):
    """Una pregunta del golden set con su respuesta conocida."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9-]+$")
    pregunta: str = Field(min_length=5)
    documento_id_esperado: str = Field(pattern=r"^[a-z0-9-]+$")
    contiene: str = Field(
        min_length=2, description="Texto que aparece en el fragmento que responde la pregunta."
    )
    tipo: str = Field(
        pattern=r"^(lexica|semantica)$",
        description=(
            "lexica: la pregunta nombra un código o un identificador exacto. semantica: la "
            "pregunta usa otras palabras que el documento."
        ),
    )


class GoldenSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    descripcion: str
    preguntas: tuple[PreguntaGolden, ...] = Field(min_length=5)

    @field_validator("preguntas")
    @classmethod
    def _ids_unicos(cls, preguntas: tuple[PreguntaGolden, ...]) -> tuple[PreguntaGolden, ...]:
        ids = [p.id for p in preguntas]
        if len(ids) != len(set(ids)):
            raise ValueError("hay preguntas con el mismo id en el golden set")
        return preguntas


class Recuperado(BaseModel):
    """Lo mínimo de un fragmento recuperado que hace falta para medirlo."""

    documento_id: str
    texto: str


class MetricasPregunta(BaseModel):
    id: str
    recall: float = Field(ge=0, le=1)
    precision: float = Field(ge=0, le=1)
    reciprocal_rank: float = Field(ge=0, le=1)
    acierto_fragmento: float = Field(ge=0, le=1)
    posicion: int | None = Field(description="Posición del primer fragmento del documento esperado.")
    documentos: list[str] = Field(description="documento_id de cada fragmento recuperado, en orden.")


class MetricasAgregadas(BaseModel):
    k: int
    preguntas: int
    recall: float
    precision: float
    f1: float = Field(description="Media armónica de la precisión y el recall promedio.")
    mrr: float
    acierto_fragmento: float


def _normalizar(texto: str) -> str:
    return re.sub(r"\s+", " ", texto).strip().casefold()


def medir_pregunta(
    pregunta: PreguntaGolden, recuperados: Sequence[Recuperado], k: int
) -> MetricasPregunta:
    top = list(recuperados)[:k]
    relevantes = [i for i, r in enumerate(top, start=1) if r.documento_id == pregunta.documento_id_esperado]
    posicion = relevantes[0] if relevantes else None
    buscado = _normalizar(pregunta.contiene)
    return MetricasPregunta(
        id=pregunta.id,
        recall=1.0 if relevantes else 0.0,
        precision=len(relevantes) / k,
        reciprocal_rank=1 / posicion if posicion else 0.0,
        acierto_fragmento=1.0 if any(buscado in _normalizar(r.texto) for r in top) else 0.0,
        posicion=posicion,
        documentos=[r.documento_id for r in top],
    )


def agregar(resultados: Sequence[MetricasPregunta], k: int) -> MetricasAgregadas:
    if not resultados:
        raise ValueError("no hay resultados para agregar")
    n = len(resultados)

    def promedio(campo: str) -> float:
        return sum(getattr(r, campo) for r in resultados) / n

    recall, precision = promedio("recall"), promedio("precision")
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return MetricasAgregadas(
        k=k,
        preguntas=n,
        recall=round(recall, 4),
        precision=round(precision, 4),
        f1=round(f1, 4),
        mrr=round(promedio("reciprocal_rank"), 4),
        acierto_fragmento=round(promedio("acierto_fragmento"), 4),
    )


def precision_maxima(pregunta: PreguntaGolden, fragmentos_por_documento: dict[str, int], k: int) -> float:
    """El techo de Precision@k: si el documento tiene 3 fragmentos, con k=5 no pasa de 0,6."""
    return min(k, fragmentos_por_documento.get(pregunta.documento_id_esperado, 0)) / k
