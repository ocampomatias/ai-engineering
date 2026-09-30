"""RAGSystem: recuperador híbrido (Pinecone + BM25) fusionado con EnsembleRetriever.

    consulta ─┬─> Pinecone (similitud coseno, top `candidatos`)  ─┐
              └─> BM25 en memoria (léxico, top `candidatos`)    ─┴─> RRF ponderado -> top_k

- La parte vectorial es un `PineconeVectorStore` sobre el índice y el namespace
  de la ingesta. Encuentra fragmentos por significado aunque no compartan
  palabras con la consulta.
- La parte léxica es un `BM25Retriever`. Encuentra lo que los embeddings
  confunden: códigos (`TMB-1009`), nombres de alertas, comandos.
- `EnsembleRetriever` fusiona las dos listas con Reciprocal Rank Fusion:
  cada fragmento suma peso / (c + posición) por cada lista donde aparece. Usa
  posiciones y no puntajes, porque un 12,3 de BM25 y un 0,81 de coseno no
  están en la misma escala y sumarlos no tiene sentido.

BM25 necesita el texto de todo el corpus en memoria. Se arma leyendo los
fragmentos del mismo namespace de Pinecone (el texto está en los metadatos),
así las dos mitades del recuperador siempre ven el mismo corpus.
"""

import asyncio
import logging
import re
import time
import unicodedata
import warnings
from collections.abc import Callable
from typing import Any, Literal

from langchain_classic.retrievers import EnsembleRetriever

# langchain-community avisa al importarse que va a dejar de mantenerse. BM25Retriever
# (el que pide la consigna) sigue estando solo ahí, así que el aviso se silencia.
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message=".*langchain-community.*", category=DeprecationWarning)
    from langchain_community.retrievers import BM25Retriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever
from langchain_pinecone import PineconeVectorStore
from pydantic import BaseModel, Field

from .config import ConfigPinecone, cargar_config
from .infra import IndiceError, abrir_indice, conectar, validar_indice
from .ingesta import crear_embeddings, dimension_de, ids_en_namespace, lotes
from .schemas import CLAVE_TEXTO

logger = logging.getLogger(__name__)

Modo = Literal["hibrido", "vectorial", "bm25"]
MODOS: tuple[Modo, ...] = ("hibrido", "vectorial", "bm25")

# --- Tokenización para BM25 ------------------------------------------------

# Palabras que aparecen en casi todos los fragmentos y no ayudan a distinguir uno de otro.
STOPWORDS = frozenset(
    """a al algo como con cual cuando de del desde donde el ella en entre es esa ese
    esta este esto hasta hay la las le les lo los mas me mi muy no o para pero por
    que se si sin sobre su sus te tiene tu un una uno unos y ya son ser fue cada otro
    otra qué cuál cómo cuándo dónde cuánto cuántos cuánta cuántas puedo puede hace hacer tengo""".split()
)
_TOKEN = re.compile(r"[a-z0-9]+(?:[-_./][a-z0-9]+)*")


def _sin_tildes(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def tokenizar(texto: str) -> list[str]:
    """Tokens para BM25: minúsculas, sin tildes, sin stopwords, y los compuestos enteros y partidos.

    El tokenizador por defecto de BM25Retriever es `texto.split()`: "TMB-1009."
    y "TMB-1009" serían tokens distintos, y "rotación" no coincidiría con
    "rotacion". Acá "TMB-1009" da ["tmb-1009", "tmb", "1009"]: coincide con la
    consulta exacta y también con "error 1009".
    """
    tokens: list[str] = []
    for token in _TOKEN.findall(_sin_tildes(texto.lower())):
        if token in STOPWORDS:
            continue
        tokens.append(token)
        partes = re.split(r"[-_./]", token)
        if len(partes) > 1:
            tokens.extend(p for p in partes if p and p not in STOPWORDS)
    return tokens


# --- Filtros de metadatos --------------------------------------------------


def cumple_filtro(metadatos: dict[str, Any], filtro: dict[str, Any] | None) -> bool:
    """Evalúa un filtro con la sintaxis de Pinecone sobre metadatos en memoria.

    Hace falta para que BM25 respete el mismo filtro que Pinecone: si la parte
    vectorial filtra por categoría y la léxica no, la fusión mete fragmentos de
    otras categorías. Soporta $eq, $ne, $in, $nin, $gt, $gte, $lt, $lte, $and y
    $or. Como en Pinecone, un campo lista (las etiquetas) cumple $eq o $in si
    alguno de sus elementos cumple.
    """
    if not filtro:
        return True
    for clave, condicion in filtro.items():
        if clave == "$and":
            if not all(cumple_filtro(metadatos, f) for f in condicion):
                return False
            continue
        if clave == "$or":
            if not any(cumple_filtro(metadatos, f) for f in condicion):
                return False
            continue
        if not isinstance(condicion, dict):
            condicion = {"$eq": condicion}
        valor = metadatos.get(clave)
        valores = valor if isinstance(valor, list) else [valor]
        for operador, esperado in condicion.items():
            if not _cumple(operador, valores, esperado, valor is None):
                return False
    return True


def _cumple(operador: str, valores: list[Any], esperado: Any, falta: bool) -> bool:  # noqa: ANN401
    comparaciones: dict[str, Callable[[Any], bool]] = {
        "$eq": lambda v: v == esperado,
        "$in": lambda v: v in esperado,
        "$gt": lambda v: v > esperado,
        "$gte": lambda v: v >= esperado,
        "$lt": lambda v: v < esperado,
        "$lte": lambda v: v <= esperado,
    }
    if operador == "$ne":
        return falta or all(v != esperado for v in valores)
    if operador == "$nin":
        return falta or all(v not in esperado for v in valores)
    if operador not in comparaciones:
        raise ValueError(f"operador de filtro no soportado: {operador}")
    if falta:
        return False
    return any(comparaciones[operador](v) for v in valores)


# --- BM25 ------------------------------------------------------------------


class RecuperadorBM25(BM25Retriever):
    """`BM25Retriever` con dos cambios: descarta los puntajes 0 y acepta un filtro.

    El original devuelve siempre k documentos, aunque no compartan ni una palabra
    con la consulta. En la fusión esos documentos suman puntaje RRF por el solo
    hecho de estar en la lista, así que se descartan.
    """

    filtro: dict[str, Any] | None = None

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        puntajes = self.vectorizer.get_scores(self.preprocess_func(query))
        orden = sorted(range(len(self.docs)), key=lambda i: puntajes[i], reverse=True)
        resultado: list[Document] = []
        for i in orden:
            if puntajes[i] <= 0 or len(resultado) == self.k:
                break
            if cumple_filtro(self.docs[i].metadata, self.filtro):
                resultado.append(self.docs[i])
        return resultado


# --- Resultado -------------------------------------------------------------


class FragmentoHibrido(BaseModel):
    """Un fragmento del top-k, con de dónde vino y en qué posición lo puso cada recuperador."""

    posicion: int = Field(ge=1)
    chunk_id: str
    documento_id: str
    fuente: str
    pagina: int | None = None
    seccion: str
    puntaje_rrf: float = Field(ge=0, description="Suma de peso / (c + posición) de cada lista.")
    posicion_vectorial: int | None = Field(default=None, description="None: Pinecone no lo trajo.")
    posicion_bm25: int | None = Field(default=None, description="None: BM25 no lo trajo.")
    extracto: str


# --- RAGSystem -------------------------------------------------------------


class RAGSystem:
    """Recuperación híbrida sobre el corpus indexado en Pinecone."""

    def __init__(
        self,
        config: ConfigPinecone | None = None,
        *,
        cliente: Any = None,  # noqa: ANN401
        embeddings: Embeddings | None = None,
    ) -> None:
        self.config = config or cargar_config()
        inicio = time.perf_counter()
        self.embeddings = embeddings or crear_embeddings(self.config)
        cliente = cliente or conectar(self.config)

        self.index = abrir_indice(cliente, self.config)
        validar_indice(
            cliente.describe_index(self.config.indice), self.config, dimension_de(self.embeddings)
        )
        self.vectorstore = PineconeVectorStore(
            index=self.index,
            embedding=self.embeddings,
            text_key=CLAVE_TEXTO,
            namespace=self.config.namespace,
        )
        self.corpus = self._leer_corpus()
        self.bm25 = RecuperadorBM25.from_documents(
            self.corpus, k=self.config.candidatos, preprocess_func=tokenizar
        )
        logger.info(
            "RAGSystem listo en %.1f s: índice '%s', namespace '%s', %d fragmentos para BM25, "
            "pesos vectorial/BM25 %s",
            time.perf_counter() - inicio,
            self.config.indice,
            self.config.namespace,
            len(self.corpus),
            self.config.pesos,
        )

    def _leer_corpus(self) -> list[Document]:
        """Trae de Pinecone el texto y los metadatos de todo el namespace."""
        ids = sorted(ids_en_namespace(self.index, self.config.namespace))
        if not ids:
            raise IndiceError(
                f"el namespace '{self.config.namespace}' del índice '{self.config.indice}' está "
                "vacío. Corré `python ingestar_pinecone.py`."
            )
        documentos: list[Document] = []
        for lote in lotes(ids, 100):
            respuesta = self.index.fetch(ids=list(lote), namespace=self.config.namespace)
            for id_, vector in respuesta.vectors.items():
                metadatos = dict(vector.metadata)
                texto = metadatos.pop(CLAVE_TEXTO)
                documentos.append(Document(id=id_, page_content=texto, metadata=metadatos))
        return sorted(documentos, key=lambda d: d.metadata["chunk_id"])

    # --- Recuperadores ------------------------------------------------------

    def recuperador_vectorial(self, filtro: dict[str, Any] | None = None) -> BaseRetriever:
        return self.vectorstore.as_retriever(
            search_kwargs={"k": self.config.candidatos, "filter": filtro}
        )

    def recuperador_bm25(self, filtro: dict[str, Any] | None = None) -> BaseRetriever:
        # model_copy es superficial: la copia comparte el índice BM25, no lo recalcula.
        return self.bm25.model_copy(update={"filtro": filtro})

    def recuperador_hibrido(self, filtro: dict[str, Any] | None = None) -> EnsembleRetriever:
        return EnsembleRetriever(
            retrievers=[self.recuperador_vectorial(filtro), self.recuperador_bm25(filtro)],
            weights=self.config.pesos,
            c=self.config.rrf_c,
            # Sin id_key, el ensemble reconoce un mismo fragmento por su texto. Con el
            # chunk_id no depende de que las dos fuentes devuelvan el texto idéntico.
            id_key="chunk_id",
        )

    # --- Búsqueda -----------------------------------------------------------

    def buscar(
        self,
        consulta: str,
        *,
        k: int | None = None,
        filtro: dict[str, Any] | None = None,
        modo: Modo = "hibrido",
    ) -> list[Document]:
        """Los `k` fragmentos más relevantes (top_k de la config si no se indica).

        `modo` permite comparar la búsqueda híbrida contra cada mitad por separado;
        es lo que usa `evaluate.py`.
        """
        if not consulta.strip():
            raise ValueError("la consulta está vacía")
        recuperadores = {
            "hibrido": self.recuperador_hibrido,
            "vectorial": self.recuperador_vectorial,
            "bm25": self.recuperador_bm25,
        }
        inicio = time.perf_counter()
        documentos = recuperadores[modo](filtro).invoke(consulta)[: k or self.config.top_k]
        logger.info(
            "búsqueda %s en %.0f ms: %r -> %s",
            modo,
            (time.perf_counter() - inicio) * 1000,
            consulta,
            ", ".join(d.metadata["chunk_id"] for d in documentos) or "(nada)",
        )
        return documentos

    async def abuscar(self, consulta: str, **kwargs: Any) -> list[Document]:  # noqa: ANN401
        """Versión asíncrona: corre la búsqueda en un thread para no bloquear el event loop."""
        return await asyncio.to_thread(self.buscar, consulta, **kwargs)

    def explicar(
        self, consulta: str, *, filtro: dict[str, Any] | None = None
    ) -> list[FragmentoHibrido]:
        """El top-k híbrido con la posición que le dio cada recuperador y su puntaje RRF."""
        vectorial = self.recuperador_vectorial(filtro).invoke(consulta)
        lexico = self.recuperador_bm25(filtro).invoke(consulta)
        hibrido = self.buscar(consulta, filtro=filtro)

        def posiciones(documentos: list[Document]) -> dict[str, int]:
            return {d.metadata["chunk_id"]: i for i, d in enumerate(documentos, start=1)}

        pos_v, pos_b = posiciones(vectorial), posiciones(lexico)
        peso_v, peso_b = self.config.pesos
        c = self.config.rrf_c
        resultado = []
        for posicion, documento in enumerate(hibrido, start=1):
            id_ = documento.metadata["chunk_id"]
            rrf = sum(
                peso / (c + pos[id_]) for peso, pos in ((peso_v, pos_v), (peso_b, pos_b)) if id_ in pos
            )
            resultado.append(
                FragmentoHibrido(
                    posicion=posicion,
                    chunk_id=id_,
                    documento_id=documento.metadata["documento_id"],
                    fuente=documento.metadata["fuente"],
                    pagina=documento.metadata.get("pagina"),
                    seccion=documento.metadata["seccion"],
                    puntaje_rrf=round(rrf, 6),
                    posicion_vectorial=pos_v.get(id_),
                    posicion_bm25=pos_b.get(id_),
                    extracto=" ".join(documento.page_content.split())[:160],
                )
            )
        return resultado
