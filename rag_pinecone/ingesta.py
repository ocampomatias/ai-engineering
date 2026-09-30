"""Ingesta a Pinecone: fragmentos -> embeddings -> upsert por lotes, en un namespace.

Decisiones:

- Upsert por lotes de `tamano_lote` vectores (100 por defecto). Un request por
  vector paga la latencia de red cada vez; uno solo con todo el corpus choca
  con el límite de 2 MB por request de Pinecone.
- Incremental, como en la pre-entrega 3: los IDs son deterministas, así que
  antes de calcular embeddings se listan los IDs que ya están en el namespace.
  Solo se suben los fragmentos nuevos y se borran los que ya no existen.
- El texto va en los metadatos (`texto`). La búsqueda devuelve el contenido
  directamente, sin una segunda consulta a otra base.
- Pinecone es eventualmente consistente: un vector recién subido tarda unos
  segundos en aparecer en las búsquedas. La ingesta espera a que el namespace
  tenga la cantidad esperada antes de terminar, para que una evaluación
  corrida justo después no mida un índice a medio llenar.
"""

import logging
import time
from collections.abc import Iterator, Sequence
from typing import Any

from langchain_core.embeddings import Embeddings

from rag.embeddings import EmbeddingsLocales

from .carga import cargar_corpus
from .config import ConfigPinecone
from .infra import asegurar_indice, conectar
from .schemas import MetadatosFragmento, ResumenIngesta

logger = logging.getLogger(__name__)


def crear_embeddings(config: ConfigPinecone) -> Embeddings:
    return EmbeddingsLocales(config.modelo_embeddings, cache_dir=config.carpeta_cache_modelos)


def dimension_de(embeddings: Embeddings) -> int:
    """Dimensión real del modelo, medida en vez de escrita a mano en la configuración."""
    return len(embeddings.embed_query("dimensión"))


def lotes(items: Sequence[Any], tamano: int) -> Iterator[Sequence[Any]]:
    for inicio in range(0, len(items), tamano):
        yield items[inicio : inicio + tamano]


def ids_en_namespace(index: Any, namespace: str) -> set[str]:  # noqa: ANN401
    """Todos los IDs del namespace. `list` pagina de a 100 y solo existe en serverless."""
    ids: set[str] = set()
    for pagina in index.list(namespace=namespace):
        ids.update(pagina)
    return ids


def contar_vectores(index: Any, namespace: str) -> int:  # noqa: ANN401
    stats = index.describe_index_stats()
    info = (stats.namespaces or {}).get(namespace)
    return int(info.vector_count) if info else 0


def esperar_consistencia(
    index: Any,  # noqa: ANN401
    namespace: str,
    esperado: int,
    *,
    timeout_s: float = 60,
    intervalo_s: float = 2,
) -> int:
    """Espera a que el namespace tenga `esperado` vectores. Devuelve cuántos vio."""
    limite = time.monotonic() + timeout_s
    while True:
        actual = contar_vectores(index, namespace)
        if actual == esperado or time.monotonic() > limite:
            if actual != esperado:
                logger.warning(
                    "ingesta: después de %.0f s el namespace tiene %d vectores y se esperaban %d",
                    timeout_s,
                    actual,
                    esperado,
                )
            return actual
        time.sleep(intervalo_s)


def subir_por_lotes(
    index: Any,  # noqa: ANN401
    fragmentos: Sequence[MetadatosFragmento],
    embeddings: Embeddings,
    config: ConfigPinecone,
) -> int:
    """Calcula embeddings y hace upsert de a `tamano_lote`. Devuelve cuántos requests hizo."""
    cantidad = 0
    total = (len(fragmentos) + config.tamano_lote - 1) // config.tamano_lote
    for numero, lote in enumerate(lotes(fragmentos, config.tamano_lote), start=1):
        inicio = time.perf_counter()
        vectores = embeddings.embed_documents([f.texto for f in lote])
        medio = time.perf_counter()
        respuesta = index.upsert(
            vectors=[
                {"id": f.chunk_id, "values": v, "metadata": f.para_pinecone()}
                for f, v in zip(lote, vectores, strict=True)
            ],
            namespace=config.namespace,
        )
        subidos = getattr(respuesta, "upserted_count", len(lote))
        if subidos != len(lote):
            raise RuntimeError(f"Pinecone confirmó {subidos} de {len(lote)} vectores del lote")
        logger.info(
            "ingesta: lote %d/%d: %d vectores (embeddings %.1f s, upsert %.2f s)",
            numero,
            total,
            len(lote),
            medio - inicio,
            time.perf_counter() - medio,
        )
        cantidad += 1
    return cantidad


def ingestar(
    config: ConfigPinecone,
    *,
    cliente: Any = None,  # noqa: ANN401
    embeddings: Embeddings | None = None,
    reiniciar: bool = False,
    esperar: bool = True,
) -> ResumenIngesta:
    """Crea el índice si hace falta y sincroniza el namespace con el corpus.

    Con `reiniciar=True` vacía el namespace y sube todo de cero. Los demás
    namespaces del índice no se tocan.
    """
    inicio = time.perf_counter()
    embeddings = embeddings or crear_embeddings(config)
    cliente = cliente or conectar(config)

    fragmentos = cargar_corpus(config)
    asegurar_indice(cliente, config, dimension_de(embeddings))
    index = cliente.Index(config.indice)

    existentes = ids_en_namespace(index, config.namespace)
    if reiniciar and existentes:
        index.delete(delete_all=True, namespace=config.namespace)
        logger.info(
            "ingesta: namespace '%s' vaciado (%d vectores)", config.namespace, len(existentes)
        )
        if esperar:
            esperar_consistencia(index, config.namespace, 0)
        existentes = set()

    nuevos = {f.chunk_id: f for f in fragmentos}
    a_subir = [f for id_, f in nuevos.items() if id_ not in existentes]
    a_borrar = sorted(existentes - nuevos.keys())

    for lote in lotes(a_borrar, 1000):  # delete acepta hasta 1000 IDs por request
        index.delete(ids=list(lote), namespace=config.namespace)
    if a_borrar:
        logger.info("ingesta: %d fragmento(s) viejos borrados", len(a_borrar))

    cantidad_lotes = subir_por_lotes(index, a_subir, embeddings, config) if a_subir else 0
    if not a_subir and not a_borrar:
        logger.info(
            "ingesta: el namespace '%s' ya tiene los %d fragmentos actuales, no se sube nada",
            config.namespace,
            len(nuevos),
        )
    elif esperar:
        visibles = esperar_consistencia(index, config.namespace, len(nuevos))
        logger.info("ingesta: %d vectores visibles en el namespace", visibles)

    resumen = ResumenIngesta(
        indice=config.indice,
        namespace=config.namespace,
        documentos=len({f.documento_id for f in fragmentos}),
        fragmentos=len(nuevos),
        agregados=len(a_subir),
        eliminados=len(a_borrar),
        lotes=cantidad_lotes,
        reiniciado=reiniciar,
        duracion_ms=(time.perf_counter() - inicio) * 1000,
    )
    logger.info("ingesta terminada en %.1f s", resumen.duracion_ms / 1000)
    return resumen
