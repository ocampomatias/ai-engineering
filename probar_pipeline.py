"""Mini-script de prueba del pipeline de extracción (pre-entrega 2).

Corre `process_text()` sobre tres textos y después hace dos pruebas de estrés:
un texto ambiguo y un tope de tokens tan bajo que la respuesta sale cortada.

Uso:

    python probar_pipeline.py                        # proveedor de LLM_PROVIDER (.env)
    python probar_pipeline.py --provider anthropic   # forzar proveedor
    python probar_pipeline.py --texto "..."          # analizar un texto propio
"""

import argparse
import asyncio
import logging
import sys

from llm_client import Provider, load_settings
from llm_client.errors import ConfigurationError
from pipeline import ExtraccionError, construir_cadena, crear_modelo, process_text

TEXTOS = {
    "Descripción de arquitectura": (
        "El backend es una API en FastAPI detrás de un Nginx, con Redis como caché de sesiones "
        "y PostgreSQL como base principal. Todo corre en Kubernetes sobre AWS. En los picos de "
        "tráfico el pool de conexiones a PostgreSQL se agota y las respuestas pasan de 80 ms a "
        "más de 4 segundos."
    ),
    "Log de error": (
        "2026-09-14T03:12:44Z ERROR [payments-worker] celery.task.process_payment failed: "
        "psycopg.OperationalError: connection to server at 'db-prod.internal' (10.0.3.12), "
        "port 5432 failed: FATAL: remaining connection slots are reserved. Retries exhausted "
        "(5/5). RabbitMQ queue 'payments' depth=1243 and growing."
    ),
    "Mejora sin urgencia": (
        "Propuesta para el próximo trimestre: migrar los scripts de build de Webpack a Vite y "
        "reemplazar Jest por Vitest en el frontend de React, para bajar el tiempo de CI."
    ),
}

# Sin nombres de tecnologías: el esquema exige al menos una. O el modelo encuentra
# algo defendible, o se agotan los reintentos y el error llega controlado.
TEXTO_AMBIGUO = (
    "Desde ayer el sistema anda lento y algunos usuarios dicen que no les carga nada. "
    "Puede ser la base o puede ser el deploy del viernes, no sabemos."
)

# La consola de Windows no siempre arranca en UTF-8, y tanto el JSON como los logs
# tienen tildes. Tiene que ir antes de basicConfig, que toma stderr al configurarse.
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s | %(name)s | %(message)s")
for ruidoso in ("httpx", "httpx2", "httpcore", "openai", "anthropic"):
    logging.getLogger(ruidoso).setLevel(logging.WARNING)
# Cuando el parser de LangChain ve un corte por max_tokens imprime un traceback
# entero. El validador de la cadena ya informa lo mismo en una línea.
logging.getLogger("langchain_core.output_parsers.openai_tools").setLevel(logging.CRITICAL)


def titulo(texto: str) -> None:
    print(f"\n{'=' * 70}\n{texto}\n{'=' * 70}", flush=True)


async def analizar(nombre: str, texto: str, **kwargs: object) -> bool:
    titulo(nombre)
    print(f"{texto}\n", flush=True)
    try:
        resultado = await process_text(texto, **kwargs)  # type: ignore[arg-type]
    except ExtraccionError as exc:
        print(f"\nERROR CONTROLADO tras {exc.intentos} intento(s): {exc}", flush=True)
        return False
    print("\nSalida validada:", flush=True)
    print(resultado.model_dump_json(indent=2), flush=True)
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prueba del pipeline de extracción")
    parser.add_argument("--provider", choices=[p.value for p in Provider])
    parser.add_argument("--texto", help="Analizar solo este texto.")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    settings = load_settings()
    provider = Provider(args.provider) if args.provider else settings.provider

    try:
        modelo = crear_modelo(settings, provider)
    except ConfigurationError as exc:
        print(f"{exc}\nCompletá la key en el .env o elegí otro proveedor con --provider.")
        return 1

    cadena = construir_cadena(modelo)
    titulo(f"Pipeline de extracción | {provider.value} | {settings.model_for(provider)}")

    if args.texto:
        return 0 if await analizar("Texto propio", args.texto, cadena=cadena) else 1

    for nombre, texto in TEXTOS.items():
        await analizar(nombre, texto, cadena=cadena)

    await analizar("Prueba de estrés 1: texto ambiguo", TEXTO_AMBIGUO, cadena=cadena)

    # Con 30 tokens el modelo no llega a cerrar la estructura. La cadena tiene que
    # detectar el corte por finish_reason en vez de intentar validar un objeto a medias.
    cadena_corta = construir_cadena(crear_modelo(settings, provider, max_tokens=30), intentos=2)
    await analizar("Prueba de estrés 2: tope de 30 tokens", TEXTOS["Log de error"], cadena=cadena_corta)

    titulo("Fin")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
