"""Estado de los trabajos y cola de tareas, los dos en Redis.

- Cada trabajo es un JSON en `tambor:job:{id}` (con TTL): su estado, el pedido,
  lo que espera aprobación, el resultado o el error.
- La cola es una lista de Redis (`tambor:cola`): la API hace LPUSH y los
  workers BLMOVE hacia `tambor:procesando`. El mensaje sale de "en proceso"
  (`confirmar`) recién cuando el estado final del trabajo quedó guardado, y al
  arrancar, `recuperar` devuelve a la cola lo que quedó a medias. Así ni un
  reinicio ni una caída a mitad de un trabajo pierden el mensaje.

Todo es `redis.asyncio`: ninguna operación bloquea el event loop de FastAPI.
"""

import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from redis.exceptions import WatchError

from orquestador.state import Aporte, Decision

PREFIJO = "tambor"
COLA = f"{PREFIJO}:cola"
PROCESANDO = f"{PREFIJO}:procesando"
REEMBOLSOS = f"{PREFIJO}:reembolsos"
TTL_JOB_S = 7 * 24 * 3600

#: Caídas momentáneas de Redis. El nodo `reembolso` se reintenta ante ellas, y el worker
#: devuelve el mensaje a la cola en vez de marcar FAILED.
REDIS_TRANSITORIO = (RedisConnectionError, RedisTimeoutError)


class EstadoJob(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    DONE = "DONE"
    FAILED = "FAILED"


def _ahora() -> datetime:
    return datetime.now(UTC)


class AccionGuardada(BaseModel):
    """La acción crítica tal como queda guardada en el trabajo.

    Sin los validadores de entrada de `app.hitl.Accion` (que la extiende): así un trabajo
    guardado antes de que existieran se sigue leyendo.
    """

    tipo: Literal["reembolso"] = Field(
        default="reembolso", description="Tipo de acción. La única con efectos secundarios es el reembolso."
    )
    cobro_ids: list[str] = Field(
        min_length=1, description="IDs de los cobros a reembolsar (formato cob_XXXXX).", examples=[["cob_7E8Q4"]]
    )


class RevisionGuardada(BaseModel):
    """La firma humana tal como queda guardada. `app.hitl.Revision` la extiende con los validadores de entrada."""

    aprobado: bool = Field(description="True aprueba el reembolso; False lo rechaza y no se ejecuta.")
    revisor: str = Field(
        min_length=1, max_length=80, description="Quién firma. Queda en el registro del reembolso y en Phoenix."
    )
    comentario: str = Field(default="", max_length=500, description="Por qué se aprobó o rechazó (opcional).")


class SolicitudAprobacion(BaseModel):
    """Lo que el grafo dejó esperando la firma: la acción, el análisis y la pregunta para quien revisa."""

    accion: AccionGuardada = Field(description="La acción que se ejecuta si se aprueba.")
    analisis: str = Field(default="", description="La respuesta de la síntesis, para decidir con los datos a la vista.")
    pregunta: str = Field(description="La pregunta para quien revisa.")
    span_id: str | None = Field(
        default=None, description="Span de Phoenix que pidió la firma; ahí se anota la decisión."
    )


class Resultado(BaseModel):
    """Lo que dejó el grafo al terminar el trabajo."""

    respuesta_final: str = Field(default="", description="La respuesta de la síntesis para el usuario.")
    resultado_accion: str | None = Field(
        default=None, description="Qué pasó con cada cobro del reembolso; null si no hubo reembolso."
    )
    revision: RevisionGuardada | None = Field(default=None, description="La firma humana, si el pedido la necesitó.")
    decisiones: list[Decision] = Field(default_factory=list, description="Cada decisión del supervisor, en orden.")
    aportes: list[Aporte] = Field(default_factory=list, description="Lo que aportó cada especialista, en orden.")


class Job(BaseModel):
    """Un trabajo: el pedido, su estado y, al final, el resultado o el error."""

    id: str = Field(description="ID del trabajo. Es también el thread_id del checkpoint y la sesión en Phoenix.")
    pedido: str = Field(description="El pedido tal como lo recibió la API.")
    estado: EstadoJob = Field(default=EstadoJob.PENDING, description="En qué punto del flujo está el trabajo.")
    creado: datetime = Field(default_factory=_ahora, description="Cuándo se creó (UTC).")
    actualizado: datetime = Field(default_factory=_ahora, description="Último cambio de estado (UTC).")
    aprobacion: SolicitudAprobacion | None = Field(
        default=None, description="Lo que el grafo dejó esperando revisión humana (solo en WAITING_APPROVAL)."
    )
    resultado: Resultado | None = Field(default=None, description="El resultado del grafo (solo en DONE).")
    error: str | None = Field(default=None, description="Tipo y mensaje del error (solo en FAILED).")


class ErrorAPI(BaseModel):
    """El cuerpo de un error de la API, como lo arma FastAPI."""

    detail: str | list[dict[str, Any]] = Field(
        description="Qué salió mal. En un 422, la lista de campos inválidos con su mensaje (`msg`)."
    )


_ERRORES = {
    404: "No existe ese trabajo.",
    409: "El trabajo no espera aprobación.",
    422: "El cuerpo no pasó la validación.",
    503: "No hay conexión con Redis; se puede reintentar.",
}


def respuestas_de_error(*codigos: int) -> dict[int | str, dict[str, Any]]:
    """Las respuestas de error de un endpoint, para el `responses=` de FastAPI."""
    return {codigo: {"model": ErrorAPI, "description": _ERRORES[codigo]} for codigo in codigos}


class JobStore:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis  # con decode_responses=True

    @staticmethod
    def _clave(job_id: str) -> str:
        return f"{PREFIJO}:job:{job_id}"

    async def _guardar(self, job: Job) -> None:
        await self.redis.set(self._clave(job.id), job.model_dump_json(), ex=TTL_JOB_S)

    async def crear(self, pedido: str) -> Job:
        """Registra el trabajo como PENDING y lo encola, en una sola transacción (MULTI/EXEC).

        Es atómico: un worker nunca toma un trabajo cuyo registro no existe (el
        cliente vería un 404), y no queda un registro sin encolar. Además es un
        solo viaje de red a Redis en vez de dos.
        """
        job = Job(id=uuid4().hex, pedido=pedido)
        async with self.redis.pipeline(transaction=True) as tx:
            tx.set(self._clave(job.id), job.model_dump_json(), ex=TTL_JOB_S)
            tx.lpush(COLA, json.dumps({"job_id": job.id}))
            await tx.execute()
        return job

    async def obtener(self, job_id: str) -> Job | None:
        crudo = await self.redis.get(self._clave(job_id))
        return Job.model_validate_json(crudo) if crudo else None

    async def actualizar(self, job_id: str, **campos: Any) -> Job:  # noqa: ANN401
        job = await self.obtener(job_id)
        if job is None:
            raise KeyError(job_id)
        # model_validate y no model_copy: lo que manda el worker (dicts) se valida contra el modelo.
        job = Job.model_validate({**job.model_dump(), **campos, "actualizado": _ahora()})
        await self._guardar(job)
        return job

    async def reanudar(self, job_id: str, resume: dict[str, Any]) -> Job | None:
        """Pasa el trabajo de WAITING_APPROVAL a PENDING y encola la revisión, en una sola transacción.

        WATCH + MULTI/EXEC: si la API se cae antes del EXEC no se escribió nada y la revisión se
        puede volver a mandar (nunca queda un PENDING sin mensaje), y si llegan dos revisiones a la
        vez solo una pasa. None si el trabajo no existe, no espera aprobación o ya se revisó.
        """
        clave = self._clave(job_id)
        async with self.redis.pipeline(transaction=True) as tx:
            await tx.watch(clave)
            crudo = await tx.get(clave)
            job = Job.model_validate_json(crudo) if crudo else None
            if job is None or job.estado != EstadoJob.WAITING_APPROVAL:
                return None
            job = job.model_copy(update={"estado": EstadoJob.PENDING, "actualizado": _ahora()})
            tx.multi()
            tx.set(clave, job.model_dump_json(), ex=TTL_JOB_S)
            tx.lpush(COLA, json.dumps({"job_id": job_id, "resume": resume}))
            try:
                await tx.execute()
            except WatchError:  # otra revisión cambió el trabajo entre el GET y el EXEC
                return None
        return job

    async def desencolar(self, timeout_s: float = 2) -> tuple[str, dict[str, Any]] | None:
        """El próximo mensaje `(crudo, mensaje)`, o None si no llegó nada en `timeout_s`.

        BLMOVE lo pasa de la cola a "en proceso" en un solo paso atómico: si el proceso muere
        antes de `confirmar(crudo)`, el mensaje sigue en Redis y `recuperar` lo devuelve.
        `timeout_s` tiene que quedar bien por debajo del socket_timeout del cliente (5 s por
        defecto en redis-py); si el cliente corta justo cuando Redis movió el mensaje, ya no
        se pierde: queda en "en proceso".
        """
        crudo = await self.redis.blmove(COLA, PROCESANDO, timeout_s, "RIGHT", "LEFT")
        return (crudo, json.loads(crudo)) if crudo else None

    async def confirmar(self, crudo: str) -> None:
        """El trabajo terminó y su estado quedó guardado: el mensaje sale de "en proceso"."""
        await self.redis.lrem(PROCESANDO, 1, crudo)

    async def devolver(self, crudo: str) -> bool:
        """Redis se cortó a mitad del trabajo: el mensaje vuelve de "en proceso" a la cola con
        `intentos` + 1 (el nuevo reemplaza al viejo en la misma transacción).

        Idempotente: solo encola si `crudo` sigue en "en proceso" (WATCH + LPOS + MULTI). Si un
        EXEC se aplicó y se perdió la respuesta, el reintento no duplica el mensaje. False si ya
        no estaba. RPUSH lo deja en la punta de la que saca BLMOVE: se retoma antes que lo nuevo.
        shortcut: WATCH y no un script Lua porque fakeredis no trae Lua sin `lupa`; con muchos
        workers el WATCH choca más seguido (se reintenta en el acto).
        """
        mensaje = json.loads(crudo)
        nuevo = json.dumps({**mensaje, "intentos": mensaje.get("intentos", 0) + 1})
        async with self.redis.pipeline(transaction=True) as tx:
            while True:
                await tx.watch(PROCESANDO)
                if await tx.lpos(PROCESANDO, crudo) is None:
                    return False
                tx.multi()
                tx.lrem(PROCESANDO, 1, crudo)
                tx.rpush(COLA, nuevo)
                try:
                    await tx.execute()
                    return True
                except WatchError:  # otro worker tocó "en proceso" (BLMOVE, LREM): de nuevo
                    continue

    async def recuperar(self) -> int:
        """Devuelve a la cola lo que quedó en "en proceso" (caída o reinicio). Llamar antes de los workers.

        LMOVE LEFT RIGHT los deja en la punta de salida de la cola, en el orden en que habían
        entrado: se retoman antes que los pedidos nuevos.
        shortcut: una sola instancia de la API; con dos, esta devolvería a la cola lo que la otra
        está procesando. Para escalar: una lista "en proceso" por instancia con latido y TTL.
        """
        movidos = 0
        while await self.redis.lmove(PROCESANDO, COLA, "LEFT", "RIGHT"):
            movidos += 1
        return movidos

    async def registrar_reembolso(self, cobro_id: str, datos: dict[str, Any]) -> bool:
        """El efecto secundario del flujo. HSETNX: un cobro no se reembolsa dos veces.

        True si el reembolso es de este trabajo (`datos["job_id"]`): recién registrado, o
        registrado por el mismo trabajo antes de una caída. False si lo registró otro.
        """
        if await self.redis.hsetnx(REEMBOLSOS, cobro_id, json.dumps(datos, ensure_ascii=False)):
            return True
        previo = await self.redis.hget(REEMBOLSOS, cobro_id)
        return bool(previo) and json.loads(previo).get("job_id") == datos["job_id"]
