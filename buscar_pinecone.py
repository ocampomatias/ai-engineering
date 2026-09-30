"""Búsqueda híbrida puntual contra Pinecone (pre-entrega 4).

Uso:

    python buscar_pinecone.py "¿Qué significa el error TMB-1009?"
    python buscar_pinecone.py "¿Cuánto dura el acceso a producción?" --categoria seguridad
    python buscar_pinecone.py "límites de la API" --audiencia comercios --desde 2026-08-01

Muestra los top-5 con la posición que le dio cada recuperador (Pinecone y BM25)
y el puntaje de la fusión RRF.
"""

import argparse
import logging
import sys
from datetime import date

from rag_pinecone import ConfigError, IndiceError, RAGSystem, cargar_config

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s | %(name)s | %(message)s")


def main() -> int:
    parser = argparse.ArgumentParser(description="Búsqueda híbrida en Pinecone + BM25")
    parser.add_argument("consulta")
    parser.add_argument("--categoria", help="filtra por categoría (ej. seguridad, producto)")
    parser.add_argument("--audiencia", choices=["interna", "comercios"])
    parser.add_argument("--desde", type=date.fromisoformat, help="actualizado desde AAAA-MM-DD")
    args = parser.parse_args()

    condiciones: list[dict[str, object]] = []
    if args.categoria:
        condiciones.append({"categoria": {"$eq": args.categoria}})
    if args.audiencia:
        condiciones.append({"audiencia": {"$eq": args.audiencia}})
    if args.desde:
        condiciones.append({"actualizado": {"$gte": int(args.desde.strftime("%Y%m%d"))}})
    filtro = {"$and": condiciones} if len(condiciones) > 1 else (condiciones[0] if condiciones else None)

    try:
        sistema = RAGSystem(cargar_config())
    except (ConfigError, IndiceError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1

    print(f"\nConsulta: {args.consulta}" + (f"\nFiltro: {filtro}" if filtro else ""))
    for r in sistema.explicar(args.consulta, filtro=filtro):
        origen = f"Pinecone #{r.posicion_vectorial or '-'} · BM25 #{r.posicion_bm25 or '-'}"
        pagina = f", página {r.pagina}" if r.pagina else ""
        print(f"\n{r.posicion}. {r.fuente}{pagina} — {r.seccion}  [RRF {r.puntaje_rrf:.4f} | {origen}]")
        print(f"   {r.extracto}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
