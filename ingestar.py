"""Script de ingesta (pre-entrega 3): indexa los documentos de /data en ChromaDB.

Uso:

    python ingestar.py              # indexa solo lo que cambió desde la última vez
    python ingestar.py --reiniciar  # borra la colección y la arma de cero

La primera corrida descarga el modelo de embeddings (unos 640 MB) a
.cache/fastembed. Las siguientes lo cargan de disco.
"""

import argparse
import logging
import sys

from rag import BaseVectorialError, ConfigRAG, IngestaError, ingestar

# La consola de Windows no siempre arranca en UTF-8 y los logs tienen tildes.
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s | %(name)s | %(message)s")
for ruidoso in ("httpx", "httpx2", "httpcore", "huggingface_hub", "chromadb"):
    logging.getLogger(ruidoso).setLevel(logging.WARNING)


def main() -> int:
    parser = argparse.ArgumentParser(description="Indexa /data en ChromaDB")
    parser.add_argument(
        "--reiniciar", action="store_true", help="borra la colección y reindexa todo"
    )
    args = parser.parse_args()

    config = ConfigRAG()
    try:
        resumen = ingestar(config, reiniciar=args.reiniciar)
    except (IngestaError, BaseVectorialError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1

    print(
        f"\nColección '{config.coleccion}' en {config.carpeta_vectorstore.name}/: "
        f"{resumen.documentos} documentos, {resumen.fragmentos} fragmentos "
        f"({resumen.agregados} indexados, {resumen.eliminados} borrados) "
        f"en {resumen.duracion_ms / 1000:.1f} s."
    )
    if resumen.sin_cambios:
        print("No había cambios: no se recalculó ningún embedding.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
