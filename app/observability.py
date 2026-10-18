"""Trazas con Arize Phoenix (OpenTelemetry + OpenInference).

- `LangChainInstrumentor` instrumenta LangChain y LangGraph: cada nodo del grafo,
  cada llamada al LLM (con tokens, latencia y costo) y cada herramienta queda
  como un span, sin decorar función por función.
- El worker abre un span `job` por cada ejecución (ver `tracer`) y lo marca con
  `using_session(job_id)`: en Phoenix, la corrida que se frenó esperando
  aprobación y la que la reanudó quedan juntas en la misma sesión.

Por defecto manda a un Phoenix local (`phoenix serve`, http://localhost:6006).
Para Phoenix Cloud alcanza con PHOENIX_COLLECTOR_ENDPOINT y PHOENIX_API_KEY en el
`.env`. Si Phoenix no está levantado, la API funciona igual: solo se pierden
las trazas.
"""

import logging
import os

from openinference.instrumentation import OITracer, TraceConfig
from openinference.instrumentation.langchain import LangChainInstrumentor
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

logger = logging.getLogger(__name__)

#: Tracer de la app. Mientras no se llame a `iniciar_trazas`, los spans no van a ningún lado.
tracer = OITracer(trace.get_tracer("tambor.api"), config=TraceConfig())


def iniciar_trazas() -> TracerProvider | None:
    if os.getenv("PHOENIX_DISABLED", "").lower() in {"1", "true"}:
        logger.info("Trazas desactivadas (PHOENIX_DISABLED).")
        return None
    from phoenix.otel import register  # import acá: los tests no lo necesitan

    proveedor = register(
        project_name=os.getenv("PHOENIX_PROJECT_NAME", "tambor-api"),
        batch=True,  # exporta en segundo plano, no en el camino de cada request
        verbose=False,
    )
    LangChainInstrumentor().instrument(tracer_provider=proveedor)
    logger.info("Trazas a Phoenix: %s", os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006"))
    return proveedor


def cerrar_trazas(proveedor: TracerProvider | None) -> None:
    if proveedor is not None:
        LangChainInstrumentor().uninstrument()
        proveedor.shutdown()  # vacía el lote pendiente antes de salir
