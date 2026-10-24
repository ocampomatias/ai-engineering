"""Pruebas contra la API levantada (pre-entrega 7): carga concurrente, aprobación y reinicio.

Uso, con la API corriendo (`uvicorn app.main:app --port 8000`):

    python probar_api.py --carga            # 5 pedidos a la vez
    python probar_api.py --hitl             # un reembolso: espera aprobación y lo aprueba
    python probar_api.py --hitl --rechazar  # el mismo, pero lo rechaza

Deja el resultado en evidencia/carga_api.json y evidencia/hitl_api.json.

Sin una API levantada (este modo arranca la suya en el puerto 8001, con el .env real):

    python probar_api.py --reinicio         # mata la API a mitad del reembolso y la vuelve a levantar

Deja evidencia/reinicio_api.json y el log de los dos servidores en evidencia/log_reinicio_api.log.
"""

import argparse
import asyncio
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

RAIZ = Path(__file__).resolve().parent
EVIDENCIA = RAIZ / "evidencia"
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


def percentil(valores: list[float], p: float) -> float:
    """Rango más cercano: con 5 muestras, p95 es la más lenta (no se interpola)."""
    orden = sorted(valores)
    return orden[max(0, math.ceil(p / 100 * len(orden)) - 1)]


assert percentil([5, 1, 4, 2, 3], 95) == 5 and percentil([5, 1, 4, 2, 3], 50) == 3


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
    latencia = {
        f"{nombre}_p{p}": percentil([r[campo] for r in resultados], p)
        for nombre, campo in (("total_s", "s_total"), ("post_ms", "ms_post"))
        for p in (50, 95)
    }
    print(f"Latencia total: p50 {latencia['total_s_p50']:.1f} s, p95 {latencia['total_s_p95']:.1f} s | "
          f"POST /tasks: p50 {latencia['post_ms_p50']:.1f} ms, p95 {latencia['post_ms_p95']:.1f} ms")
    print(f"(p95 por rango más cercano: con {len(resultados)} muestras es la más lenta.)")
    (EVIDENCIA / "carga_api.json").write_text(
        json.dumps({"s_total": round(total, 1), "latencia": latencia, "pedidos": resultados},
                   ensure_ascii=False, indent=2),
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


# --- Reinicio: una caída real a mitad del trabajo -----------------------------

PUERTO_REINICIO = 8001
LOG_REINICIO = EVIDENCIA / "log_reinicio_api.log"
# Lo loguea el especialista al empezar (orquestador/agents/especialista.py). Para entonces el
# checkpoint posterior a la decisión del supervisor ya está guardado (durability="sync").
MARCA_INVESTIGADOR = "[investigador] recibe"


def levantar_api(modo: str) -> tuple[subprocess.Popen[bytes], Any]:  # noqa: ANN401
    """La API propia en el puerto 8001, con stdout y stderr al log. `modo` "w" la primera vez, "a" después."""
    log = LOG_REINICIO.open(modo, encoding="utf-8")
    # La app carga el .env real por su cuenta (load_dotenv en app/main.py); acá no se lee.
    entorno = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    proceso = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PUERTO_REINICIO)],
        cwd=RAIZ, stdout=log, stderr=subprocess.STDOUT, env=entorno,
    )
    return proceso, log


def leer_log(desde: int = 0, hasta: int | None = None) -> str:
    """El log entre dos bytes (lo escrito por la API 2 empieza donde terminó la API 1)."""
    return LOG_REINICIO.read_bytes()[desde:hasta].decode("utf-8", errors="replace")


async def esperar_salud(http: httpx.AsyncClient, proceso: subprocess.Popen[bytes], tope_s: float = 180) -> None:
    inicio = time.perf_counter()
    while time.perf_counter() - inicio < tope_s:
        if proceso.poll() is not None:
            raise RuntimeError(f"La API terminó al arrancar (código {proceso.returncode}); ver {LOG_REINICIO}")
        try:
            if (await http.get("/health")).status_code == 200:
                return
        except httpx.TransportError:
            pass  # todavía no escucha
        await asyncio.sleep(1)
    raise TimeoutError(f"La API no respondió /health en {tope_s:.0f} s; ver {LOG_REINICIO}")


async def esperar_en_log(texto: str, proceso: subprocess.Popen[bytes], tope_s: float = 180) -> None:
    inicio = time.perf_counter()
    while time.perf_counter() - inicio < tope_s:
        if texto in leer_log():
            return
        if proceso.poll() is not None:
            raise RuntimeError(f"La API terminó antes de loguear {texto!r}; ver {LOG_REINICIO}")
        await asyncio.sleep(0.2)
    raise TimeoutError(f"No apareció {texto!r} en el log en {tope_s:.0f} s")


def apagar(proceso: subprocess.Popen[bytes], log: Any) -> None:  # noqa: ANN401
    """kill(): en Windows es TerminateProcess, una caída sin apagado ordenado (ni lifespan ni confirmaciones)."""
    if proceso.poll() is None:
        proceso.kill()
    proceso.wait(timeout=30)
    log.close()


async def reinicio() -> int:
    url = f"http://127.0.0.1:{PUERTO_REINICIO}"
    # Otra API contra el mismo Redis (la de --url, por defecto) le robaría los mensajes a la prueba.
    ocupados = ((url, "el puerto de la prueba"), ("http://localhost:8000", "otra API, quizá contra el mismo Redis"))
    for otra, motivo in ocupados:
        async with httpx.AsyncClient(base_url=otra, timeout=5) as http:
            try:
                await http.get("/health")
                print(f"Ya hay algo escuchando en {otra} ({motivo}): cerralo antes de la prueba.")
                return 1
            except httpx.TransportError:
                pass  # libre

    inicio = time.perf_counter()
    linea: list[dict[str, Any]] = []

    def marca(evento: str) -> None:
        linea.append({"s": round(time.perf_counter() - inicio, 1), "evento": evento})
        print(f"[{linea[-1]['s']:6.1f} s] {evento}")

    # Un cliente por servidor: las conexiones abiertas con la API 1 mueren con ella.
    proceso, log = levantar_api("w")
    try:
        async with httpx.AsyncClient(base_url=url, timeout=30) as http:
            await esperar_salud(http, proceso)
            marca(f"API 1 arriba (pid {proceso.pid})")
            job = (await http.post("/tasks", json={"pedido": PEDIDO_HITL})).raise_for_status().json()
            marca(f"pedido encolado: {job['id']}")
            await esperar_en_log(MARCA_INVESTIGADOR, proceso)
            marca("el supervisor ya decidió y el investigador empezó")
            al_matar = (await http.get(f"/tasks/{job['id']}")).raise_for_status().json()["estado"]
    finally:
        apagar(proceso, log)
    marca(f"API 1 muerta con kill() (el trabajo estaba en {al_matar})")

    corte = LOG_REINICIO.stat().st_size
    proceso, log = levantar_api("a")
    try:
        async with httpx.AsyncClient(base_url=url, timeout=30) as http:
            await esperar_salud(http, proceso)
            marca(f"API 2 arriba (pid {proceso.pid})")
            en_espera = await esperar(http, job["id"], FINALES | {"WAITING_APPROVAL"})
            marca(f"el trabajo llegó a {en_espera['estado']}")
            if en_espera["estado"] != "WAITING_APPROVAL":
                print(f"No se detuvo a esperar aprobación: {en_espera['error'] or ''}")
                return 1
            revision = {"aprobado": True, "revisor": "probar_api.py",
                        "comentario": "verificado en la prueba, después del reinicio"}
            r = await http.post(f"/tasks/{job['id']}/approve", json=revision)
            marca(f"POST /approve {revision['aprobado']} -> {r.status_code}")
            final = await esperar(http, job["id"], FINALES)
            marca(f"el trabajo terminó en {final['estado']}")
            # Las trazas salen en lotes cada 5 s: sin esta espera, kill() se lleva el span `firma`.
            await asyncio.sleep(8)
    finally:
        apagar(proceso, log)

    api_1, api_2 = leer_log(0, corte), leer_log(corte)
    recuperacion = [ln for ln in api_2.splitlines()
                    if "[recuperación]" in ln or "reanuda desde el checkpoint" in ln]
    print("\nEn el log de la API 2:\n" + "\n".join(f"  {ln}" for ln in recuperacion))
    if final["estado"] == "DONE":
        print(f"\nDONE. Resultado de la acción:\n{final['resultado']['resultado_accion'] or '(no se ejecutó)'}")
    else:
        print(f"\nFAILED: {final['error']}")
    (EVIDENCIA / "reinicio_api.json").write_text(
        json.dumps({
            "job_id": job["id"],
            "linea_de_tiempo": linea,
            "estado_al_matar": al_matar,
            "log_recuperacion": recuperacion,
            # La decisión que la API 1 dejó en el checkpoint no se repite en la API 2.
            "decisiones_del_supervisor": {"api_1": api_1.count("[supervisor] evalúa"),
                                          "api_2": api_2.count("[supervisor] evalúa")},
            "arranques_del_investigador": {"api_1": api_1.count(MARCA_INVESTIGADOR),
                                           "api_2": api_2.count(MARCA_INVESTIGADOR)},
            "en_espera": en_espera,
            "final": final,
        }, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return 0 if final["estado"] == "DONE" else 1


async def main() -> int:
    parser = argparse.ArgumentParser(description="Prueba la API del orquestador")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--carga", action="store_true", help="5 pedidos concurrentes")
    parser.add_argument("--hitl", action="store_true", help="flujo de aprobación de un reembolso")
    parser.add_argument("--rechazar", action="store_true", help="con --hitl: rechaza en vez de aprobar")
    parser.add_argument("--reinicio", action="store_true",
                        help=f"levanta su propia API en el puerto {PUERTO_REINICIO}, la mata a mitad de un "
                             "reembolso, la vuelve a levantar, aprueba y espera DONE (no usa --url)")
    args = parser.parse_args()
    if not (args.carga or args.hitl or args.reinicio):
        parser.error("elegí --carga, --hitl, --reinicio o varios")
    EVIDENCIA.mkdir(exist_ok=True)
    async with httpx.AsyncClient(base_url=args.url, timeout=30) as http:
        codigo = await carga(http) if args.carga else 0
        if args.hitl:
            codigo = await hitl(http, args.rechazar) or codigo
    if args.reinicio:
        codigo = await reinicio() or codigo
    return codigo


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
