"""Sistema de recuperación semántica local (RAG) con ChromaDB, LangChain y Pydantic.

Pre-entrega 3 del curso AI Engineering de Coderhouse. Reutiliza la
configuración del módulo 1 (`.env`, proveedor y keys) y el modelo del módulo 2.
"""

from .chain import (
    ObservadorRAG,
    RAGError,
    construir_cadena_rag,
    get_rag_response,
    recuperar,
)
from .config import ConfigRAG
from .ingesta import (
    BaseVectorialError,
    IngestaError,
    abrir_vectorstore,
    cargar_documentos,
    fragmentar,
    ingestar,
    limpiar_texto,
)
from .schemas import (
    NO_LO_SE,
    FragmentoRecuperado,
    Metricas,
    Referencia,
    RespuestaLLM,
    RespuestaRAG,
    ResumenIngesta,
)

__all__ = [
    "NO_LO_SE",
    "BaseVectorialError",
    "ConfigRAG",
    "FragmentoRecuperado",
    "IngestaError",
    "Metricas",
    "ObservadorRAG",
    "RAGError",
    "Referencia",
    "RespuestaLLM",
    "RespuestaRAG",
    "ResumenIngesta",
    "abrir_vectorstore",
    "cargar_documentos",
    "construir_cadena_rag",
    "fragmentar",
    "get_rag_response",
    "ingestar",
    "limpiar_texto",
    "recuperar",
]
