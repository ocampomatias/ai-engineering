"""Herramientas del agente: el contrato que lee el LLM para decidir qué hacer.

Cada herramienta tiene un nombre, un docstring que el modelo usa para elegirla,
un esquema de entrada en Pydantic (valida los argumentos antes de tocar la
base) y un modelo de salida, que se serializa a JSON con las claves que lee el
modelo.

Los errores esperables (un comercio que no existe, un nombre ambiguo) se
lanzan como `ToolException` con un mensaje pensado para el modelo: qué salió
mal y qué puede hacer. El nodo de herramientas los devuelve como resultado, y
el agente decide si reintenta con otros argumentos o le pregunta al usuario.

Las dependencias (la base y el buscador) entran por la fábrica
`crear_herramientas`, no por el estado del grafo: el estado se guarda en el
checkpointer y no tiene que llevar conexiones adentro.
"""

import asyncio
import difflib
import logging
import time

from langchain_core.tools import BaseTool, ToolException, tool
from pydantic import BaseModel, Field, field_validator

from .datos import BaseCobros, Cobro, Comercio, EstadoCobro, normalizar
from .documentacion import Buscador

logger = logging.getLogger(__name__)

MAX_CARACTERES_FRAGMENTO = 900


def responder(herramienta: str, salida: BaseModel, inicio: float) -> str:
    """Serializa la salida ya validada y loguea cuánto tardó la herramienta.

    `exclude_unset` deja afuera los opcionales que no se llenaron (como `aviso`): el
    JSON tiene las mismas claves que antes de usar modelos de salida.
    """
    logger.info(
        "validación OK: %s -> %s en %.1f ms",
        herramienta,
        type(salida).__name__,
        (time.perf_counter() - inicio) * 1000,
    )
    return salida.model_dump_json(exclude_unset=True)


# --- Esquemas de entrada ---------------------------------------------------


def _recortar(valor: object) -> object:
    return valor.strip() if isinstance(valor, str) else valor


class EntradaBuscarComercio(BaseModel):
    nombre: str = Field(
        min_length=2,
        description="Nombre del comercio o parte de él, tal como lo dijo el usuario. Ej: 'López'.",
    )

    _recortar_nombre = field_validator("nombre", mode="before")(_recortar)


class EntradaListarCobros(BaseModel):
    comercio_id: int = Field(ge=1, description="ID numérico del comercio (lo da buscar_comercio).")
    estado: EstadoCobro | None = Field(
        default=None,
        description="Filtra por estado: aprobado, rechazado, en_revision o reembolsado. Sin filtro, todos.",
    )
    limite: int = Field(default=5, ge=1, le=20, description="Cuántos cobros traer, de 1 a 20.")


class EntradaConsultarCobro(BaseModel):
    cobro_id: str = Field(
        pattern=r"^cob_[A-Z0-9]{5}$",
        description="ID del cobro, con el formato cob_XXXXX (ej. cob_7F3K2).",
    )

    @field_validator("cobro_id", mode="before")
    @classmethod
    def _normalizar_id(cls, valor: object) -> object:
        """'cob_7f3k2' -> 'cob_7F3K2': en la base el sufijo va en mayúsculas."""
        if isinstance(valor, str) and valor.strip()[:4].lower() == "cob_":
            return "cob_" + valor.strip()[4:].upper()
        return valor


class EntradaBuscarDocumentacion(BaseModel):
    consulta: str = Field(
        min_length=3,
        description=(
            "Qué buscar. Para un error, el código exacto (ej. 'TMB-2031'); para un procedimiento, "
            "una descripción breve (ej. 'cómo pedir un crédito por caída del servicio')."
        ),
    )
    k: int = Field(default=3, ge=1, le=5, description="Cuántos fragmentos traer, de 1 a 5.")

    _recortar_consulta = field_validator("consulta", mode="before")(_recortar)


# --- Esquemas de salida ----------------------------------------------------


class SalidaBuscarComercio(BaseModel):
    comercios: list[Comercio] = Field(description="Comercios cuyo nombre coincide con la búsqueda.")
    aviso: str | None = Field(
        default=None, description="Solo si coincide más de uno: preguntarle al usuario cuál."
    )


class CobroResumido(BaseModel):
    id: str = Field(description="ID del cobro, formato cob_XXXXX.")
    fecha: str = Field(description="Fecha y hora del cobro, ISO 8601.")
    monto: str = Field(description="Monto formateado, ej. '45.990,00 ARS'.")
    estado: EstadoCobro = Field(description="Estado del cobro.")
    codigo_error: str | None = Field(description="Código TMB-XXXX si el cobro falló; si no, null.")


class SalidaListarCobros(BaseModel):
    comercio: str = Field(description="Nombre del comercio consultado.")
    filtro_estado: EstadoCobro | None = Field(description="Estado por el que se filtró, o null.")
    cobros: list[CobroResumido] = Field(description="Cobros, del más reciente al más viejo.")
    aviso: str | None = Field(default=None, description="Solo si no hay cobros con ese filtro.")


class SalidaConsultarCobro(Cobro):
    comercio: str | None = Field(description="Nombre del comercio dueño del cobro.")


class Fragmento(BaseModel):
    fuente: str | None = Field(description="Archivo de la documentación de donde sale el fragmento.")
    seccion: str | None = Field(description="Título de la sección, o el código de error.")
    pagina: int | None = Field(description="Página del PDF; null en los demás formatos.")
    texto: str = Field(
        max_length=MAX_CARACTERES_FRAGMENTO, description="Texto del fragmento, recortado."
    )


class SalidaBuscarDocumentacion(BaseModel):
    fragmentos: list[Fragmento] = Field(description="Fragmentos más relevantes, del mejor al peor.")
    aviso: str | None = Field(default=None, description="Solo si la búsqueda no encontró nada.")
    buscador: str = Field(
        description="Qué buscador respondió: 'pinecone-hibrido' (Pinecone + BM25) o 'bm25-local'."
    )


# --- Fábrica ---------------------------------------------------------------


def crear_herramientas(base: BaseCobros, buscador: Buscador) -> list[BaseTool]:
    """Las cuatro herramientas, con la base y el buscador ya inyectados."""

    @tool(args_schema=EntradaBuscarComercio)
    async def buscar_comercio(nombre: str) -> str:
        """Busca comercios de Tambor por nombre y devuelve su ID, plan y estado.

        Usala cuando el usuario nombra un comercio y todavía no tenés su ID numérico: las demás
        herramientas de cobros necesitan el ID. Ignora mayúsculas y tildes. Si devuelve más de un
        comercio y no está claro cuál quiere el usuario, preguntale antes de seguir.
        """
        inicio = time.perf_counter()
        encontrados = await asyncio.to_thread(base.buscar_comercios, nombre)
        if not encontrados:
            nombres = await asyncio.to_thread(base.nombres_comercios)
            parecidos = difflib.get_close_matches(
                normalizar(nombre), [normalizar(n) for n in nombres], n=3, cutoff=0.4
            )
            sugerencia = [n for n in nombres if normalizar(n) in parecidos]
            raise ToolException(
                f"No hay ningún comercio cuyo nombre contenga '{nombre}'. "
                + (f"Nombres parecidos: {sugerencia}. " if sugerencia else "")
                + "Probá con otra parte del nombre o pedile al usuario que lo confirme."
            )
        salida = SalidaBuscarComercio(comercios=encontrados)
        if len(encontrados) > 1:
            salida.aviso = (
                f"Hay {len(encontrados)} comercios que coinciden. Si el usuario no dijo cuál, "
                "preguntale antes de consultar sus cobros."
            )
        return responder("buscar_comercio", salida, inicio)

    @tool(args_schema=EntradaListarCobros)
    async def listar_cobros(
        comercio_id: int, estado: EstadoCobro | None = None, limite: int = 5
    ) -> str:
        """Lista los cobros de un comercio, del más reciente al más viejo.

        Devuelve, para cada cobro, su ID, fecha, monto, estado y código de error (si falló). Usala
        para preguntas como "el último cobro", "los cobros rechazados" o "cuánto cobró". Para ver
        el detalle de un cobro puntual (medio de pago, intentos), usá consultar_cobro.
        """
        inicio = time.perf_counter()
        comercio = await asyncio.to_thread(base.comercio, comercio_id)
        if comercio is None:
            raise ToolException(
                f"No existe un comercio con ID {comercio_id}. Si tenés el nombre, usá "
                "buscar_comercio para obtener el ID correcto."
            )
        cobros = await asyncio.to_thread(base.listar_cobros, comercio_id, estado, limite)
        campos = set(CobroResumido.model_fields)
        salida = SalidaListarCobros(
            comercio=comercio.nombre,
            filtro_estado=estado,
            cobros=[CobroResumido(**c.model_dump(include=campos)) for c in cobros],
        )
        if not cobros:
            salida.aviso = "El comercio no tiene cobros con ese filtro."
        return responder("listar_cobros", salida, inicio)

    @tool(args_schema=EntradaConsultarCobro)
    async def consultar_cobro(cobro_id: str) -> str:
        """Devuelve el detalle de un cobro: comercio, fecha, monto, estado, código de error, medio
        de pago y cantidad de intentos.

        Usala cuando ya tenés el ID de un cobro (formato cob_XXXXX) y necesitás saber qué pasó con
        él. Si tiene código de error (TMB-XXXX), buscá ese código con buscar_documentacion para
        explicar qué significa y qué tiene que hacer el comercio.
        """
        inicio = time.perf_counter()
        cobro = await asyncio.to_thread(base.cobro, cobro_id)
        if cobro is None:
            raise ToolException(
                f"No existe el cobro {cobro_id}. Revisá el ID con listar_cobros."
            )
        comercio = await asyncio.to_thread(base.comercio, cobro.comercio_id)
        salida = SalidaConsultarCobro(
            **cobro.model_dump(), comercio=comercio.nombre if comercio else None
        )
        return responder("consultar_cobro", salida, inicio)

    @tool(args_schema=EntradaBuscarDocumentacion)
    async def buscar_documentacion(consulta: str, k: int = 3) -> str:
        """Busca en la documentación interna de Tambor: catálogo de códigos de error de la API
        (TMB-XXXX), referencia de la API de cobros, SLA y soporte, política de despliegues, runbook
        de incidentes, seguridad y observabilidad.

        Usala para explicar qué significa un código de error y qué tiene que hacer el comercio, o
        para responder preguntas sobre reglas y procedimientos. Respondé solo con lo que devuelve:
        si no encontrás la respuesta, decilo.
        """
        inicio = time.perf_counter()
        try:
            documentos = await asyncio.to_thread(buscador.buscar, consulta, k)
        except Exception as exc:  # Pinecone caído, sin red, índice vacío
            raise ToolException(
                f"La búsqueda en la documentación no está disponible ({type(exc).__name__}: "
                f"{exc}). Avisale al usuario que no pudiste consultar la documentación."
            ) from exc
        salida = SalidaBuscarDocumentacion(
            buscador=buscador.nombre,
            fragmentos=[
                Fragmento(
                    fuente=d.metadata.get("fuente"),
                    seccion=d.metadata.get("seccion"),
                    pagina=d.metadata.get("pagina"),
                    texto=" ".join(d.page_content.split())[:MAX_CARACTERES_FRAGMENTO],
                )
                for d in documentos
            ],
        )
        if not documentos:
            salida.aviso = "No se encontró nada para esa consulta."
        return responder("buscar_documentacion", salida, inicio)

    herramientas: list[BaseTool] = [buscar_comercio, listar_cobros, consultar_cobro, buscar_documentacion]
    for herramienta in herramientas:
        # Los ToolException vuelven al modelo como resultado, no cortan el grafo.
        herramienta.handle_tool_error = True
    return herramientas
