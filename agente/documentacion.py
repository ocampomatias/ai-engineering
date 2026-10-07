"""Búsqueda en la documentación de Tambor para la herramienta `buscar_documentacion`.

Dos implementaciones con la misma interfaz:

- `BuscadorPinecone`: el `RAGSystem` híbrido de la pre-entrega 4 (Pinecone +
  BM25). Es el que se usa si hay PINECONE_API_KEY.
- `BuscadorLocal`: solo BM25, en memoria, sobre el mismo `corpus/`. No necesita
  key, red ni modelo de embeddings, así que el agente funciona igual en una
  máquina sin Pinecone. Para los códigos de error, que es lo que más consulta
  el agente, BM25 fue el mejor modo en la evaluación de la pre-entrega 4.

Los dos se inicializan recién en la primera búsqueda: el agente que contesta
"hola" no paga la carga del modelo de embeddings ni la conexión a Pinecone.
"""

import logging
import os
import threading
from typing import Literal, Protocol

from langchain_core.documents import Document

logger = logging.getLogger(__name__)

ModoBuscador = Literal["auto", "pinecone", "local"]


class Buscador(Protocol):
    nombre: str

    def buscar(self, consulta: str, k: int) -> list[Document]: ...


class BuscadorLocal:
    """BM25 sobre `corpus/`, con el tokenizador y el BM25 sin puntajes 0 de la pre-entrega 4."""

    nombre = "bm25-local"

    def __init__(self) -> None:
        self._bm25 = None
        self._lock = threading.Lock()

    def _cargar(self):  # noqa: ANN202 - RecuperadorBM25, se importa tarde
        with self._lock:
            if self._bm25 is None:
                from rag_pinecone import ConfigPinecone, cargar_corpus, tokenizar
                from rag_pinecone.schemas import CLAVE_TEXTO
                from rag_pinecone.sistema import RecuperadorBM25

                documentos = []
                for fragmento in cargar_corpus(ConfigPinecone()):
                    metadatos = fragmento.para_pinecone()
                    texto = metadatos.pop(CLAVE_TEXTO)
                    documentos.append(Document(page_content=texto, metadata=metadatos))
                self._bm25 = RecuperadorBM25.from_documents(documentos, preprocess_func=tokenizar)
                logger.info("documentación: BM25 local con %d fragmentos", len(documentos))
        return self._bm25

    def buscar(self, consulta: str, k: int) -> list[Document]:
        return self._cargar().model_copy(update={"k": k}).invoke(consulta)


class BuscadorPinecone:
    """El `RAGSystem` híbrido de la pre-entrega 4."""

    nombre = "pinecone-hibrido"

    def __init__(self) -> None:
        self._sistema = None
        self._lock = threading.Lock()

    def _cargar(self):  # noqa: ANN202 - RAGSystem, se importa tarde
        with self._lock:
            if self._sistema is None:
                from rag_pinecone import RAGSystem

                self._sistema = RAGSystem()
        return self._sistema

    def buscar(self, consulta: str, k: int) -> list[Document]:
        return self._cargar().buscar(consulta, k=k)


def crear_buscador(modo: ModoBuscador = "auto") -> Buscador:
    """`auto` usa Pinecone si hay PINECONE_API_KEY en el entorno y si no, BM25 local."""
    if modo == "auto":
        modo = "pinecone" if os.getenv("PINECONE_API_KEY", "").strip() else "local"
    return BuscadorPinecone() if modo == "pinecone" else BuscadorLocal()
