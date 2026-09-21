"""Ingesta: leer /data, limpiar, fragmentar y persistir en ChromaDB.

    cargar_documentos -> limpiar_texto -> fragmentar -> ChromaDB (en disco)

Decisiones:

- El chunking mide en tokens (tiktoken, `cl100k_base`), no en caracteres: 500
  tokens con 50 de solapamiento. Los separadores son los de Markdown, así que
  el splitter prueba cortar primero por títulos, después por párrafos, líneas
  y palabras.
- El ID de cada fragmento se calcula a partir de su contenido. Reindexar el
  mismo documento da los mismos IDs, y eso permite saber qué cambió sin
  recalcular embeddings.
- La ingesta es idempotente: si la colección ya tiene exactamente esos
  fragmentos, no hace nada. Si un archivo cambió, agrega los fragmentos
  nuevos y borra los viejos de ese archivo.
- La colección guarda qué modelo de embeddings la creó. Consultarla con otro
  modelo es un error, no una búsqueda que devuelve ruido.
"""

import hashlib
import logging
import re
import time
import unicodedata

import chromadb
from chromadb.config import Settings as ChromaSettings
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_text_splitters import Language, RecursiveCharacterTextSplitter

from .config import ConfigRAG
from .embeddings import EmbeddingsLocales
from .schemas import ResumenIngesta

logger = logging.getLogger(__name__)

ENCODING_TIKTOKEN = "cl100k_base"
CLAVE_MODELO = "modelo_embeddings"

_TITULO_MD = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)


class IngestaError(Exception):
    """No hay documentos para indexar, o la carpeta de datos no existe."""


class BaseVectorialError(Exception):
    """La colección no existe, está vacía o se creó con otro modelo de embeddings."""


# --- Limpieza --------------------------------------------------------------


def limpiar_texto(texto: str) -> str:
    """Normaliza el texto antes de fragmentarlo.

    Unifica Unicode (una 'á' puede venir como un carácter o como 'a' + tilde, y
    para el tokenizador son distintas), saca caracteres de control que deja una
    mala extracción, espacios al final de línea y saltos de línea de más.
    """
    texto = unicodedata.normalize("NFC", texto)
    texto = texto.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    texto = texto.replace("\u00a0", " ").replace("\ufeff", "")
    texto = "".join(c for c in texto if c == "\n" or unicodedata.category(c) != "Cc")
    texto = re.sub(r"[ ]+\n", "\n", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


# --- Carga -----------------------------------------------------------------


def cargar_documentos(config: ConfigRAG) -> list[Document]:
    """Lee los .md y .txt de la carpeta de datos, limpios y con su nombre y título."""
    carpeta = config.carpeta_datos
    if not carpeta.is_dir():
        raise IngestaError(f"no existe la carpeta de datos: {carpeta}")

    archivos = sorted(
        p for p in carpeta.iterdir() if p.is_file() and p.suffix.lower() in config.extensiones
    )
    documentos: list[Document] = []
    for archivo in archivos:
        texto = limpiar_texto(archivo.read_text(encoding="utf-8"))
        if not texto:
            logger.warning("ingesta: %s está vacío, se saltea", archivo.name)
            continue
        titulo = _TITULO_MD.search(texto)
        documentos.append(
            Document(
                page_content=texto,
                metadata={
                    "fuente": archivo.name,
                    "titulo": titulo.group(2) if titulo else archivo.stem,
                },
            )
        )

    if not documentos:
        extensiones = ", ".join(config.extensiones)
        raise IngestaError(f"no hay documentos ({extensiones}) con contenido en {carpeta}")
    logger.info("ingesta: %d documento(s) leídos de %s", len(documentos), carpeta.name)
    return documentos


# --- Fragmentación ---------------------------------------------------------


def crear_splitter(config: ConfigRAG) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name=ENCODING_TIKTOKEN,
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
        # Son expresiones regulares (por ejemplo "\n#{1,6} " para los títulos). Sin
        # is_separator_regex se buscan como texto literal y nunca se corta en un título.
        separators=RecursiveCharacterTextSplitter.get_separators_for_language(Language.MARKDOWN),
        is_separator_regex=True,
    )


def _posiciones(texto: str, partes: list[str]) -> list[int]:
    """Dónde empieza cada fragmento dentro del texto original (-1 si no aparece).

    No se usa `add_start_index` del splitter: resta el solapamiento como si fueran
    caracteres, pero acá son tokens, y con 50 tokens (unos 200 caracteres) la
    búsqueda arranca después del inicio real y devuelve -1. Como los fragmentos
    salen en orden, alcanza con buscar cada uno a partir del anterior.
    """
    posiciones: list[int] = []
    desde = 0
    for parte in partes:
        posicion = texto.find(parte, desde)
        posiciones.append(posicion)
        if posicion >= 0:
            desde = posicion + 1
    return posiciones


def _seccion_en(texto: str, posicion: int) -> str | None:
    """Último título de Markdown que aparece antes de `posicion` (o justo en ella)."""
    seccion = None
    for titulo in _TITULO_MD.finditer(texto):
        if titulo.start() > posicion:
            break
        seccion = titulo.group(2)
    return seccion


def id_de_fragmento(fuente: str, contenido: str) -> str:
    """ID determinista: mismo archivo y mismo texto dan el mismo ID."""
    huella = hashlib.sha256(f"{fuente}\x00{contenido}".encode()).hexdigest()[:16]
    return f"{fuente}:{huella}"


def fragmentar(documentos: list[Document], config: ConfigRAG) -> list[Document]:
    """Parte cada documento en fragmentos y les agrega metadatos para citar la fuente."""
    splitter = crear_splitter(config)
    contar = splitter._length_function  # el mismo contador de tokens que usa el splitter
    fragmentos: list[Document] = []

    for documento in documentos:
        partes = splitter.split_documents([documento])
        inicios = _posiciones(documento.page_content, [p.page_content for p in partes])
        for indice, (parte, inicio) in enumerate(zip(partes, inicios, strict=True)):
            seccion = _seccion_en(documento.page_content, inicio) if inicio >= 0 else None
            parte.id = id_de_fragmento(parte.metadata["fuente"], parte.page_content)
            parte.metadata.update(
                chunk=indice,
                total_chunks=len(partes),
                tokens=contar(parte.page_content),
                # Chroma no acepta None en metadatos: sin sección va un string vacío.
                seccion=seccion or "",
            )
            fragmentos.append(parte)

    tokens = [f.metadata["tokens"] for f in fragmentos]
    logger.info(
        "ingesta: %d fragmento(s), entre %d y %d tokens (tope %d, solapamiento %d)",
        len(fragmentos),
        min(tokens),
        max(tokens),
        config.chunk_size,
        config.chunk_overlap,
    )
    return fragmentos


# --- ChromaDB --------------------------------------------------------------


def crear_embeddings(config: ConfigRAG) -> Embeddings:
    return EmbeddingsLocales(config.modelo_embeddings, cache_dir=config.carpeta_cache_modelos)


def _cliente(config: ConfigRAG) -> chromadb.ClientAPI:
    config.carpeta_vectorstore.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(
        path=str(config.carpeta_vectorstore),
        settings=ChromaSettings(anonymized_telemetry=False),
    )


def _coleccion_existe(cliente: chromadb.ClientAPI, nombre: str) -> bool:
    return nombre in {c.name for c in cliente.list_collections()}


def abrir_vectorstore(
    config: ConfigRAG,
    embeddings: Embeddings | None = None,
    *,
    crear: bool = False,
) -> Chroma:
    """Abre la colección persistida en disco.

    Con `crear=False` (el modo de consulta) falla si la colección no existe o
    está vacía, en vez de devolver una búsqueda sin resultados. En los dos
    modos falla si la colección se creó con otro modelo de embeddings.
    """
    cliente = _cliente(config)
    existe = _coleccion_existe(cliente, config.coleccion)

    if existe:
        metadata = cliente.get_collection(config.coleccion).metadata or {}
        modelo_guardado = metadata.get(CLAVE_MODELO)
        if modelo_guardado != config.modelo_embeddings:
            raise BaseVectorialError(
                f"la colección '{config.coleccion}' se indexó con {modelo_guardado!r} y se está "
                f"usando {config.modelo_embeddings!r}. Los vectores de modelos distintos no se "
                "pueden comparar: reindexá con `python ingestar.py --reiniciar`."
            )
    elif not crear:
        raise BaseVectorialError(
            f"no existe la colección '{config.coleccion}' en {config.carpeta_vectorstore}. "
            "Corré primero `python ingestar.py`."
        )

    store = Chroma(
        client=cliente,
        collection_name=config.coleccion,
        embedding_function=embeddings or crear_embeddings(config),
        collection_metadata={CLAVE_MODELO: config.modelo_embeddings},
        # Coseno: compara la dirección de los vectores, no su largo. Chroma usa L2
        # por defecto, y con coseno la relevancia queda entre 0 y 1.
        collection_configuration={"hnsw": {"space": "cosine"}},
    )
    if not crear and store._collection.count() == 0:
        raise BaseVectorialError(
            f"la colección '{config.coleccion}' está vacía. Corré `python ingestar.py`."
        )
    return store


def ingestar(
    config: ConfigRAG | None = None,
    *,
    embeddings: Embeddings | None = None,
    reiniciar: bool = False,
) -> ResumenIngesta:
    """Indexa /data en ChromaDB. Solo calcula embeddings de lo que cambió.

    Con `reiniciar=True` borra la colección y la arma de cero (por ejemplo,
    para cambiar de modelo de embeddings o de tamaño de chunk).
    """
    config = config or ConfigRAG()
    inicio = time.perf_counter()

    fragmentos = fragmentar(cargar_documentos(config), config)
    documentos = len({f.metadata["fuente"] for f in fragmentos})

    if reiniciar:
        cliente = _cliente(config)
        if _coleccion_existe(cliente, config.coleccion):
            cliente.delete_collection(config.coleccion)
            logger.info("ingesta: colección '%s' borrada para reindexar", config.coleccion)

    store = abrir_vectorstore(config, embeddings, crear=True)
    existentes = set(store.get(include=[])["ids"])
    nuevos = {f.id: f for f in fragmentos}

    a_agregar = [f for id_, f in nuevos.items() if id_ not in existentes]
    a_borrar = sorted(existentes - nuevos.keys())

    if a_borrar:
        store.delete(ids=a_borrar)
        logger.info("ingesta: %d fragmento(s) viejos borrados", len(a_borrar))

    if a_agregar:
        inicio_embeddings = time.perf_counter()
        store.add_documents(a_agregar, ids=[f.id for f in a_agregar])
        logger.info(
            "ingesta: %d fragmento(s) indexados en %.1f s (embeddings + escritura)",
            len(a_agregar),
            time.perf_counter() - inicio_embeddings,
        )
    elif not a_borrar:
        logger.info(
            "ingesta: la colección ya tiene los %d fragmentos actuales, no se reindexa nada",
            len(nuevos),
        )

    resumen = ResumenIngesta(
        documentos=documentos,
        fragmentos=len(nuevos),
        agregados=len(a_agregar),
        eliminados=len(a_borrar),
        reiniciada=reiniciar,
        duracion_ms=(time.perf_counter() - inicio) * 1000,
    )
    logger.info("ingesta terminada en %.0f ms", resumen.duracion_ms)
    return resumen
