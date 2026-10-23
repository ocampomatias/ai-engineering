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
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field
from redis.asyncio import Redis
from redis.exceptions import WatchError

PREFIJO = "tambor"
COLA = f"{PREFIJO}:cola"
PROCESANDO = f"{PREFIJO}:procesando"
REEMBOLSOS = f"{PREFIJO}:reembolsos"
TTL_JOB_S = 7 * 24 * 3600


class EstadoJob(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    DONE = "DONE"
    FAILED = "FAILED"


def _ahora() -> datetime:
    return datetime.now(UTC)


class Job(BaseModel):
    id: str
    pedido: str
    estado: EstadoJob = EstadoJob.PENDING
    creado: datetime = Field(default_factory=_ahora)
    actualizado: datetime = Field(default_factory=_ahora)
    aprobacion: dict[str, Any] | None = Field(
        default=None, description="Lo que el grafo dejó esperando revisión humana."
    )
    resultado: dict[str, Any] | None = None
    error: str | None = None


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
        job = job.model_copy(update={**campos, "actualizado": _ahora()})
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
        """El efecto secundario del flujo. HSETNX: un cobro no se reembolsa dos veces."""
        return bool(await self.redis.hsetnx(REEMBOLSOS, cobro_id, json.dumps(datos, ensure_ascii=False)))
