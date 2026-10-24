"""Trazas con Arize Phoenix (OpenTelemetry + OpenInference).

- `LangChainInstrumentor` instrumenta LangChain y LangGraph: cada nodo del grafo,
  cada llamada al LLM (con tokens, latencia y costo) y cada herramienta queda
  como un span, sin decorar función por función.
- El worker abre un span `pedido` (`firma` al reanudar con la aprobación, `reanudacion`
  con la etiqueta `recuperado` al retomar tras una caída) por cada ejecución y lo
  marca con la sesión (`job_id`), una etiqueta (`reembolso` o `consulta`) y, en la
  firma, quién firmó como usuario. En Phoenix, la corrida que se frenó esperando
  aprobación y la que la reanudó quedan juntas en la misma sesión.
- La decisión humana queda como anotación `aprobacion_humana` (HUMAN) sobre el
  span que pidió la firma: aparece en la columna de anotaciones y se puede filtrar.

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

#: Cliente de Phoenix para anotar; None si las trazas están apagadas.
_cliente = None

#: Tracer de la app. Mientras no se llame a `iniciar_trazas`, los spans no van a ningún lado.
tracer = OITracer(trace.get_tracer("tambor.api"), config=TraceConfig())


def iniciar_trazas() -> TracerProvider | None:
    if os.getenv("PHOENIX_DISABLED", "").lower() in {"1", "true"}:
        logger.info("Trazas desactivadas (PHOENIX_DISABLED).")
        return None
    global _cliente
    from phoenix.client import AsyncClient  # imports acá: los tests no los necesitan
    from phoenix.otel import register

    # El SDK de Anthropic (1.15+) emite su propio span por llamada al ver un tracer global: Phoenix
    # contaría cada llamada dos veces (y el doble de costo). El span de LangChain ya trae todo.
    # Antes de crear los modelos, que leen la variable al construirse.
    os.environ.setdefault("ANTHROPIC_OPEN_TELEMETRY", "false")
    proveedor = register(
        project_name=os.getenv("PHOENIX_PROJECT_NAME", "tambor-api"),
        batch=True,  # exporta en segundo plano, no en el camino de cada request
        verbose=False,
    )
    LangChainInstrumentor().instrument(tracer_provider=proveedor)
    _cliente = AsyncClient(
        base_url=os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006"),
        api_key=os.getenv("PHOENIX_API_KEY"),
    )
    logger.info("Trazas a Phoenix: %s", os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006"))
    return proveedor


async def anotar_firma(span_id: str, revision: dict) -> None:
    """Registra en Phoenix la decisión de quien firmó, sobre el span que pidió la firma."""
    if _cliente is None:
        return
    try:
        await _cliente.spans.add_span_annotation(
            span_id=span_id,
            annotation_name="aprobacion_humana",
            annotator_kind="HUMAN",
            label="aprobado" if revision["aprobado"] else "rechazado",
            score=1.0 if revision["aprobado"] else 0.0,
            explanation=revision.get("comentario") or None,
            metadata={"revisor": revision["revisor"]},
            identifier=revision["revisor"],
        )
    except Exception:  # Phoenix caído no debe frenar un reembolso ya firmado
        logger.warning("No se pudo anotar la firma en Phoenix (span %s)", span_id, exc_info=True)


def cerrar_trazas(proveedor: TracerProvider | None) -> None:
    if proveedor is not None:
        LangChainInstrumentor().uninstrument()
        proveedor.shutdown()  # vacía el lote pendiente antes de salir
