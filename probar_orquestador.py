"""Prueba de punta a punta del orquestador multi-agente (pre-entrega 6).

Uso:

    python probar_orquestador.py
    python probar_orquestador.py --pedido "¿Qué tasa de rechazo tiene el Café del Puerto?"

Corre pedidos que necesitan a los dos especialistas (datos + cálculos) y una
síntesis. Deja la traza en evidencia/traza_orquestador.json, el log en
evidencia/traza_orquestador.log y el diagrama en evidencia/grafo_orquestador.mmd.
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from orquestador import Ejecucion, construir_orquestador, ejecutar

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

RAIZ = Path(__file__).resolve().parent
EVIDENCIA = RAIZ / "evidencia"

PEDIDOS = [
    "Analizá los últimos 5 cobros de Ferretería López: qué porcentaje se rechazó, cuánta plata "
    "quedó sin cobrar y qué tiene que hacer el comercio con cada código de error.",
    "Compará la tasa de rechazo de Librería Andina y Café del Puerto. ¿Cuál está peor y por qué?",
]


def configurar_logs() -> None:
    formato = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", "%H:%M:%S")
    archivo = logging.FileHandler(EVIDENCIA / "traza_orquestador.log", mode="w", encoding="utf-8")
    consola = logging.StreamHandler()
    for handler in (archivo, consola):
        handler.setFormatter(formato)
    logging.basicConfig(level=logging.INFO, handlers=[archivo, consola])
    for ruidoso in ("httpx", "httpx2", "httpcore", "anthropic", "openai", "huggingface_hub",
                    "urllib3", "pinecone", "rag_pinecone", "rag.embeddings"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)


def resumen(ejecucion: Ejecucion) -> str:
    lineas = [f"  recorrido: {' -> '.join(ejecucion.recorrido)}"]
    for d in ejecucion.decisiones:
        lineas.append(f"  supervisor -> {d.siguiente}: {d.instruccion or d.motivo}")
    lineas.append(f"  {ejecucion.duracion_ms / 1000:.1f} s, {len(ejecucion.aportes)} aportes")
    return "\n".join(lineas)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Prueba el orquestador multi-agente")
    parser.add_argument("--pedido", help="un pedido propio en vez de los de prueba")
    args = parser.parse_args()

    EVIDENCIA.mkdir(exist_ok=True)
    configurar_logs()
    grafo = construir_orquestador()
    (EVIDENCIA / "grafo_orquestador.mmd").write_text(grafo.get_graph().draw_mermaid(), encoding="utf-8")

    ejecuciones = []
    for pedido in [args.pedido] if args.pedido else PEDIDOS:
        ejecucion = await ejecutar(grafo, pedido)
        ejecuciones.append(ejecucion)
        print(f"\nPedido: {pedido}\n{resumen(ejecucion)}\n\nRespuesta:\n{ejecucion.respuesta_final}")

    if not args.pedido:
        (EVIDENCIA / "traza_orquestador.json").write_text(
            json.dumps([e.model_dump() for e in ejecuciones], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
