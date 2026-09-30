"""Evaluación de la recuperación (pre-entrega 4): Recall@k y Precision@k sobre un golden set.

Uso:

    python evaluate.py                        # k=5, compara híbrido, vectorial y BM25
    python evaluate.py --peso-vectorial 0.7   # otro peso para la fusión
    python evaluate.py --detalle              # también la tabla pregunta por pregunta

Corre cada pregunta de evaluacion/golden_set.json contra el índice de Pinecone
con los tres modos del RAGSystem, imprime el reporte en consola y lo guarda en
evidencia/evaluacion_pinecone.json.
"""

import argparse
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path

from rag_pinecone import (
    MODOS,
    ConfigError,
    GoldenSet,
    IndiceError,
    RAGSystem,
    agregar,
    cargar_config,
    medir_pregunta,
)
from rag_pinecone.metricas import MetricasPregunta, Recuperado, precision_maxima

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

RAIZ = Path(__file__).resolve().parent
GOLDEN = RAIZ / "evaluacion" / "golden_set.json"
SALIDA = RAIZ / "evidencia" / "evaluacion_pinecone.json"

logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s | %(name)s | %(message)s")


def validar_golden(golden: GoldenSet, sistema: RAGSystem) -> None:
    """Cada respuesta tiene que existir en el documento esperado, o la métrica mide mal."""
    textos = {}
    for doc in sistema.corpus:
        textos.setdefault(doc.metadata["documento_id"], []).append(" ".join(doc.page_content.split()))
    for p in golden.preguntas:
        del_documento = textos.get(p.documento_id_esperado)
        if del_documento is None:
            raise ValueError(f"{p.id}: el documento {p.documento_id_esperado!r} no está en el índice")
        buscado = " ".join(p.contiene.split()).casefold()
        if not any(buscado in t.casefold() for t in del_documento):
            raise ValueError(f"{p.id}: {p.contiene!r} no aparece en {p.documento_id_esperado}")


def evaluar_modo(
    sistema: RAGSystem, golden: GoldenSet, modo: str, k: int
) -> tuple[list[MetricasPregunta], float]:
    resultados = []
    inicio = time.perf_counter()
    for p in golden.preguntas:
        documentos = sistema.buscar(p.pregunta, k=k, modo=modo)  # type: ignore[arg-type]
        recuperados = [
            Recuperado(documento_id=d.metadata["documento_id"], texto=d.page_content)
            for d in documentos
        ]
        resultados.append(medir_pregunta(p, recuperados, k))
    return resultados, (time.perf_counter() - inicio) * 1000 / len(golden.preguntas)


def _pct(valor: float) -> str:
    return f"{valor * 100:5.1f} %"


def main() -> int:
    parser = argparse.ArgumentParser(description="Recall@k y Precision@k sobre el golden set")
    parser.add_argument("--k", type=int, default=5, help="fragmentos por consulta (default 5)")
    parser.add_argument("--peso-vectorial", type=float, help="peso del recuperador vectorial en RRF")
    parser.add_argument("--golden", type=Path, default=GOLDEN, help="archivo del golden set")
    parser.add_argument("--detalle", action="store_true", help="tabla pregunta por pregunta")
    parser.add_argument("--salida", type=Path, default=SALIDA, help="JSON con los resultados")
    args = parser.parse_args()

    golden = GoldenSet.model_validate_json(args.golden.read_text(encoding="utf-8"))
    overrides: dict[str, object] = {"top_k": args.k, "candidatos": max(10, args.k * 2)}
    if args.peso_vectorial is not None:
        overrides["peso_vectorial"] = args.peso_vectorial
    config = cargar_config(**overrides)

    try:
        sistema = RAGSystem(config)
    except (ConfigError, IndiceError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    validar_golden(golden, sistema)

    fragmentos_por_doc = Counter(d.metadata["documento_id"] for d in sistema.corpus)
    techo = sum(precision_maxima(p, fragmentos_por_doc, args.k) for p in golden.preguntas) / len(
        golden.preguntas
    )
    tipos = Counter(p.tipo for p in golden.preguntas)

    print(
        f"\nEvaluación sobre {len(golden.preguntas)} preguntas ({tipos['lexica']} léxicas, "
        f"{tipos['semantica']} semánticas), k={args.k}"
    )
    print(
        f"Índice '{config.indice}', namespace '{config.namespace}', {len(sistema.corpus)} "
        f"fragmentos. Pesos RRF vectorial/BM25: {config.pesos}, c={config.rrf_c}."
    )

    reporte: dict[str, object] = {
        "k": args.k,
        "indice": config.indice,
        "namespace": config.namespace,
        "fragmentos": len(sistema.corpus),
        "pesos": config.pesos,
        "precision_maxima_posible": round(techo, 4),
        "modos": {},
    }
    por_modo: dict[str, list[MetricasPregunta]] = {}

    encabezado = f"\n{'Modo':<10} {'Recall@k':>9} {'Prec@k':>9} {'F1':>7} {'MRR':>7} {'Frag@k':>9} {'ms/consulta':>12}"
    print(encabezado)
    print("-" * (len(encabezado) - 1))
    for modo in MODOS:
        resultados, ms = evaluar_modo(sistema, golden, modo, args.k)
        por_modo[modo] = resultados
        total = agregar(resultados, args.k)
        por_tipo = {
            tipo: agregar([r for r, p in zip(resultados, golden.preguntas) if p.tipo == tipo], args.k)
            for tipo in sorted(tipos)
        }
        print(
            f"{modo:<10} {_pct(total.recall):>9} {_pct(total.precision):>9} {total.f1:>7.3f} "
            f"{total.mrr:>7.3f} {_pct(total.acierto_fragmento):>9} {ms:>10.0f} ms"
        )
        reporte["modos"][modo] = {  # type: ignore[index]
            "total": total.model_dump(),
            "por_tipo": {t: m.model_dump() for t, m in por_tipo.items()},
            "ms_por_consulta": round(ms, 1),
            "preguntas": [r.model_dump() for r in resultados],
        }

    print(
        f"\nPrecision@{args.k} máxima posible con este corpus: {_pct(techo).strip()} (hay documentos "
        f"con menos de {args.k} fragmentos)."
    )

    print("\nPor tipo de pregunta (Recall@k / MRR / acierto de fragmento):")
    for tipo in sorted(tipos):
        partes = []
        for modo in MODOS:
            m = reporte["modos"][modo]["por_tipo"][tipo]  # type: ignore[index]
            partes.append(f"{modo} {_pct(m['recall']).strip()} / {m['mrr']:.2f} / {_pct(m['acierto_fragmento']).strip()}")
        print(f"  {tipo:<9} " + " | ".join(partes))

    if args.detalle:
        print(f"\n{'Pregunta':<26} " + " ".join(f"{m:>16}" for m in MODOS))
        for i, p in enumerate(golden.preguntas):
            celdas = []
            for modo in MODOS:
                r = por_modo[modo][i]
                pos = f"#{r.posicion}" if r.posicion else "--"
                frag = "frag" if r.acierto_fragmento else "    "
                celdas.append(f"{pos:>4} P={r.precision:.1f} {frag}")
            print(f"{p.id:<26} " + " ".join(f"{c:>16}" for c in celdas))
        print("#n: posición del primer fragmento del documento esperado. frag: trajo el fragmento exacto.")

    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
    salida = args.salida.resolve()
    print(f"\nResultados guardados en {salida.relative_to(RAIZ) if salida.is_relative_to(RAIZ) else salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
