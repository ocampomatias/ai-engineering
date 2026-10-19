"""Tests de la API de producción (pre-entrega 7). Sin API keys, sin red y sin Redis real.

- Redis es `fakeredis` (estado de trabajos, cola, reembolsos) y el checkpointer
  es `InMemorySaver`: el grafo, la interrupción y la reanudación son los reales.
- El supervisor y la síntesis son el modelo guionado de test_orquestador.py; los
  especialistas son nodos falsos que devuelven un aporte fijo.
- La API se prueba por HTTP con httpx + ASGITransport, con los workers corriendo.

    python -m pytest -q tests/test_api.py
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from fakeredis import FakeAsyncRedis
from redis.exceptions import ConnectionError as RedisConnectionError
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from test_orquestador import SupervisorGuionado, decision

from app import observability
from app.graph import construir_grafo, serializador_redis
from app.hitl import Accion, Revision, detectar_accion
from app.jobs import COLA, REEMBOLSOS, JobStore
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
    return FakeAsyncRedis(decode_responses=True)


@asynccontextmanager
async def cliente(redis: FakeAsyncRedis, *, falla: bool = False) -> AsyncIterator[httpx.AsyncClient]:
    modelo = SupervisorGuionado(
        decisiones=[decision("investigador", "traé los cobros"), decision("sintesis")],
        respuesta_sintesis="Análisis listo.",
    )

    @asynccontextmanager
    async def fabrica() -> AsyncIterator[Recursos]:
        store = JobStore(redis)
        grafo = construir_grafo(
            InMemorySaver(),
            store.registrar_reembolso,
            modelo,
            investigador=especialista_falso("investigador", falla),
            analista=especialista_falso("analista"),
        )
        yield Recursos(store, grafo)

    app = crear_app(fabrica, workers=2)
    async with app.router.lifespan_context(app):
        transporte = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transporte, base_url="http://test") as http:
            yield http


async def esperar(http: httpx.AsyncClient, job_id: str, *estados: str) -> dict[str, Any]:
    for _ in range(200):
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
