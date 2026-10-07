"""Herramientas del agente: el contrato que lee el LLM para decidir qué hacer.

Cada herramienta tiene un nombre, un docstring que el modelo usa para elegirla,
un esquema de entrada en Pydantic (valida los argumentos antes de tocar la
base) y una salida en JSON.

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
import json
from typing import Any

from langchain_core.tools import BaseTool, ToolException, tool
from pydantic import BaseModel, Field

from .datos import BaseCobros, EstadoCobro, normalizar
from .documentacion import Buscador

MAX_CARACTERES_FRAGMENTO = 900


def _json(datos: Any) -> str:  # noqa: ANN401
    return json.dumps(datos, ensure_ascii=False)


# --- Esquemas de entrada ---------------------------------------------------


class EntradaBuscarComercio(BaseModel):
    nombre: str = Field(
        min_length=2,
        description="Nombre del comercio o parte de él, tal como lo dijo el usuario. Ej: 'López'.",
    )


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


class EntradaBuscarDocumentacion(BaseModel):
    consulta: str = Field(
        min_length=3,
        description=(
            "Qué buscar. Para un error, el código exacto (ej. 'TMB-2031'); para un procedimiento, "
            "una descripción breve (ej. 'cómo pedir un crédito por caída del servicio')."
        ),
    )
    k: int = Field(default=3, ge=1, le=5, description="Cuántos fragmentos traer, de 1 a 5.")


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
        respuesta: dict[str, Any] = {"comercios": [c.model_dump() for c in encontrados]}
        if len(encontrados) > 1:
            respuesta["aviso"] = (
                f"Hay {len(encontrados)} comercios que coinciden. Si el usuario no dijo cuál, "
                "preguntale antes de consultar sus cobros."
            )
        return _json(respuesta)

    @tool(args_schema=EntradaListarCobros)
    async def listar_cobros(
        comercio_id: int, estado: EstadoCobro | None = None, limite: int = 5
    ) -> str:
        """Lista los cobros de un comercio, del más reciente al más viejo.

        Devuelve, para cada cobro, su ID, fecha, monto, estado y código de error (si falló). Usala
        para preguntas como "el último cobro", "los cobros rechazados" o "cuánto cobró". Para ver
        el detalle de un cobro puntual (medio de pago, intentos), usá consultar_cobro.
        """
        comercio = await asyncio.to_thread(base.comercio, comercio_id)
        if comercio is None:
            raise ToolException(
                f"No existe un comercio con ID {comercio_id}. Si tenés el nombre, usá "
                "buscar_comercio para obtener el ID correcto."
            )
        cobros = await asyncio.to_thread(base.listar_cobros, comercio_id, estado, limite)
        campos = {"id", "fecha", "monto", "estado", "codigo_error"}
        respuesta: dict[str, Any] = {
            "comercio": comercio.nombre,
            "filtro_estado": estado,
            "cobros": [c.model_dump(include=campos) for c in cobros],
        }
        if not cobros:
            respuesta["aviso"] = "El comercio no tiene cobros con ese filtro."
        return _json(respuesta)

    @tool(args_schema=EntradaConsultarCobro)
    async def consultar_cobro(cobro_id: str) -> str:
        """Devuelve el detalle de un cobro: comercio, fecha, monto, estado, código de error, medio
        de pago y cantidad de intentos.

        Usala cuando ya tenés el ID de un cobro (formato cob_XXXXX) y necesitás saber qué pasó con
        él. Si tiene código de error (TMB-XXXX), buscá ese código con buscar_documentacion para
        explicar qué significa y qué tiene que hacer el comercio.
        """
        cobro = await asyncio.to_thread(base.cobro, cobro_id)
        if cobro is None:
            raise ToolException(
                f"No existe el cobro {cobro_id}. Revisá el ID con listar_cobros."
            )
        comercio = await asyncio.to_thread(base.comercio, cobro.comercio_id)
        return _json({**cobro.model_dump(), "comercio": comercio.nombre if comercio else None})

    @tool(args_schema=EntradaBuscarDocumentacion)
    async def buscar_documentacion(consulta: str, k: int = 3) -> str:
        """Busca en la documentación interna de Tambor: catálogo de códigos de error de la API
        (TMB-XXXX), referencia de la API de cobros, SLA y soporte, política de despliegues, runbook
        de incidentes, seguridad y observabilidad.

        Usala para explicar qué significa un código de error y qué tiene que hacer el comercio, o
        para responder preguntas sobre reglas y procedimientos. Respondé solo con lo que devuelve:
        si no encontrás la respuesta, decilo.
        """
        try:
            documentos = await asyncio.to_thread(buscador.buscar, consulta, k)
        except Exception as exc:  # Pinecone caído, sin red, índice vacío
            raise ToolException(
                f"La búsqueda en la documentación no está disponible ({type(exc).__name__}: "
                f"{exc}). Avisale al usuario que no pudiste consultar la documentación."
            ) from exc
        if not documentos:
            return _json({"fragmentos": [], "aviso": "No se encontró nada para esa consulta."})
        return _json(
            {
                "fragmentos": [
                    {
                        "fuente": d.metadata.get("fuente"),
                        "seccion": d.metadata.get("seccion"),
                        "pagina": d.metadata.get("pagina"),
                        "texto": " ".join(d.page_content.split())[:MAX_CARACTERES_FRAGMENTO],
                    }
                    for d in documentos
                ]
            }
        )

    herramientas: list[BaseTool] = [buscar_comercio, listar_cobros, consultar_cobro, buscar_documentacion]
    for herramienta in herramientas:
        # Los ToolException vuelven al modelo como resultado, no cortan el grafo.
        herramienta.handle_tool_error = True
    return herramientas
