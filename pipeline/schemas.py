"""Contrato de salida del pipeline, validado con Pydantic v2.

`EntidadesTecnicas` cumple dos funciones. LangChain la convierte en el esquema
que el modelo tiene que llenar (por eso cada campo lleva `description`: es lo
que lee el LLM), y después valida lo que el modelo devolvió. Si algo no encaja,
la validación falla y la cadena reintenta.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NivelCriticidad(StrEnum):
    """Qué tan urgente es lo que describe el texto."""

    BAJA = "baja"
    MEDIA = "media"
    ALTA = "alta"


class EntidadesTecnicas(BaseModel):
    """Entidades técnicas extraídas de un texto: tecnologías, criticidad y resumen."""

    model_config = ConfigDict(extra="forbid")

    tecnologias: list[str] = Field(
        min_length=1,
        max_length=30,
        description=(
            "Tecnologías concretas mencionadas en el texto: lenguajes, frameworks, bases de "
            "datos, servicios cloud, colas, herramientas. Con su nombre propio (ej. 'PostgreSQL', "
            "no 'la base de datos'). Sin repetidos."
        ),
    )
    nivel_de_criticidad: NivelCriticidad = Field(
        description=(
            "'alta' si hay caída, pérdida de datos, riesgo de seguridad o impacto directo en "
            "usuarios; 'media' si hay degradación o un problema que va a escalar; 'baja' si es "
            "informativo o una mejora sin urgencia."
        ),
    )
    resumen_tecnico: str = Field(
        min_length=20,
        max_length=400,
        description="Resumen técnico de una o dos oraciones, en español, sin repetir el texto.",
    )

    @field_validator("tecnologias")
    @classmethod
    def _limpiar_tecnologias(cls, valores: list[str]) -> list[str]:
        """Recorta espacios y saca repetidos sin distinguir mayúsculas.

        Si después de limpiar no queda ninguna, falla: una lista de strings
        vacíos no es una extracción válida.
        """
        vistas: set[str] = set()
        limpias: list[str] = []
        for valor in valores:
            nombre = valor.strip()
            if nombre and nombre.casefold() not in vistas:
                vistas.add(nombre.casefold())
                limpias.append(nombre)
        if not limpias:
            raise ValueError("la lista de tecnologías no puede quedar vacía")
        return limpias

    @field_validator("nivel_de_criticidad", mode="before")
    @classmethod
    def _normalizar_criticidad(cls, valor: object) -> object:
        # El modelo a veces responde "Alta" o " media ". Eso es el mismo valor,
        # no un error que valga un reintento.
        if isinstance(valor, str):
            return valor.strip().lower()
        return valor

    @field_validator("resumen_tecnico")
    @classmethod
    def _recortar_resumen(cls, valor: str) -> str:
        return valor.strip()
