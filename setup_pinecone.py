"""Inicialización de Pinecone (pre-entrega 4): crea el índice serverless si no existe.

Uso:

    python setup_pinecone.py

Lee PINECONE_API_KEY e INDEX_NAME del .env. Si el índice ya existe, comprueba
que su dimensión y su métrica coincidan con el modelo de embeddings y no lo
toca. `ingestar_pinecone.py` hace este mismo paso antes de subir, así que
correr este script primero es opcional.
"""

import logging
import sys

from rag_pinecone import ConfigError, IndiceError, asegurar_indice, cargar_config, conectar
from rag_pinecone.ingesta import crear_embeddings, dimension_de

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s | %(name)s | %(message)s")
for ruidoso in ("httpx", "httpcore", "huggingface_hub", "urllib3", "pinecone"):
    logging.getLogger(ruidoso).setLevel(logging.WARNING)


def main() -> int:
    config = cargar_config()
    try:
        cliente = conectar(config)
        dimension = dimension_de(crear_embeddings(config))
        creado = asegurar_indice(cliente, config, dimension)
    except (ConfigError, IndiceError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1

    estado = "creado" if creado else "ya existía y es compatible"
    print(
        f"\nÍndice '{config.indice}' {estado}: serverless en {config.cloud}/{config.region}, "
        f"dimensión {dimension}, métrica {config.metrica}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
