"""Módulo de recuperación escalable: Pinecone Serverless + BM25, fusionados con RRF.

Pre-entrega 4 del curso AI Engineering de Coderhouse. Reutiliza la limpieza,
el splitter y el modelo de embeddings local de la pre-entrega 3.
"""

from rag.ingesta import IngestaError

from .carga import cargar_corpus, leer_catalogo
from .config import ConfigError, ConfigPinecone, cargar_config
from .infra import IndiceError, asegurar_indice, conectar
from .ingesta import ingestar
from .metricas import GoldenSet, MetricasAgregadas, PreguntaGolden, agregar, medir_pregunta
from .schemas import MetadatosDocumento, MetadatosFragmento, ResumenIngesta
from .sistema import MODOS, FragmentoHibrido, RAGSystem, cumple_filtro, tokenizar

__all__ = [
    "MODOS",
    "ConfigError",
    "ConfigPinecone",
    "FragmentoHibrido",
    "GoldenSet",
    "IndiceError",
    "IngestaError",
    "MetadatosDocumento",
    "MetadatosFragmento",
    "MetricasAgregadas",
    "PreguntaGolden",
    "RAGSystem",
    "ResumenIngesta",
    "agregar",
    "asegurar_indice",
    "cargar_config",
    "cargar_corpus",
    "conectar",
    "cumple_filtro",
    "ingestar",
    "leer_catalogo",
    "medir_pregunta",
    "tokenizar",
]
