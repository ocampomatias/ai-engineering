"""Pruebas de punta a punta del agente (pre-entrega 5) y traza del razonamiento.

Uso:

    python probar_agente.py

Corre cuatro conversaciones contra el LLM configurado en el .env:

1. caso-ferreteria: una pregunta que obliga a encadenar herramientas (buscar el
   comercio, listar sus cobros, ver el cobro, buscar el código de error) y una
   segunda pregunta que solo se entiende con la memoria del thread.
2. caso-lopez: un nombre ambiguo ("López" son dos comercios). El agente tiene
   que pedir aclaración, y con la respuesta del usuario seguir sin repreguntar.
3. caso-nombre-mal-escrito: "Libreria Andinna" no existe. La herramienta devuelve
   un error con nombres parecidos; el agente tiene que reintentar o confirmar
   con el usuario, y con la confirmación terminar la consulta.
4. caso-sin-herramientas: un saludo, que no necesita ninguna herramienta.

Cada thread se borra antes de empezar, así la corrida es reproducible. Deja la
traza completa en evidencia/traza_agente.json y el log en evidencia/traza_agente.log.
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

from agente import Turno, abrir_agente

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

RAIZ = Path(__file__).resolve().parent
TRAZA_JSON = RAIZ / "evidencia" / "traza_agente.json"
TRAZA_LOG = RAIZ / "evidencia" / "traza_agente.log"

CONVERSACIONES: dict[str, list[str]] = {
    "caso-ferreteria": [
        "¿Por qué falló el último cobro de Ferretería López y qué tiene que hacer el comercio?",
        "¿Y el cobro anterior a ese? ¿También tuvo problemas?",
    ],
    "caso-lopez": [
        "Mostrame los cobros rechazados de López.",
        "El de la farmacia.",
    ],
    "caso-nombre-mal-escrito": [
        "¿Cuál fue el último cobro de la Libreria Andinna y en qué estado quedó?",
        "Sí, esa.",
    ],
    "caso-sin-herramientas": [
        "Hola, ¿qué tipo de consultas puedo hacerte?",
    ],
}


def configurar_logs() -> None:
    formato = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", "%H:%M:%S")
    TRAZA_LOG.parent.mkdir(parents=True, exist_ok=True)
    archivo = logging.FileHandler(TRAZA_LOG, mode="w", encoding="utf-8")
    consola = logging.StreamHandler()
    for handler in (archivo, consola):
        handler.setFormatter(formato)
    logging.basicConfig(level=logging.INFO, handlers=[archivo, consola])
    for ruidoso in ("httpx", "httpx2", "httpcore", "anthropic", "openai", "huggingface_hub", "urllib3",
                    "pinecone", "aiosqlite", "rag_pinecone", "rag.embeddings"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)


def resumen(turno: Turno) -> str:
    herramientas = [p.herramienta for p in turno.pasos if p.tipo == "llamada"]
    errores = sum(p.error for p in turno.pasos if p.tipo == "resultado")
    return (
        f"  herramientas: {' -> '.join(herramientas) or '(ninguna)'}"
        + (f"  [{errores} con error]" if errores else "")
        + f"\n  {turno.duracion_ms / 1000:.1f} s, {turno.tokens_entrada} tokens de entrada / "
        f"{turno.tokens_salida} de salida, {turno.mensajes_en_memoria} mensajes en memoria"
    )


async def main() -> int:
    configurar_logs()
    turnos: list[Turno] = []
    async with abrir_agente() as agente:
        for thread_id, preguntas in CONVERSACIONES.items():
            await agente.grafo.checkpointer.adelete_thread(thread_id)
            print(f"\n=== {thread_id} ===")
            for pregunta in preguntas:
                turno = await agente.preguntar(pregunta, thread_id)
                turnos.append(turno)
                print(f"\nUsuario: {pregunta}\nAgente: {turno.respuesta}\n{resumen(turno)}")

    TRAZA_JSON.write_text(
        json.dumps([t.model_dump() for t in turnos], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    multipaso = [t for t in turnos if t.llamadas_herramientas >= 2]
    print(
        f"\n{len(turnos)} turnos, {sum(t.llamadas_herramientas for t in turnos)} llamadas a "
        f"herramientas, {len(multipaso)} turno(s) con 2 o más. Traza en "
        f"{TRAZA_JSON.relative_to(RAIZ)} y {TRAZA_LOG.relative_to(RAIZ)}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
