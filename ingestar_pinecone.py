"""Ingesta a Pinecone (pre-entrega 4): sube el corpus de /corpus al índice serverless.

Uso:

    python ingestar_pinecone.py              # sube solo lo que cambió desde la última vez
    python ingestar_pinecone.py --reiniciar  # vacía el namespace y sube todo de cero

Crea el índice si no existe. Los embeddings se calculan en la máquina con el
mismo modelo de la pre-entrega 3; la primera vez se descarga (unos 640 MB).
"""

import argparse
import logging
import sys

from rag_pinecone import ConfigError, IndiceError, IngestaError, cargar_config, ingestar

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s | %(name)s | %(message)s")
for ruidoso in ("httpx", "httpcore", "huggingface_hub", "urllib3", "pinecone"):
    logging.getLogger(ruidoso).setLevel(logging.WARNING)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sube /corpus a Pinecone")
    parser.add_argument(
        "--reiniciar", action="store_true", help="vacía el namespace y sube todo de cero"
    )
    args = parser.parse_args()

    config = cargar_config()
    try:
        resumen = ingestar(config, reiniciar=args.reiniciar)
    except (ConfigError, IndiceError, IngestaError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1

    print(
        f"\nÍndice '{resumen.indice}', namespace '{resumen.namespace}': {resumen.documentos} "
        f"documentos, {resumen.fragmentos} fragmentos ({resumen.agregados} subidos en "
        f"{resumen.lotes} lote(s), {resumen.eliminados} borrados) en "
        f"{resumen.duracion_ms / 1000:.1f} s."
    )
    if resumen.sin_cambios:
        print("No había cambios: no se calculó ningún embedding ni se subió nada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
