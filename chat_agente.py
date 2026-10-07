"""Una pregunta al agente de soporte (pre-entrega 5), con memoria por thread_id.

Uso:

    python chat_agente.py --thread caso-102 "¿Por qué falló el último cobro de Ferretería López?"
    python chat_agente.py --thread caso-102 "¿Y el anterior?"     # otro proceso, misma memoria
    python chat_agente.py --thread caso-102 --historial            # lo que quedó guardado
    python chat_agente.py --thread caso-102 --olvidar              # borra ese thread

Cada corrida es un proceso nuevo: lo que el agente recuerda entre una y otra
sale de memoria_agente.sqlite, no de la memoria del programa.
"""

import argparse
import asyncio
import logging
import sys

from agente import abrir_agente
from llm_client.errors import ConfigurationError

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s | %(name)s | %(message)s")
for ruidoso in ("httpx", "httpx2", "httpcore", "anthropic", "openai", "huggingface_hub", "urllib3", "pinecone",
                "aiosqlite", "rag_pinecone", "rag.embeddings"):
    logging.getLogger(ruidoso).setLevel(logging.WARNING)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Pregunta al agente de soporte de Tambor")
    parser.add_argument("pregunta", nargs="?", help="lo que le querés preguntar")
    parser.add_argument("--thread", default="consola", help="ID de la conversación (thread_id)")
    parser.add_argument("--historial", action="store_true", help="muestra la memoria del thread")
    parser.add_argument("--olvidar", action="store_true", help="borra la memoria del thread")
    parser.add_argument(
        "--buscador", choices=["auto", "pinecone", "local"], default="auto",
        help="dónde busca la documentación (auto: Pinecone si hay key)",
    )
    args = parser.parse_args()
    if not args.pregunta and not (args.historial or args.olvidar):
        parser.error("falta la pregunta (o --historial / --olvidar)")

    try:
        async with abrir_agente(buscador=args.buscador) as agente:
            if args.olvidar:
                await agente.grafo.checkpointer.adelete_thread(args.thread)
                print(f"Thread '{args.thread}' borrado.")
                return 0
            if args.historial:
                for mensaje in await agente.historial(args.thread):
                    llamadas = getattr(mensaje, "tool_calls", None)
                    texto = " ".join(mensaje.text.split())
                    detalle = f" -> {[c['name'] for c in llamadas]}" if llamadas else ""
                    print(f"{mensaje.type:>6}: {texto[:200]}{detalle}")
                return 0
            turno = await agente.preguntar(args.pregunta, args.thread)
    except ConfigurationError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1

    print(f"\n{turno.respuesta}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
