"""Tests de la API de producción (pre-entrega 7). Sin API keys, sin red y sin Redis real.

- Redis es `fakeredis` (estado de trabajos, cola, reembolsos) y el checkpointer
  es `InMemorySaver`: el grafo, la interrupción y la reanudación son los reales.
- El supervisor y la síntesis son el modelo guionado de test_orquestador.py; los
  especialistas son nodos falsos que devuelven un aporte fijo.
- La API se prueba por HTTP con httpx + ASGITransport, con los workers corriendo.
- Un reinicio se simula con dos `lifespan` seguidos sobre el mismo Redis y el
  mismo checkpointer: lo que sobrevive a una caída real.

    python -m pytest -q tests/test_api.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from fakeredis import FakeAsyncRedis
from redis.asyncio import Redis as RedisAsync
from redis.exceptions import ConnectionError as RedisConnectionError
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError
from test_orquestador import SupervisorGuionado, decision

from app import observability
from app.graph import construir_grafo, serializador_redis
from app.hitl import Accion, Revision, detectar_accion
from app.jobs import COLA, PROCESANDO, REEMBOLSOS, EstadoJob, Job, JobStore
from app.main import Recursos, crear_app
from orquestador import Aporte

APROBADO = "cob_7E8Q4"  # aprobado, 8.700,00 ARS (agente/datos_cobros.json)
RECHAZADO = "cob_7F3K2"


def especialista_falso(nombre: str, falla: bool = False) -> Any:  # noqa: ANN401
    async def nodo(estado: dict[str, Any]) -> dict[str, Any]:
        if falla:
            raise RuntimeError("se cayó la API del modelo")
        aporte = Aporte(agente=nombre, instruccion=estado["instruccion"], contenido="datos", herramientas=[], ms=1)
        return {"aportes": [aporte], "messages": [AIMessage("datos", name=nombre)]}

    return nodo


@pytest.fixture(autouse=True)
def sin_phoenix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIX_DISABLED", "1")


@pytest.fixture
def redis() -> FakeAsyncRedis:
    falso = FakeAsyncRedis(decode_responses=True)

    # fakeredis 2.40: BLMOVE no bloquea (vuelve None al instante) ni mueve lo que llega mientras
    # espera, y los workers girarían en vacío. Y BRPOPLPUSH, cancelado, sigue consumiendo como un
    # fantasma: se llevaría el mensaje recuperado en el segundo arranque. Sondear con LMOVE no deja
    # nada colgado. En Redis real se usa BLMOVE.
    async def blmove(origen: str, destino: str, timeout: float, src: str = "LEFT", dest: str = "RIGHT") -> Any:  # noqa: ANN401
        for _ in range(int(timeout / 0.01)):
            if (crudo := await falso.lmove(origen, destino, src, dest)) is not None:
                return crudo
            await asyncio.sleep(0.01)
        return None

    falso.blmove = blmove
    return falso


def guion() -> SupervisorGuionado:
    return SupervisorGuionado(
        decisiones=[decision("investigador", "traé los cobros"), decision("sintesis")],
        respuesta_sintesis="Análisis listo.",
    )


async def cola_vacia(redis: FakeAsyncRedis) -> bool:
    """True cuando no queda nada en la cola ni en proceso (el worker confirma apenas guarda el estado)."""
    for _ in range(100):
        if await redis.llen(COLA) == await redis.llen(PROCESANDO) == 0:
            return True
        await asyncio.sleep(0.01)
    return False


@asynccontextmanager
async def cliente(
    redis: FakeAsyncRedis,
    *,
    falla: bool = False,
    modelo: SupervisorGuionado | None = None,
    checkpointer: InMemorySaver | None = None,
    investigador: Any = None,  # noqa: ANN401
    revisar_cola: bool = True,
    workers: int = 2,
) -> AsyncIterator[httpx.AsyncClient]:
    """La API con sus workers. Para simular un reinicio, dos llamadas con el mismo `checkpointer`.

    `revisar_cola=False` en el lifespan que simula la caída: ahí queda trabajo en proceso a propósito.
    """
    modelo = modelo or guion()
    checkpointer = checkpointer or InMemorySaver()

    @asynccontextmanager
    async def fabrica() -> AsyncIterator[Recursos]:
        store = JobStore(redis)
        grafo = construir_grafo(
            checkpointer,
            store.registrar_reembolso,
            modelo,
            investigador=investigador or especialista_falso("investigador", falla),
            analista=especialista_falso("analista"),
        )
        yield Recursos(store, grafo)

    app = crear_app(fabrica, workers=workers)
    async with app.router.lifespan_context(app):
        transporte = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transporte, base_url="http://test") as http:
            yield http
        if revisar_cola:
            assert await cola_vacia(redis), "quedaron mensajes en la cola o en proceso"


async def esperar(http: httpx.AsyncClient, job_id: str, *estados: str, intentos: int = 200) -> dict[str, Any]:
    for _ in range(intentos):
        job = (await http.get(f"/tasks/{job_id}")).json()
        if job["estado"] in estados:
            return job
        await asyncio.sleep(0.02)
    raise AssertionError(f"el trabajo quedó en {job['estado']}")


# --- Detección de tareas críticas -------------------------------------------


def test_detectar_accion() -> None:
    accion = detectar_accion(f"Reembolsá {APROBADO} y {RECHAZADO}, y también {APROBADO}")
    assert accion is not None and accion.cobro_ids == [APROBADO, RECHAZADO]
    assert detectar_accion(f"Devolvele la plata del cobro {APROBADO}") is not None
    assert detectar_accion(f"¿Por qué se rechazó {RECHAZADO}?") is None  # sin verbo de reembolso
    assert detectar_accion("Reembolsá el último cobro de Ferretería López") is None  # sin ID


# --- Flujo normal -------------------------------------------------------------


async def test_post_encola_y_el_worker_termina(redis: FakeAsyncRedis) -> None:
    async with cliente(redis) as http:
        r = await http.post("/tasks", json={"pedido": "Analizá los cobros de Ferretería López"})
        assert r.status_code == 202
        assert r.json()["estado"] == "PENDING"
        job = await esperar(http, r.json()["id"], "DONE", "FAILED")
    assert job["estado"] == "DONE"
    assert job["resultado"]["respuesta_final"] == "Análisis listo."
    assert [a["agente"] for a in job["resultado"]["aportes"]] == ["investigador"]
    assert job["resultado"]["resultado_accion"] is None


async def test_validacion_y_404(redis: FakeAsyncRedis) -> None:
    async with cliente(redis) as http:
        assert (await http.post("/tasks", json={"pedido": "x"})).status_code == 422
        assert (await http.get("/tasks/no-existe")).status_code == 404
        assert (await http.get("/health")).json() == {"estado": "ok"}
        consola = await http.get("/")
        assert consola.status_code == 200 and "Libro de operaciones" in consola.text
        assert (await http.get("/static/consola.js")).status_code == 200
        aprobar = {"aprobado": True, "revisor": "ana"}
        assert (await http.post("/tasks/no-existe/approve", json=aprobar)).status_code == 404


async def test_una_excepcion_deja_el_trabajo_en_failed(redis: FakeAsyncRedis) -> None:
    async with cliente(redis, falla=True) as http:
        r = await http.post("/tasks", json={"pedido": "Analizá los cobros de Ferretería López"})
        job = await esperar(http, r.json()["id"], "DONE", "FAILED")
        assert job["estado"] == "FAILED"
        assert "se cayó la API del modelo" in job["error"]
        # El worker sigue vivo: el próximo trabajo (el guion ya va directo a la síntesis) termina.
        r = await http.post("/tasks", json={"pedido": "Analizá los cobros de Ferretería López"})
        assert (await esperar(http, r.json()["id"], "DONE", "FAILED"))["estado"] == "DONE"


async def test_un_error_despues_del_grafo_tambien_deja_failed(
    redis: FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    def romper(final: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("estado inesperado")

    monkeypatch.setattr("app.worker._resultado", romper)
    async with cliente(redis) as http:
        r = await http.post("/tasks", json={"pedido": "Analizá los cobros de Ferretería López"})
        job = await esperar(http, r.json()["id"], "DONE", "FAILED")
    assert job["estado"] == "FAILED"
    assert job["error"] == "ValueError: estado inesperado"


def test_el_serializador_de_redis_reconstruye_los_modelos_del_estado() -> None:
    """Lo que guarda AsyncRedisSaver vuelve con los mismos tipos (antes volvían sobres `lc`)."""
    estado = {
        "aportes": [Aporte(agente="investigador", instruccion="x", contenido="y", herramientas=[], ms=1)],
        "decisiones": [decision("sintesis")],
        "accion": Accion(cobro_ids=[APROBADO]),
        "revision": Revision(aprobado=True, revisor="ana"),
    }
    serde = serializador_redis()
    assert serde.loads_typed(serde.dumps_typed(estado)) == estado


# --- Human-in-the-loop --------------------------------------------------------


async def test_reembolso_espera_aprobacion_y_se_ejecuta_una_vez(redis: FakeAsyncRedis) -> None:
    async with cliente(redis) as http:
        r = await http.post("/tasks", json={"pedido": f"Reembolsá {APROBADO} y {RECHAZADO}"})
        job_id = r.json()["id"]
        job = await esperar(http, job_id, "WAITING_APPROVAL", "DONE", "FAILED")
        assert job["estado"] == "WAITING_APPROVAL"
        assert job["aprobacion"]["accion"]["cobro_ids"] == [APROBADO, RECHAZADO]
        assert job["aprobacion"]["analisis"] == "Análisis listo."
        assert await redis.hlen(REEMBOLSOS) == 0  # nada se ejecutó todavía

        r = await http.post(f"/tasks/{job_id}/approve", json={"aprobado": True, "revisor": "ana"})
        assert r.status_code == 202
        segunda = await http.post(f"/tasks/{job_id}/approve", json={"aprobado": True, "revisor": "ana"})
        assert segunda.status_code == 409
        job = await esperar(http, job_id, "DONE", "FAILED")

    assert job["estado"] == "DONE"
    assert job["resultado"]["revision"]["revisor"] == "ana"
    assert "cob_7E8Q4: reembolso de 8.700,00 ARS registrado" in job["resultado"]["resultado_accion"]
    assert "cob_7F3K2: está rechazado" in job["resultado"]["resultado_accion"]
    assert list(await redis.hkeys(REEMBOLSOS)) == [APROBADO]
    assert await redis.llen(COLA) == 0


async def test_reembolso_rechazado_no_se_ejecuta(redis: FakeAsyncRedis) -> None:
    async with cliente(redis) as http:
        r = await http.post("/tasks", json={"pedido": f"Reembolsá {APROBADO}"})
        job_id = r.json()["id"]
        await esperar(http, job_id, "WAITING_APPROVAL")
        revision = {"aprobado": False, "revisor": "ana", "comentario": "no corresponde"}
        await http.post(f"/tasks/{job_id}/approve", json=revision)
        job = await esperar(http, job_id, "DONE", "FAILED")
    assert job["estado"] == "DONE"
    assert job["resultado"]["revision"]["aprobado"] is False
    assert job["resultado"]["resultado_accion"] is None
    assert await redis.hlen(REEMBOLSOS) == 0


async def test_aprobar_un_trabajo_que_no_espera_da_409(redis: FakeAsyncRedis) -> None:
    async with cliente(redis) as http:
        r = await http.post("/tasks", json={"pedido": "Analizá los cobros de Ferretería López"})
        job_id = r.json()["id"]
        await esperar(http, job_id, "DONE")
        r = await http.post(f"/tasks/{job_id}/approve", json={"aprobado": True, "revisor": "ana"})
    assert r.status_code == 409


async def test_la_firma_es_atomica_si_falla_el_encolado_se_puede_volver_a_firmar(
    redis: FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    def lpush_caido(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        raise RedisConnectionError("se cortó antes de encolar")

    revision = {"aprobado": True, "revisor": "ana"}
    async with cliente(redis) as http:
        r = await http.post("/tasks", json={"pedido": f"Reembolsá {APROBADO}"})
        job_id = r.json()["id"]
        await esperar(http, job_id, "WAITING_APPROVAL")
        # En la clase: alcanza tanto al LPUSH suelto como al que va dentro de la transacción.
        monkeypatch.setattr(RedisAsync, "lpush", lpush_caido)
        caida = await http.post(f"/tasks/{job_id}/approve", json=revision)
        monkeypatch.undo()
        assert caida.status_code == 503
        # No quedó un PENDING sin mensaje: sigue esperando la firma y se puede volver a firmar.
        assert (await http.get(f"/tasks/{job_id}")).json()["estado"] == "WAITING_APPROVAL"
        dos = await asyncio.gather(*(http.post(f"/tasks/{job_id}/approve", json=revision) for _ in range(2)))
        assert sorted(r.status_code for r in dos) == [202, 409]  # dos firmas a la vez: pasa una
        job = await esperar(http, job_id, "DONE", "FAILED")
    assert job["estado"] == "DONE"
    assert list(await redis.hkeys(REEMBOLSOS)) == [APROBADO]


# --- Cola confiable y recuperación tras un reinicio ----------------------------

PEDIDO = "Analizá los cobros de Ferretería López"


async def test_un_mensaje_no_confirmado_vuelve_a_la_cola_al_arrancar(
    redis: FakeAsyncRedis, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    job = await JobStore(redis).crear(PEDIDO)
    await redis.lmove(COLA, PROCESANDO, "RIGHT", "LEFT")  # un worker lo tomó y la API se cayó
    async with cliente(redis) as http:  # al cerrar, `cliente` revisa que la cola y "en proceso" quedaron vacías
        final = await esperar(http, job.id, "DONE", "FAILED")
    assert final["estado"] == "DONE"
    assert "[recuperación] 1 mensaje(s) vuelven a la cola" in caplog.text


async def test_un_mensaje_repetido_de_un_trabajo_terminado_se_descarta(redis: FakeAsyncRedis) -> None:
    modelo = guion()
    async with cliente(redis, modelo=modelo) as http:
        r = await http.post("/tasks", json={"pedido": PEDIDO})
        antes = await esperar(http, r.json()["id"], "DONE")
        llamadas = len(modelo.vistos)
        await redis.lpush(COLA, json.dumps({"job_id": antes["id"]}))  # el mismo mensaje, otra vez
        assert await cola_vacia(redis)
        despues = (await http.get(f"/tasks/{antes['id']}")).json()
    assert len(modelo.vistos) == llamadas  # ninguna llamada nueva al modelo
    assert despues == antes  # ni siquiera cambió `actualizado`


async def test_reinicio_con_el_trabajo_en_running_retoma_desde_el_checkpoint(
    redis: FakeAsyncRedis, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    modelo, checkpointer = guion(), InMemorySaver()
    entro, liberar = asyncio.Event(), asyncio.Event()

    async def investigador_lento(estado: dict[str, Any]) -> dict[str, Any]:
        entro.set()
        await liberar.wait()  # en el primer arranque nunca se libera: la API se cae acá
        return await especialista_falso("investigador")(estado)

    def arrancar(**kwargs: Any) -> Any:  # noqa: ANN401
        return cliente(redis, modelo=modelo, checkpointer=checkpointer, investigador=investigador_lento, **kwargs)

    async with arrancar(revisar_cola=False) as http:
        job_id = (await http.post("/tasks", json={"pedido": PEDIDO})).json()["id"]
        await asyncio.wait_for(entro.wait(), 5)
        assert (await http.get(f"/tasks/{job_id}")).json()["estado"] == "RUNNING"
    assert await redis.llen(PROCESANDO) == 1

    liberar.set()
    async with arrancar() as http:
        job = await esperar(http, job_id, "DONE", "FAILED")
    assert job["estado"] == "DONE"
    assert [d["siguiente"] for d in job["resultado"]["decisiones"]] == ["investigador", "sintesis"]
    assert [a["agente"] for a in job["resultado"]["aportes"]] == ["investigador"]
    assert len(modelo.vistos) == 3  # las 2 decisiones del supervisor y la síntesis, ninguna repetida
    assert f"[worker] {job_id} reanuda desde el checkpoint (siguiente: investigador)" in caplog.text


async def test_reinicio_con_el_grafo_terminado_guarda_done_sin_llamar_al_modelo(redis: FakeAsyncRedis) -> None:
    modelo, checkpointer = guion(), InMemorySaver()
    async with cliente(redis, modelo=modelo, checkpointer=checkpointer) as http:
        job_id = (await http.post("/tasks", json={"pedido": PEDIDO})).json()["id"]
        hecho = await esperar(http, job_id, "DONE")
    # La caída justo después del grafo y antes de guardar el estado: RUNNING y el mensaje en proceso.
    await JobStore(redis).actualizar(job_id, estado=EstadoJob.RUNNING, resultado=None)
    await redis.lpush(PROCESANDO, json.dumps({"job_id": job_id}))
    llamadas = len(modelo.vistos)

    async with cliente(redis, modelo=modelo, checkpointer=checkpointer) as http:
        job = await esperar(http, job_id, "DONE", "FAILED")
    assert job["estado"] == "DONE"
    assert job["resultado"] == hecho["resultado"]
    assert len(modelo.vistos) == llamadas


async def test_reinicio_durante_la_firma_no_reembolsa_dos_veces(
    redis: FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    modelo, checkpointer = guion(), InMemorySaver()
    registrar = JobStore.registrar_reembolso
    registrado = asyncio.Event()

    async def registrar_y_caer(self: JobStore, cobro_id: str, datos: dict[str, Any]) -> bool:
        nuevo = await registrar(self, cobro_id, datos)
        registrado.set()
        await asyncio.Event().wait()  # la API se cae después del HSETNX y antes del checkpoint
        return nuevo

    with monkeypatch.context() as parche:  # solo para el primer arranque
        parche.setattr(JobStore, "registrar_reembolso", registrar_y_caer)
        async with cliente(redis, modelo=modelo, checkpointer=checkpointer, revisar_cola=False) as http:
            job_id = (await http.post("/tasks", json={"pedido": f"Reembolsá {APROBADO}"})).json()["id"]
            await esperar(http, job_id, "WAITING_APPROVAL")
            await http.post(f"/tasks/{job_id}/approve", json={"aprobado": True, "revisor": "ana"})
            await asyncio.wait_for(registrado.wait(), 5)
    [crudo] = await redis.lrange(PROCESANDO, 0, -1)
    assert json.loads(crudo)["resume"]["revisor"] == "ana"  # la firma quedó en proceso

    async with cliente(redis, modelo=modelo, checkpointer=checkpointer) as http:
        job = await esperar(http, job_id, "DONE", "FAILED")
    assert job["estado"] == "DONE"
    assert list(await redis.hkeys(REEMBOLSOS)) == [APROBADO]  # una sola vez
    assert f"{APROBADO}: ya tenía un reembolso registrado" in job["resultado"]["resultado_accion"]


class PhoenixFalso:
    """Hace de `AsyncClient` de Phoenix: guarda las anotaciones, o falla si se le pide."""

    def __init__(self, falla: bool = False) -> None:
        self.anotaciones: list[dict[str, Any]] = []
        self.falla = falla
        self.spans = self

    async def add_span_annotation(self, **kwargs: Any) -> None:  # noqa: ANN401
        if self.falla:
            raise httpx.ConnectError("Phoenix no responde")
        self.anotaciones.append(kwargs)


async def test_la_firma_queda_anotada_en_phoenix(redis: FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    phoenix = PhoenixFalso()
    async with cliente(redis) as http:
        monkeypatch.setattr(observability, "_cliente", phoenix)  # después del lifespan, que lo deja en None
        r = await http.post("/tasks", json={"pedido": f"Reembolsá {APROBADO}"})
        job_id = r.json()["id"]
        job = await esperar(http, job_id, "WAITING_APPROVAL")
        span_id = job["aprobacion"]["span_id"]
        revision = {"aprobado": False, "revisor": "ana", "comentario": "no corresponde"}
        await http.post(f"/tasks/{job_id}/approve", json=revision)
        await esperar(http, job_id, "DONE", "FAILED")
    [anotacion] = phoenix.anotaciones
    assert anotacion["span_id"] == span_id
    assert anotacion["annotation_name"] == "aprobacion_humana"
    assert (anotacion["label"], anotacion["score"]) == ("rechazado", 0.0)
    assert anotacion["explanation"] == "no corresponde"


async def test_phoenix_caido_no_frena_la_firma(redis: FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    async with cliente(redis) as http:
        monkeypatch.setattr(observability, "_cliente", PhoenixFalso(falla=True))
        r = await http.post("/tasks", json={"pedido": f"Reembolsá {APROBADO}"})
        job_id = r.json()["id"]
        await esperar(http, job_id, "WAITING_APPROVAL")
        await http.post(f"/tasks/{job_id}/approve", json={"aprobado": True, "revisor": "ana"})
        job = await esperar(http, job_id, "DONE", "FAILED")
    assert job["estado"] == "DONE"
    assert list(await redis.hkeys(REEMBOLSOS)) == [APROBADO]


async def test_sin_redis_responde_503(redis: FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    async def corte(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        raise RedisConnectionError("getaddrinfo failed")  # como el corte de DNS que vimos en vivo

    async with cliente(redis) as http:
        monkeypatch.setattr(redis, "execute_command", corte)
        salud = await http.get("/health")
        consulta = await http.get("/tasks/cualquiera")
        monkeypatch.undo()
    assert salud.status_code == consulta.status_code == 503
    assert "Redis" in salud.json()["detail"]


# --- Contrato de la API (Pydantic) ----------------------------------------------

#: Los esquemas que arma FastAPI solo; no son modelos del proyecto.
_DE_FASTAPI = {"HTTPValidationError", "ValidationError"}


def _referencias(nodo: Any) -> set[str]:  # noqa: ANN401
    """Los modelos que nombra un esquema, entrando por $ref, anyOf, allOf, items y properties."""
    if isinstance(nodo, dict):
        propias = {nodo["$ref"].rsplit("/", 1)[-1]} if "$ref" in nodo else set()
        return propias.union(*(_referencias(v) for v in nodo.values()))
    if isinstance(nodo, list):
        return set().union(*(_referencias(v) for v in nodo))
    return set()


def test_cada_campo_de_la_api_tiene_descripcion() -> None:
    esquema = crear_app(workers=1).openapi()
    modelos = esquema["components"]["schemas"]
    # Lo que expone la API, siguiendo las referencias hasta el fondo (Job -> Resultado -> Aporte...).
    alcanzados: set[str] = set()
    pendientes = _referencias(esquema["paths"])
    while pendientes:
        nombre = pendientes.pop()
        alcanzados.add(nombre)
        pendientes |= _referencias(modelos[nombre]) - alcanzados
    propios = alcanzados - _DE_FASTAPI
    assert {"Pedido", "Job", "Revision", "Resultado", "SolicitudAprobacion", "AccionGuardada",
            "RevisionGuardada", "Aporte", "Decision", "Salud", "ErrorAPI"} <= propios
    sin_descripcion = [
        f"{nombre}.{campo}"
        for nombre in sorted(propios)
        for campo, definicion in modelos[nombre].get("properties", {}).items()
        if not definicion.get("description")
    ]
    assert not sin_descripcion, sin_descripcion
    assert esquema["info"]["version"] == "8.0.0"
    for ruta, metodos in esquema["paths"].items():
        for metodo, operacion in metodos.items():
            assert operacion.get("summary") and operacion.get("tags"), f"{metodo.upper()} {ruta}"
    respuestas = esquema["paths"]["/tasks/{job_id}/approve"]["post"]["responses"]
    assert {"404", "409", "422", "503"} <= set(respuestas)
    assert respuestas["409"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorAPI")


async def test_los_validadores_recortan_y_rechazan_lo_vacio(redis: FakeAsyncRedis) -> None:
    async with cliente(redis) as http:
        assert (await http.post("/tasks", json={"pedido": "     "})).status_code == 422
        assert (await http.post("/tasks", json={"pedido": "   ab   "})).status_code == 422  # 2 caracteres
        r = await http.post("/tasks", json={"pedido": f"  {PEDIDO}\n"})
        assert r.status_code == 202 and r.json()["pedido"] == PEDIDO
        await esperar(http, r.json()["id"], "DONE")
        sin_revisor = await http.post("/tasks/x/approve", json={"aprobado": True, "revisor": "  "})
        assert sin_revisor.status_code == 422
        assert (await http.get("/health")).json() == {"estado": "ok"}
    assert Revision(aprobado=True, revisor=" ana ").revisor == "ana"
    with pytest.raises(ValidationError, match="repetidos"):
        Accion(cobro_ids=[APROBADO, APROBADO])
    with pytest.raises(ValidationError, match="formato"):
        Accion(cobro_ids=["cob_7e8q4"])


def test_un_trabajo_guardado_antes_de_los_modelos_se_sigue_leyendo() -> None:
    """Lo que hoy está en Redis: aprobación sin span_id y una firma que los validadores nuevos rechazarían."""
    viejo = {
        "id": "abc", "pedido": f"Reembolsá {APROBADO}", "estado": "DONE",
        "creado": "2026-10-20T12:00:00Z", "actualizado": "2026-10-20T12:01:00Z",
        "aprobacion": {"accion": {"tipo": "reembolso", "cobro_ids": [APROBADO]}, "analisis": "x", "pregunta": "?"},
        "resultado": {
            "respuesta_final": "ok", "resultado_accion": None,
            "revision": {"aprobado": True, "revisor": "  ", "comentario": ""},
            "decisiones": [decision("sintesis").model_dump()],
            "aportes": [{"agente": "investigador", "instruccion": "x", "contenido": "y", "herramientas": [],
                         "error": False, "ms": 1.0}],
        },
        "error": None,
    }
    job = Job.model_validate_json(json.dumps(viejo))
    assert job.aprobacion is not None and job.aprobacion.span_id is None
    assert json.loads(job.model_dump_json())["resultado"] == viejo["resultado"]  # mismas claves de ida y vuelta


async def test_los_logs_de_validacion_y_tiempos(redis: FakeAsyncRedis, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    pedido = f"Reembolsá {APROBADO}"
    async with cliente(redis) as http:
        job_id = (await http.post("/tasks", json={"pedido": pedido})).json()["id"]
        await esperar(http, job_id, "WAITING_APPROVAL")
        await http.post(f"/tasks/{job_id}/approve", json={"aprobado": True, "revisor": "ana"})
        await esperar(http, job_id, "DONE")
    assert f"validación OK: pedido de {len(pedido)} caracteres -> job {job_id} encolado en " in caplog.text
    assert "validación OK: Decision | [supervisor] evalúa:" in caplog.text
    assert f"[worker] {job_id} -> WAITING_APPROVAL en " in caplog.text
    assert "validación OK: revisión de ana (aprobado=True)" in caplog.text
    assert re.search(rf"\[worker\] {job_id} -> DONE en \d+,\d s", caplog.text)


# --- Concurrencia --------------------------------------------------------------
# Dos firmas simultáneas sobre el mismo trabajo (un 202, un 409, un solo reembolso) ya las cubre
# test_la_firma_es_atomica_si_falla_el_encolado_se_puede_volver_a_firmar.


class SupervisorPorPedido(SupervisorGuionado):
    """Sin estado compartido entre pedidos: delega una vez al investigador con el pedido y después sintetiza."""

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        def decidir(mensajes: list[BaseMessage]) -> Any:  # noqa: ANN401
            texto = str(mensajes[-1].content)
            if "(todavía no hay aportes)" not in texto:
                return decision("sintesis")
            return decision("investigador", texto.split("PEDIDO DEL USUARIO:\n", 1)[1].split("\n\nAPORTES", 1)[0])

        return RunnableLambda(decidir)


async def test_veinte_pedidos_simultaneos_con_cinco_workers(redis: FakeAsyncRedis) -> None:
    pedidos = [f"Analizá los cobros del comercio número {i}" for i in range(20)]
    async with cliente(redis, modelo=SupervisorPorPedido(), workers=5) as http:  # al cerrar revisa cola y procesando
        respuestas = await asyncio.gather(*(http.post("/tasks", json={"pedido": p}) for p in pedidos))
        assert {r.status_code for r in respuestas} == {202}
        ids = [r.json()["id"] for r in respuestas]
        assert len(set(ids)) == 20
        jobs = await asyncio.gather(*(esperar(http, i, "DONE", "FAILED", intentos=500) for i in ids))
    for pedido, job in zip(pedidos, jobs, strict=True):
        assert job["estado"] == "DONE"
        # Cada trabajo tiene su propio aporte: los thread_id no se mezclan.
        assert [(a["agente"], a["instruccion"]) for a in job["resultado"]["aportes"]] == [("investigador", pedido)]
        assert [d["siguiente"] for d in job["resultado"]["decisiones"]] == ["investigador", "sintesis"]
