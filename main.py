"""Script de validación del Unified Async LLM Client.

Prueba los dos modos que pide la consigna —respuesta completa y streaming—
contra el proveedor configurado, y demuestra el manejo de errores y la
concurrencia.

Uso:

    python main.py                        # proveedor de LLM_PROVIDER (.env)
    python main.py --provider anthropic   # forzar proveedor
    python main.py --all                  # probar todos los que tengan API key
    python main.py --compare              # mismo prompt a ambos, en paralelo
    python main.py --list-models          # ver qué modelos habilita tu API key
"""

import argparse
import asyncio
import logging
import sys

from llm_client import (
    AsyncLLMManager,
    ModelConfig,
    Provider,
    load_settings,
)
from llm_client.errors import LLMError

PREGUNTA = "¿Qué es la entropía?"
SYSTEM_PROMPT = "Sos un divulgador científico. Respondé en español, claro y en menos de 120 palabras."

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(name)s | %(message)s",
)
# Silenciamos el ruido HTTP de los SDKs para que se lea la salida de la demo.
logging.getLogger("httpx2").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("anthropic").setLevel(logging.WARNING)


def titulo(texto: str) -> None:
    print(f"\n{'=' * 70}\n{texto}\n{'=' * 70}")


# --- 1. Modo normal (respuesta completa) ---------------------------------


async def probar_generate(manager: AsyncLLMManager, provider: Provider, prompt: str) -> bool:
    titulo(f"[{provider.value}] Modo normal — await generate()")

    config = manager.build_config(
        provider,
        temperature=0.7,
        max_tokens=400,
        system_prompt=SYSTEM_PROMPT,
    )
    print(f"modelo: {config.model} | temperature: {config.temperature}\n")

    respuesta = await manager.generate(prompt, provider=provider, config=config)

    if not respuesta.ok:
        assert respuesta.error is not None
        print(f"ERROR CONTROLADO ({respuesta.error.type}): {respuesta.error.message}")
        print(f"intentos: {respuesta.error.attempts} | reintentable: {respuesta.error.retryable}")
        return False

    print(respuesta.text)
    print(
        f"\ntokens: {respuesta.usage.input_tokens} entrada / "
        f"{respuesta.usage.output_tokens} salida "
        f"| latencia: {respuesta.latency_ms:.0f} ms "
        f"| fin: {respuesta.finish_reason}"
    )
    return True


# --- 2. Modo streaming ----------------------------------------------------


async def probar_stream(manager: AsyncLLMManager, provider: Provider, prompt: str) -> bool:
    titulo(f"[{provider.value}] Modo streaming — async for sobre el generador")

    config = manager.build_config(provider, max_tokens=400, system_prompt=SYSTEM_PROMPT)
    print(f"modelo: {config.model}\n")

    fragmentos = 0
    ok = True

    async for chunk in manager.stream(prompt, provider=provider, config=config):
        match chunk.type:
            case "delta":
                print(chunk.delta, end="", flush=True)
                fragmentos += 1
            case "done":
                uso = chunk.usage
                print(f"\n\n[fin del stream] {fragmentos} fragmentos recibidos", end="")
                if uso:
                    print(
                        f" | tokens: {uso.input_tokens} entrada / {uso.output_tokens} salida",
                        end="",
                    )
                print()
            case "error":
                assert chunk.error is not None
                print(f"\n\nERROR CONTROLADO ({chunk.error.type}): {chunk.error.message}")
                ok = False

    return ok


# --- 3. Manejo de errores -------------------------------------------------


async def probar_errores(manager: AsyncLLMManager, provider: Provider) -> None:
    """Un modelo inexistente no debe romper el programa."""
    titulo(f"[{provider.value}] Resiliencia — modelo inválido, sin crash")

    respuesta = await manager.generate(
        "hola",
        provider=provider,
        config=ModelConfig(
            model="modelo-que-no-existe-1234",
            max_tokens=32,
            max_retries=0,
        ),
    )
    assert respuesta.error is not None, "se esperaba un error controlado"
    print(f"tipo: {respuesta.error.type}")
    print(f"mensaje: {respuesta.error.message[:200]}")
    print(f"reintentable: {respuesta.error.retryable}")
    print("\nEl programa sigue corriendo: el error volvió dentro del ModelResponse.")


# --- 4. Concurrencia ------------------------------------------------------


async def probar_concurrencia(manager: AsyncLLMManager, provider: Provider) -> None:
    """Varias llamadas en paralelo, con el semáforo como control de flujo."""
    titulo(f"[{provider.value}] Concurrencia — asyncio.gather + semáforo")

    preguntas = [
        "Definí entropía en una oración.",
        "Definí entalpía en una oración.",
        "Definí energía libre de Gibbs en una oración.",
    ]
    config = manager.build_config(provider, max_tokens=120)

    inicio = asyncio.get_running_loop().time()
    respuestas = await manager.generate_many(preguntas, provider=provider, config=config)
    transcurrido = asyncio.get_running_loop().time() - inicio

    for pregunta, respuesta in zip(preguntas, respuestas, strict=True):
        estado = "OK " if respuesta.ok else "ERR"
        detalle = respuesta.text.strip() if respuesta.ok else str(respuesta.error and respuesta.error.message)
        print(f"[{estado}] {pregunta}\n      -> {detalle[:160]}")

    exitosas = sum(1 for r in respuestas if r.ok)
    print(f"\n{exitosas}/{len(preguntas)} respuestas OK en {transcurrido:.2f}s (en paralelo)")


# --- 5. Comparación entre proveedores ------------------------------------


async def probar_comparacion(manager: AsyncLLMManager, providers: list[Provider]) -> None:
    titulo("Comparación — mismo prompt a varios proveedores en paralelo")

    resultados = await manager.compare_providers(
        "En una sola oración: ¿qué es la entropía?",
        providers=providers,
        max_tokens=150,
    )
    for provider, respuesta in resultados.items():
        print(f"\n--- {provider.value} ({respuesta.model}) ---")
        if respuesta.ok:
            print(f"{respuesta.text.strip()}\n[{respuesta.latency_ms:.0f} ms]")
        else:
            assert respuesta.error is not None
            print(f"ERROR: {respuesta.error.message}")


# --- 6. Listado de modelos ----------------------------------------------


async def listar_modelos(manager: AsyncLLMManager, providers: list[Provider]) -> None:
    titulo("Modelos habilitados para tus API keys")
    for provider in providers:
        print(f"\n--- {provider.value} ---")
        try:
            cliente = manager.get_client(provider)
            modelos = await cliente.list_models()  # type: ignore[attr-defined]
            for nombre in modelos:
                print(f"  {nombre}")
        except LLMError as exc:
            print(f"  no se pudo listar: {exc}")


# --- Punto de entrada ----------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Demo del Unified Async LLM Client")
    parser.add_argument(
        "--provider",
        choices=[p.value for p in Provider],
        help="Proveedor a probar. Por defecto usa LLM_PROVIDER del .env.",
    )
    parser.add_argument("--all", action="store_true", help="Probar todos los proveedores con API key.")
    parser.add_argument("--compare", action="store_true", help="Comparar proveedores en paralelo.")
    parser.add_argument("--list-models", action="store_true", help="Listar modelos disponibles.")
    parser.add_argument("--prompt", default=PREGUNTA, help="Prompt a usar en las pruebas.")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    settings = load_settings()
    disponibles = settings.available_providers()

    titulo("Unified Async LLM Client — script de validación")
    print(f"proveedor configurado : {settings.provider.value}")
    print(f"con API key cargada   : {[p.value for p in disponibles] or 'ninguno'}")
    print(f"concurrencia máxima   : {settings.max_concurrency}")

    if not disponibles:
        print(
            "\nNo hay ninguna API key en el entorno.\n"
            "Copiá .env.example a .env y completá OPENAI_API_KEY o ANTHROPIC_API_KEY.\n"
            "Ver el README para el detalle."
        )
        return 1

    # Qué proveedores probar
    if args.all:
        objetivo = disponibles
    elif args.provider:
        objetivo = [Provider(args.provider)]
    else:
        objetivo = [settings.provider]

    faltan_key = [p for p in objetivo if p not in disponibles]
    if faltan_key:
        print(f"\nAviso: sin API key para {[p.value for p in faltan_key]}.")
        print("Se prueban igual para mostrar el error controlado.")

    async with AsyncLLMManager(settings) as manager:
        if args.list_models:
            await listar_modelos(manager, objetivo)
            return 0

        if args.compare:
            await probar_comparacion(manager, objetivo)
            return 0

        for provider in objetivo:
            generacion_ok = await probar_generate(manager, provider, args.prompt)
            await probar_stream(manager, provider, args.prompt)
            await probar_errores(manager, provider)
            if generacion_ok:
                await probar_concurrencia(manager, provider)

    titulo("Fin")
    return 0


if __name__ == "__main__":
    # asyncio.run() es el único punto de entrada: crea el event loop, corre la
    # corrutina y lo cierra limpiamente.
    sys.exit(asyncio.run(main()))
