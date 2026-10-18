"""API de producción del orquestador multi-agente (pre-entrega 7)."""

import os

# Al leer un checkpoint de Redis, LangGraph solo reconstruye los tipos del esquema
# del estado (Aporte, Decision, mensajes...), no cualquier clase. Se lee al
# importar langgraph, por eso va acá, antes que cualquier otro import.
os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
