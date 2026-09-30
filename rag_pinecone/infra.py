"""Infraestructura en Pinecone: crear el índice serverless si no existe y validarlo.

Si el índice ya existe, se comprueba que su dimensión y su métrica coincidan con
las del modelo de embeddings. Subir un vector de 768 dimensiones a un índice de
1536 falla recién en el upsert. Si el índice es de 768 pero se creó con otro
modelo, no falla nunca: las búsquedas devuelven resultados que parecen
normales y son ruido. Por eso el índice guarda en sus tags qué modelo lo creó.
"""

import logging
import re
import time
from typing import Any

from .config import ConfigPinecone

logger = logging.getLogger(__name__)

TAG_MODELO = "embeddings"
TAG_PROYECTO = "proyecto"


class IndiceError(Exception):
    """El índice no existe, no está listo o no es compatible con el modelo de embeddings."""


def _valor_tag(texto: str) -> str:
    # Los tags de Pinecone aceptan letras, números, '_' y '-'. El modelo tiene '/' y '.'.
    return re.sub(r"[^A-Za-z0-9_-]", "_", texto)[:120]


def conectar(config: ConfigPinecone) -> Any:  # noqa: ANN401 - el cliente se importa tarde
    """Cliente de Pinecone. Se importa acá para que los tests no necesiten el SDK."""
    from pinecone import Pinecone

    return Pinecone(api_key=config.key(), source_tag="coderhouse_ai_engineering")


def _esperar_listo(cliente: Any, nombre: str, timeout_s: float) -> Any:  # noqa: ANN401
    limite = time.monotonic() + timeout_s
    while True:
        descripcion = cliente.describe_index(nombre)
        if descripcion.status["ready"]:
            return descripcion
        if time.monotonic() > limite:
            raise IndiceError(f"el índice '{nombre}' no quedó listo en {timeout_s:.0f} s")
        time.sleep(2)


def asegurar_indice(
    cliente: Any,  # noqa: ANN401
    config: ConfigPinecone,
    dimension: int,
    *,
    timeout_s: float = 120,
) -> bool:
    """Crea el índice serverless si no existe y valida el que haya. Devuelve si lo creó."""
    from pinecone import ServerlessSpec

    creado = False
    if not cliente.has_index(config.indice):
        logger.info(
            "infra: creando índice serverless '%s' (%s/%s, dimensión %d, métrica %s)",
            config.indice,
            config.cloud,
            config.region,
            dimension,
            config.metrica,
        )
        cliente.create_index(
            name=config.indice,
            dimension=dimension,
            metric=config.metrica,
            spec=ServerlessSpec(cloud=config.cloud, region=config.region),
            tags={
                TAG_MODELO: _valor_tag(config.modelo_embeddings),
                TAG_PROYECTO: "tambor-rag",
            },
        )
        creado = True

    descripcion = _esperar_listo(cliente, config.indice, timeout_s)
    validar_indice(descripcion, config, dimension)
    logger.info(
        "infra: índice '%s' listo (dimensión %d, métrica %s, host %s)",
        config.indice,
        descripcion.dimension,
        descripcion.metric,
        descripcion.host,
    )
    return creado


def validar_indice(descripcion: Any, config: ConfigPinecone, dimension: int) -> None:  # noqa: ANN401
    """Falla si el índice no sirve para este modelo de embeddings."""
    if descripcion.dimension != dimension:
        raise IndiceError(
            f"el índice '{config.indice}' tiene dimensión {descripcion.dimension} y el modelo "
            f"{config.modelo_embeddings} genera vectores de {dimension}. Usá otro INDEX_NAME o "
            "borrá el índice desde la consola de Pinecone."
        )
    if descripcion.metric != config.metrica:
        raise IndiceError(
            f"el índice '{config.indice}' usa la métrica {descripcion.metric} y la configuración "
            f"pide {config.metrica}"
        )
    # El SDK devuelve los tags como un modelo propio (IndexTags), no como dict.
    tags = descripcion.tags or {}
    tags = tags.to_dict() if hasattr(tags, "to_dict") else dict(tags)
    modelo = tags.get(TAG_MODELO)
    esperado = _valor_tag(config.modelo_embeddings)
    if modelo is not None and modelo != esperado:
        raise IndiceError(
            f"el índice '{config.indice}' se creó para el modelo {modelo!r} y se está usando "
            f"{esperado!r}: los vectores de modelos distintos no se pueden comparar"
        )
    if modelo is None:
        logger.warning(
            "infra: el índice '%s' no tiene el tag '%s'; no se puede comprobar con qué modelo "
            "se indexó",
            config.indice,
            TAG_MODELO,
        )


def abrir_indice(cliente: Any, config: ConfigPinecone) -> Any:  # noqa: ANN401
    """Abre un índice que ya tiene que existir (el modo de consulta)."""
    if not cliente.has_index(config.indice):
        raise IndiceError(
            f"no existe el índice '{config.indice}'. Corré primero `python ingestar_pinecone.py`."
        )
    return cliente.Index(config.indice)
