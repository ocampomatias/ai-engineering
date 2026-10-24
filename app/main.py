"""API REST del orquestador multi-agente.

    POST /tasks                 encola un pedido y devuelve el job_id (202, no espera al grafo)
    GET  /tasks/{id}            estado del trabajo: PENDING, RUNNING, WAITING_APPROVAL, DONE o FAILED
    POST /tasks/{id}/approve    aprueba o rechaza el reembolso que espera revisión (app/hitl.py)
    GET  /health                ping a Redis
    GET  /                      la consola web (app/static/)

Levantar:

    uvicorn app.main:app --port 8000

Los endpoints solo hablan con Redis, siempre con `await`; el grafo corre en los
workers. Por eso un pedido de 30 segundos no frena a los demás requests.
"""

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field, field_validator
from redis.exceptions import ConnectionError as RedisCaido
from redis.exceptions import TimeoutError as RedisLento

from .hitl import router as router_hitl
from .jobs import Job, JobStore, respuestas_de_error
from .observability import cerrar_trazas, iniciar_trazas
from .worker import correr_worker

logger = logging.getLogger(__name__)

ESTATICOS = Path(__file__).parent / "static"


class Pedido(BaseModel):
    """Lo que recibe `POST /tasks`."""

    pedido: str = Field(
        min_length=5,
        max_length=2000,
        description="El pedido en lenguaje natural. Se recorta; solo espacios no es un pedido.",
        examples=["Analizá los últimos 5 cobros de Ferretería López: qué porcentaje se rechazó y por qué."],
    )

    @field_validator("pedido", mode="before")
    @classmethod
    def _recortar(cls, valor: object) -> object:
        # Antes de min_length: "     " queda vacío y se rechaza, y "  ok  " no cuenta los espacios.
        return valor.strip() if isinstance(valor, str) else valor


class Salud(BaseModel):
    """Lo que devuelve `GET /health` cuando Redis responde."""

    estado: Literal["ok"] = Field(description="Siempre \"ok\": si Redis no responde, la API da 503.")


@dataclass
class Recursos:
    store: JobStore
    grafo: CompiledStateGraph


@asynccontextmanager
async def recursos_redis() -> AsyncIterator[Recursos]:
    """Redis real: estado de trabajos y cola con redis.asyncio, checkpoints con AsyncRedisSaver."""
    from langgraph.checkpoint.redis.aio import AsyncRedisSaver
    from redis.asyncio import Redis

    from .graph import construir_grafo, serializador_redis

    url = os.getenv("REDIS_URL")
    if not url:
        raise RuntimeError("Falta REDIS_URL en el .env (ver .env.example).")
    redis = Redis.from_url(url, decode_responses=True, health_check_interval=30)
    async with AsyncExitStack() as pila:
        pila.push_async_callback(redis.aclose)
        await redis.ping()
        # Cada trabajo deja ~0,7 MB de checkpoints. Sin TTL, el plan gratis (30 MB) se llena
        # en unas decenas de pedidos; un trabajo que espera aprobación más que esto ya no se reanuda.
        ttl = {"default_ttl": float(os.getenv("CHECKPOINT_TTL_MIN", "1440"))}
        checkpointer = await pila.enter_async_context(AsyncRedisSaver.from_conn_string(url, ttl=ttl))
        checkpointer.serde = serializador_redis()
        await checkpointer.asetup()  # crea los índices de RediSearch (idempotente)
        store = JobStore(redis)
        grafo = await asyncio.to_thread(construir_grafo, checkpointer, store.registrar_reembolso)
        yield Recursos(store, grafo)


def crear_app(
    fabrica: Callable[[], AbstractAsyncContextManager[Recursos]] = recursos_redis,
    workers: int | None = None,
) -> FastAPI:
    workers = workers or int(os.getenv("WORKERS", "5"))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # En un thread: register() y la instrumentación son síncronos. El AsyncClient de Phoenix
        # que crea no necesita el loop para construirse (httpx abre las conexiones al usarlo).
        proveedor = await asyncio.to_thread(iniciar_trazas)
        try:
            async with fabrica() as recursos:
                app.state.store = recursos.store
                # Antes de los workers: lo que quedó a medias por una caída vuelve a la cola.
                recuperados = await recursos.store.recuperar()
                logger.info("[recuperación] %d mensaje(s) vuelven a la cola", recuperados)
                tareas = [
                    asyncio.create_task(correr_worker(recursos.store, recursos.grafo, f"worker-{i}"))
                    for i in range(1, workers + 1)
                ]
                try:
                    yield
                finally:
                    for tarea in tareas:
                        tarea.cancel()
                    await asyncio.gather(*tareas, return_exceptions=True)
        finally:
            await asyncio.to_thread(cerrar_trazas, proveedor)  # shutdown() vacía el lote: puede tardar

    app = FastAPI(
        title="Tambor: orquestador multi-agente",
        version="8.0.0",
        lifespan=lifespan,
        # FastAPI traza cada request en Phoenix. Los GET de polling (y los HEAD de los chequeos de
        # vida) serían cientos de trazas de ruido: quedan POST /tasks y POST /approve.
        telemetry={"exclude": lambda scope: scope.get("method") in {"GET", "HEAD", "OPTIONS"}},
    )
    app.include_router(router_hitl)

    # Si se corta la conexión con Redis (red, DNS, Redis Cloud reiniciando), 503 con un mensaje
    # claro en vez de un 500 opaco. El cliente puede reintentar; nada se perdió.
    @app.exception_handler(RedisCaido)
    @app.exception_handler(RedisLento)
    async def sin_redis(request: Request, exc: Exception) -> JSONResponse:
        logger.warning("Sin conexión con Redis en %s %s: %s", request.method, request.url.path, exc)
        return JSONResponse(
            {"detail": "No hay conexión con Redis. Reintentá en unos segundos."},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")

    @app.get("/", include_in_schema=False)
    async def consola() -> FileResponse:
        """La consola web: encolar pedidos, seguirlos en vivo y firmar reembolsos."""
        return FileResponse(ESTATICOS / "index.html")

    @app.post(
        "/tasks",
        status_code=status.HTTP_202_ACCEPTED,
        tags=["trabajos"],
        summary="Encolar un pedido",
        responses=respuestas_de_error(422, 503),
    )
    async def crear_tarea(pedido: Pedido, request: Request) -> Job:
        """Lo registra como PENDING y lo encola; no espera al grafo. Seguilo con `GET /tasks/{id}`."""
        inicio = time.perf_counter()
        job = await request.app.state.store.crear(pedido.pedido)
        logger.info(
            "validación OK: pedido de %d caracteres -> job %s encolado en %.1f ms",
            len(pedido.pedido), job.id, (time.perf_counter() - inicio) * 1000,
        )
        return job

    @app.get(
        "/tasks/{job_id}",
        tags=["trabajos"],
        summary="Consultar el estado de un trabajo",
        responses=respuestas_de_error(404, 503),
    )
    async def consultar_tarea(job_id: str, request: Request) -> Job:
        """PENDING, RUNNING, WAITING_APPROVAL (con `aprobacion`), DONE (con `resultado`) o FAILED (con `error`)."""
        job = await request.app.state.store.obtener(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No existe ese trabajo.")
        return job

    @app.get("/health", tags=["sistema"], summary="Chequeo de vida (ping a Redis)", responses=respuestas_de_error(503))
    async def health(request: Request) -> Salud:
        await request.app.state.store.redis.ping()
        return Salud(estado="ok")

    return app


def _configurar_logs() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    for ruidoso in ("httpx", "httpcore", "anthropic", "openai", "urllib3", "pinecone",
                    "rag_pinecone", "rag.embeddings", "redisvl", "huggingface_hub"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)


load_dotenv()
_configurar_logs()
app = crear_app()
