"""Pruebas contra la API levantada (pre-entrega 7): carga concurrente y flujo de aprobación.

Uso, con la API corriendo (`uvicorn app.main:app --port 8000`):

    python probar_api.py --carga            # 5 pedidos a la vez
    python probar_api.py --hitl             # un reembolso: espera aprobación y lo aprueba
    python probar_api.py --hitl --rechazar  # el mismo, pero lo rechaza

Deja el resultado en evidencia/carga_api.json y evidencia/hitl_api.json.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

EVIDENCIA = Path(__file__).resolve().parent / "evidencia"
FINALES = {"DONE", "FAILED"}

PEDIDOS_CARGA = [
    "Analizá los últimos 5 cobros de Ferretería López: qué porcentaje se rechazó y qué tiene que "
    "hacer el comercio con cada código de error.",
    "Compará la tasa de rechazo de Librería Andina y Café del Puerto. ¿Cuál está peor y por qué?",
    "¿Cuánto cobró en total Café del Puerto en sus últimos 5 cobros aprobados?",
    "¿Qué cobros de Farmacia López Hermanos están en revisión y qué significa su código de error?",
    "¿Qué dice la documentación sobre el error TMB-2031 y cuántos cobros de Ferretería López lo tienen?",
]
PEDIDO_HITL = (
    "Reembolsá los cobros cob_7E8Q4 y cob_7F3K2 de Ferretería López. Antes confirmá el estado y "
    "el monto de cada uno."
)


async def esperar(http: httpx.AsyncClient, job_id: str, estados: set[str], tope_s: float = 300) -> dict[str, Any]:
    inicio = time.perf_counter()
    while time.perf_counter() - inicio < tope_s:
        job = (await http.get(f"/tasks/{job_id}")).raise_for_status().json()
        if job["estado"] in estados:
            return job
        await asyncio.sleep(1)
    raise TimeoutError(f"{job_id} sigue en {job['estado']} después de {tope_s:.0f} s")


async def un_pedido(http: httpx.AsyncClient, pedido: str) -> dict[str, Any]:
    inicio = time.perf_counter()
    r = (await http.post("/tasks", json={"pedido": pedido})).raise_for_status()
    ms_post = (time.perf_counter() - inicio) * 1000
    job = await esperar(http, r.json()["id"], FINALES)
    return {
        "job_id": job["id"],
        "pedido": pedido,
        "estado": job["estado"],
        "ms_post": round(ms_post, 1),
        "s_total": round(time.perf_counter() - inicio, 1),
        "agentes": [a["agente"] for a in (job["resultado"] or {}).get("aportes", [])],
        "respuesta": (job["resultado"] or {}).get("respuesta_final") or job["error"],
    }


async def carga(http: httpx.AsyncClient) -> int:
    print(f"Enviando {len(PEDIDOS_CARGA)} pedidos a la vez...")
    inicio = time.perf_counter()
    resultados = await asyncio.gather(*(un_pedido(http, p) for p in PEDIDOS_CARGA))
    total = time.perf_counter() - inicio
    for r in resultados:
        print(f"- {r['estado']:6} POST {r['ms_post']:6.1f} ms | total {r['s_total']:5.1f} s | "
              f"{' -> '.join(r['agentes']) or '-'} | {r['pedido'][:60]}")
    secuencial = sum(r["s_total"] for r in resultados)
    print(f"\nTodo junto: {total:.1f} s (la suma de cada uno es {secuencial:.1f} s: corrieron en paralelo).")
    (EVIDENCIA / "carga_api.json").write_text(
        json.dumps({"s_total": round(total, 1), "pedidos": resultados}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0 if all(r["estado"] == "DONE" for r in resultados) else 1


async def hitl(http: httpx.AsyncClient, rechazar: bool) -> int:
    job = (await http.post("/tasks", json={"pedido": PEDIDO_HITL})).raise_for_status().json()
    print(f"Pedido encolado: {job['id']}\n  {PEDIDO_HITL}")
    job = await esperar(http, job["id"], FINALES | {"WAITING_APPROVAL"})
    if job["estado"] != "WAITING_APPROVAL":
        print(f"No se detuvo a esperar aprobación: {job['estado']} {job['error'] or ''}")
        return 1
    print(f"\nESPERA APROBACIÓN: {job['aprobacion']['pregunta']}\n\nAnálisis de los agentes:\n"
          f"{job['aprobacion']['analisis']}\n")
    revision = {"aprobado": not rechazar, "revisor": "probar_api.py",
                "comentario": "rechazado en la prueba" if rechazar else "verificado en la prueba"}
    r = await http.post(f"/tasks/{job['id']}/approve", json=revision)
    print(f"POST /approve {revision['aprobado']} -> {r.status_code}")
    final = await esperar(http, job["id"], FINALES)
    if final["estado"] == "DONE":
        print(f"\nDONE. Resultado de la acción:\n{final['resultado']['resultado_accion'] or '(no se ejecutó)'}")
    else:
        print(f"\nFAILED: {final['error']}")
    (EVIDENCIA / "hitl_api.json").write_text(
        json.dumps({"en_espera": job, "final": final}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return 0 if final["estado"] == "DONE" else 1


async def main() -> int:
    parser = argparse.ArgumentParser(description="Prueba la API del orquestador")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--carga", action="store_true", help="5 pedidos concurrentes")
    parser.add_argument("--hitl", action="store_true", help="flujo de aprobación de un reembolso")
    parser.add_argument("--rechazar", action="store_true", help="con --hitl: rechaza en vez de aprobar")
    args = parser.parse_args()
    if not (args.carga or args.hitl):
        parser.error("elegí --carga, --hitl o los dos")
    EVIDENCIA.mkdir(exist_ok=True)
    async with httpx.AsyncClient(base_url=args.url, timeout=30) as http:
        codigo = await carga(http) if args.carga else 0
        if args.hitl:
            codigo = await hitl(http, args.rechazar) or codigo
    return codigo


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
