"""Pruebas de punta a punta del sistema RAG (pre-entrega 3).

1. Ingesta: indexa /data en ChromaDB. Si la colección ya está al día, no
   recalcula nada.
2. Dos preguntas cuya respuesta está en los documentos. Se verifica que la
   respuesta cite el archivo correcto.
3. Dos preguntas trampa cuya respuesta NO está en los documentos. Se verifica
   que el modelo diga "No lo sé." en vez de inventar. La segunda es una
   pregunta de cultura general que el modelo sabe responder de memoria; el
   sistema igual tiene que decir que no lo sabe, porque no está en el contexto.
4. Las mismas cuatro consultas lanzadas en paralelo con asyncio.gather, para
   comparar el tiempo con la ejecución una por una.

Los resultados quedan en evidencia/pruebas_rag.json.

Uso:

    python probar_rag.py                          # proveedor de LLM_PROVIDER (.env)
    python probar_rag.py --pregunta "¿Cada cuánto se rotan los secretos?"
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from llm_client import load_settings
from llm_client.errors import ConfigurationError
from rag import (
    NO_LO_SE,
    BaseVectorialError,
    IngestaError,
    RAGError,
    RespuestaRAG,
    get_rag_response,
    ingestar,
)

# La consola de Windows no siempre arranca en UTF-8, y tanto el JSON como los logs
# tienen tildes. Tiene que ir antes de basicConfig, que toma stderr al configurarse.
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s | %(name)s | %(message)s")
for ruidoso in (
    "httpx",
    "httpx2",
    "httpcore",
    "anthropic",
    "openai",
    "huggingface_hub",
    "chromadb",
):
    logging.getLogger(ruidoso).setLevel(logging.WARNING)

EVIDENCIA = Path(__file__).resolve().parent / "evidencia" / "pruebas_rag.json"


@dataclass(frozen=True)
class Caso:
    nombre: str
    pregunta: str
    debe_encontrarse: bool
    fuente_esperada: str | None = None
    #: Datos que la respuesta tiene que mencionar si salió del documento correcto.
    debe_mencionar: tuple[str, ...] = ()


CASOS = (
    Caso(
        nombre="En los documentos: procedimiento de un incidente",
        pregunta="¿Qué hay que hacer si la cola de pagos de RabbitMQ crece sin parar?",
        debe_encontrarse=True,
        fuente_esperada="runbook-incidentes.md",
        debe_mencionar=("24",),
    ),
    Caso(
        nombre="En los documentos: regla de despliegues",
        pregunta="¿Puedo desplegar a producción un viernes a las 16:00?",
        debe_encontrarse=True,
        fuente_esperada="politica-despliegues.md",
        debe_mencionar=("15:00",),
    ),
    Caso(
        nombre="Pregunta trampa: tema cercano que los documentos no cubren",
        pregunta="¿Qué CDN usa Tambor para servir el panel de comercios?",
        debe_encontrarse=False,
    ),
    Caso(
        nombre="Pregunta trampa: cultura general que el modelo sabe de memoria",
        pregunta="¿En qué año se publicó la primera versión de PostgreSQL?",
        debe_encontrarse=False,
    ),
)


def titulo(texto: str) -> None:
    print(f"\n{'=' * 78}\n{texto}\n{'=' * 78}", flush=True)


def verificar(caso: Caso, respuesta: RespuestaRAG) -> list[str]:
    """Devuelve la lista de problemas. Vacía significa que la prueba pasó."""
    problemas = []
    if respuesta.encontrada != caso.debe_encontrarse:
        esperado = "encontrada" if caso.debe_encontrarse else f"'{NO_LO_SE}'"
        problemas.append(
            f"se esperaba respuesta {esperado} y vino encontrada={respuesta.encontrada}"
        )
    if caso.fuente_esperada and caso.fuente_esperada not in {
        r.fuente for r in respuesta.referencias
    }:
        problemas.append(f"no cita {caso.fuente_esperada}")
    for dato in caso.debe_mencionar:
        if dato not in respuesta.respuesta:
            problemas.append(f"la respuesta no menciona '{dato}'")
    return problemas


async def correr_caso(caso: Caso) -> dict[str, object]:
    titulo(caso.nombre)
    print(f"Pregunta: {caso.pregunta}\n", flush=True)
    try:
        respuesta = await get_rag_response(caso.pregunta)
    except RAGError as exc:
        print(f"\nERROR CONTROLADO tras {exc.intentos} intento(s): {exc}", flush=True)
        return {"caso": caso.nombre, "pregunta": caso.pregunta, "paso": False, "error": str(exc)}

    print("\nRespuesta validada:", flush=True)
    print(respuesta.model_dump_json(indent=2), flush=True)
    problemas = verificar(caso, respuesta)
    print(f"\n{'PASA' if not problemas else 'FALLA: ' + '; '.join(problemas)}", flush=True)
    return {
        "caso": caso.nombre,
        "paso": not problemas,
        "problemas": problemas,
        "resultado": respuesta.model_dump(mode="json"),
    }


async def comparar_paralelo(preguntas: list[str]) -> dict[str, float]:
    """Corre las preguntas una por una y después todas juntas. Devuelve los tiempos en ms."""
    titulo(f"Async: {len(preguntas)} consultas una por una vs. en paralelo (asyncio.gather)")
    # Los logs de las consultas en paralelo se intercalan y no se entienden: acá
    # alcanza con los tiempos.
    logging.getLogger("rag").setLevel(logging.WARNING)
    try:
        inicio = time.perf_counter()
        for pregunta in preguntas:
            await get_rag_response(pregunta)
        secuencial = (time.perf_counter() - inicio) * 1000

        inicio = time.perf_counter()
        await asyncio.gather(*(get_rag_response(p) for p in preguntas))
        paralelo = (time.perf_counter() - inicio) * 1000
    finally:
        logging.getLogger("rag").setLevel(logging.NOTSET)

    print(f"Una por una: {secuencial:,.0f} ms", flush=True)
    print(f"En paralelo: {paralelo:,.0f} ms ({secuencial / paralelo:.1f}x más rápido)", flush=True)
    return {"secuencial_ms": round(secuencial), "paralelo_ms": round(paralelo)}


async def main(args: argparse.Namespace) -> int:
    settings = load_settings()
    if settings.key_for(settings.provider) is None:
        print(f"Falta la API key de {settings.provider.value} en el .env.", file=sys.stderr)
        return 1

    titulo("Ingesta de /data en ChromaDB")
    try:
        resumen = ingestar()
    except (IngestaError, BaseVectorialError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(resumen.model_dump_json(indent=2), flush=True)

    if args.pregunta:
        titulo("Pregunta propia")
        try:
            respuesta = await get_rag_response(args.pregunta)
        except RAGError as exc:
            print(f"ERROR CONTROLADO tras {exc.intentos} intento(s): {exc}", file=sys.stderr)
            return 1
        print(respuesta.model_dump_json(indent=2))
        return 0

    try:
        resultados = [await correr_caso(caso) for caso in CASOS]
        tiempos = await comparar_paralelo([caso.pregunta for caso in CASOS])
    except ConfigurationError as exc:
        print(f"ERROR de configuración: {exc}", file=sys.stderr)
        return 1

    pasaron = sum(bool(r["paso"]) for r in resultados)
    titulo(f"Resultado: {pasaron}/{len(resultados)} pruebas pasaron")
    for r in resultados:
        print(f"  {'PASA ' if r['paso'] else 'FALLA'}  {r['caso']}")

    EVIDENCIA.parent.mkdir(exist_ok=True)
    EVIDENCIA.write_text(
        json.dumps(
            {
                "fecha": datetime.now(UTC).isoformat(timespec="seconds"),
                "proveedor": settings.provider.value,
                "modelo": settings.model_for(settings.provider),
                "ingesta": resumen.model_dump(mode="json"),
                "pruebas": resultados,
                "async": tiempos,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nEvidencia guardada en {EVIDENCIA.parent.name}/{EVIDENCIA.name}")
    return 0 if pasaron == len(resultados) else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pruebas de punta a punta del sistema RAG")
    parser.add_argument("--pregunta", help="hacer una pregunta propia en vez de correr las pruebas")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse_args())))
