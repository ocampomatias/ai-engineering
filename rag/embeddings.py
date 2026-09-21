"""Embeddings locales con fastembed, envueltos en la interfaz de LangChain.

Anthropic no tiene API de embeddings, y el proyecto funciona con una sola key
de Anthropic. Por eso los embeddings se calculan en la máquina: fastembed corre
el modelo en ONNX, sin PyTorch y sin GPU. La primera vez descarga el modelo
(unos 640 MB) a `.cache/fastembed`; después lo carga de disco.
"""

import logging
import os
import threading
import time
from pathlib import Path

from langchain_core.embeddings import Embeddings

logger = logging.getLogger(__name__)

# huggingface_hub avisa en Windows que no puede crear symlinks en la caché. No
# afecta en nada: copia los archivos en vez de enlazarlos.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


class EmbeddingsLocales(Embeddings):
    """`Embeddings` de LangChain sobre `fastembed.TextEmbedding`.

    El modelo se carga recién en el primer uso, así que crear el objeto es
    instantáneo (los tests que no calculan embeddings no lo pagan).
    """

    def __init__(self, modelo: str, cache_dir: Path | None = None) -> None:
        self.modelo = modelo
        self.cache_dir = cache_dir
        self._motor = None
        self._lock = threading.Lock()

    def _cargar(self):  # noqa: ANN202 - el tipo es de fastembed, se importa tarde
        # LangChain corre los métodos async en un thread pool: dos consultas en
        # paralelo podrían cargar el modelo dos veces sin el lock.
        with self._lock:
            if self._motor is None:
                from fastembed import TextEmbedding

                inicio = time.perf_counter()
                cache_dir = str(self.cache_dir) if self.cache_dir else None
                try:
                    # Si ya está en la caché, no hace falta ni preguntarle a Hugging Face.
                    self._motor = TextEmbedding(
                        self.modelo, cache_dir=cache_dir, local_files_only=True
                    )
                except Exception:  # fastembed no expone una excepción propia para "no está"
                    logger.info(
                        "descargando el modelo de embeddings %s (solo la primera vez)", self.modelo
                    )
                    self._motor = TextEmbedding(self.modelo, cache_dir=cache_dir)
                logger.info(
                    "modelo de embeddings %s cargado en %.1f s",
                    self.modelo,
                    time.perf_counter() - inicio,
                )
        return self._motor

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._cargar().embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self._cargar().query_embed(text))).tolist()
