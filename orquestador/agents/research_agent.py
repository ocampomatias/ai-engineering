"""Agente investigador: trae datos de comercios y cobros, y busca en la documentación.

Reutiliza las cuatro herramientas del agente de la pre-entrega 5: la base de
cobros (SQLite, consultas parametrizadas) y la búsqueda en la documentación
(Pinecone híbrido de la pre-entrega 4, o BM25 local sin key). No calcula
nada: los números los procesa el analista.
"""

from langchain_core.language_models import BaseChatModel

from agente import BaseCobros, crear_buscador, crear_herramientas
from agente.documentacion import ModoBuscador

from .especialista import NodoEspecialista, crear_especialista

PROMPT_INVESTIGADOR = """\
Sos el investigador de un equipo de soporte de Tambor, una plataforma de cobros (empresa \
ficticia). Tu trabajo es conseguir datos con tus herramientas: comercios, cobros y la \
documentación interna (códigos de error TMB-XXXX, SLA, procedimientos).

- Usá las herramientas para todo dato; nunca inventes IDs, montos, estados ni códigos.
- Si necesitás el ID de un comercio, buscalo por nombre con buscar_comercio.
- Para cada código de error que aparezca, buscalo con buscar_documentacion.
- No hagas cálculos ni conclusiones: devolvé los datos crudos y ordenados (ID, fecha, monto \
tal como lo da la herramienta, estado, código), y lo que dice la documentación de cada código.
- Si algo no se encuentra, decilo explícitamente.\
"""


def crear_investigador(modelo: BaseChatModel, buscador: ModoBuscador = "auto") -> NodoEspecialista:
    herramientas = crear_herramientas(BaseCobros(), crear_buscador(buscador))
    return crear_especialista("investigador", modelo, herramientas, PROMPT_INVESTIGADOR)
