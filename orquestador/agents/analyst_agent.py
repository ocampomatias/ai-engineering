"""Agente analista: procesa los datos que trajo el investigador.

No consulta la base ni la documentación: sus herramientas son de cómputo.

- `estadisticas_cobros`: cantidades por estado, tasa de rechazo y montos por
  moneda. Hace las cuentas en Python con `Decimal`, no el LLM: un modelo de
  lenguaje suma mal montos como "45.990,00 ARS" + "12.500,00 ARS".
- `calcular`: aritmética para lo que no cubre la anterior (un porcentaje, un
  crédito del SLA). Evalúa la expresión recorriendo el árbol de `ast`, sin
  `eval`: solo números, + - * / y paréntesis.
"""

import ast
import json
import operator
import re
from collections import Counter
from decimal import Decimal, InvalidOperation

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, ToolException, tool
from pydantic import BaseModel, Field, field_validator

from agente.datos import EstadoCobro

from .especialista import NodoEspecialista, crear_especialista

_MONTO = re.compile(r"^\s*(\d{1,3}(?:\.\d{3})*|\d+),(\d{2})\s+([A-Z]{3})\s*$")


def parsear_monto(monto: str) -> tuple[Decimal, str]:
    """'45.990,00 ARS' -> (Decimal('45990.00'), 'ARS'). El formato que devuelven las herramientas."""
    coincide = _MONTO.match(monto)
    if not coincide:
        raise ValueError(f"monto con formato inválido: {monto!r} (se espera '45.990,00 ARS')")
    enteros, centavos, moneda = coincide.groups()
    return Decimal(f"{enteros.replace('.', '')}.{centavos}"), moneda


def formatear_monto(valor: Decimal, moneda: str) -> str:
    enteros, centavos = f"{valor:.2f}".split(".")
    return f"{int(enteros):,}".replace(",", ".") + f",{centavos} {moneda}"


def _porcentaje(parte: int, total: int) -> str:
    return f"{parte * 100 / total:.1f} %".replace(".", ",") if total else "0,0 %"


# --- Herramientas ----------------------------------------------------------


class CobroParaAnalizar(BaseModel):
    id: str = Field(pattern=r"^cob_[A-Z0-9]{5}$", description="ID del cobro, ej. cob_7F3K2.")
    monto: str = Field(description="Monto tal como lo dio el investigador, ej. '45.990,00 ARS'.")
    estado: EstadoCobro
    codigo_error: str | None = Field(default=None, description="Código TMB-XXXX, si tiene.")

    @field_validator("monto")
    @classmethod
    def _monto_valido(cls, valor: str) -> str:
        parsear_monto(valor)
        return valor


class EntradaEstadisticas(BaseModel):
    cobros: list[CobroParaAnalizar] = Field(min_length=1, description="Los cobros a analizar.")

    @field_validator("cobros")
    @classmethod
    def _sin_repetidos(cls, cobros: list[CobroParaAnalizar]) -> list[CobroParaAnalizar]:
        ids = [c.id for c in cobros]
        repetidos = sorted({i for i in ids if ids.count(i) > 1})
        if repetidos:
            raise ValueError(f"cobros repetidos: {repetidos}")
        return cobros


def calcular_estadisticas(cobros: list[CobroParaAnalizar]) -> dict[str, object]:
    por_estado = Counter(c.estado for c in cobros)
    montos: dict[str, dict[str, Decimal]] = {}
    for c in cobros:
        valor, moneda = parsear_monto(c.monto)
        de_moneda = montos.setdefault(moneda, {"total": Decimal(0)})
        de_moneda["total"] += valor
        de_moneda[c.estado] = de_moneda.get(c.estado, Decimal(0)) + valor
    no_cobrados = por_estado["rechazado"] + por_estado["en_revision"]
    return {
        "cobros": len(cobros),
        "por_estado": dict(por_estado),
        "tasa_rechazo": _porcentaje(por_estado["rechazado"], len(cobros)),
        "tasa_no_cobrados": _porcentaje(no_cobrados, len(cobros)),
        "montos_por_moneda": {
            moneda: {estado: formatear_monto(v, moneda) for estado, v in valores.items()}
            for moneda, valores in montos.items()
        },
        "codigos_error": dict(Counter(c.codigo_error for c in cobros if c.codigo_error)),
        "nota": "no_cobrados = rechazado + en_revision; los montos no se suman entre monedas.",
    }


_OPERADORES = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}


def evaluar_expresion(expresion: str) -> Decimal:
    """Aritmética segura: números, + - * / y paréntesis. Nada de nombres, llamadas ni potencias."""

    def evaluar(nodo: ast.AST) -> Decimal:
        if isinstance(nodo, ast.Expression):
            return evaluar(nodo.body)
        if isinstance(nodo, ast.Constant) and type(nodo.value) in (int, float):
            return Decimal(str(nodo.value))
        if isinstance(nodo, ast.UnaryOp) and isinstance(nodo.op, ast.USub | ast.UAdd):
            valor = evaluar(nodo.operand)
            return -valor if isinstance(nodo.op, ast.USub) else valor
        if isinstance(nodo, ast.BinOp) and type(nodo.op) in _OPERADORES:
            return _OPERADORES[type(nodo.op)](evaluar(nodo.left), evaluar(nodo.right))
        raise ValueError(f"no permitido en una expresión: {type(nodo).__name__}")

    if len(expresion) > 200:
        raise ValueError("expresión demasiado larga (máximo 200 caracteres)")
    try:
        return evaluar(ast.parse(expresion.replace(",", "."), mode="eval"))
    except (SyntaxError, ZeroDivisionError, InvalidOperation) as exc:
        raise ValueError(f"no se pudo calcular {expresion!r}: {exc}") from exc


class EntradaCalcular(BaseModel):
    expresion: str = Field(
        min_length=1,
        description="Expresión aritmética con números, + - * / y paréntesis. Ej: '(45990 + 12500) * 0.1'.",
    )


@tool(args_schema=EntradaEstadisticas)
def estadisticas_cobros(cobros: list[CobroParaAnalizar]) -> str:
    """Calcula estadísticas de una lista de cobros: cantidad por estado, tasa de rechazo, tasa de
    no cobrados (rechazados + en revisión), montos totales por moneda y estado, y frecuencia de
    cada código de error.

    Usala siempre que tengas que contar cobros o sumar montos: no hagas esas cuentas a mano.
    Pasá los cobros con el ID, el monto exactamente como vino ('45.990,00 ARS'), el estado y el
    código de error si tiene.
    """
    return json.dumps(calcular_estadisticas(cobros), ensure_ascii=False)


@tool(args_schema=EntradaCalcular)
def calcular(expresion: str) -> str:
    """Evalúa una expresión aritmética simple (+ - * / y paréntesis) y devuelve el resultado.

    Usala para cuentas puntuales que no cubre estadisticas_cobros, por ejemplo un porcentaje o
    el crédito que corresponde según el SLA. Escribí los montos como números sin separador de
    miles: 45990.00, no 45.990,00.
    """
    try:
        resultado = evaluar_expresion(expresion)
    except ValueError as exc:
        raise ToolException(str(exc)) from exc
    return json.dumps({"expresion": expresion, "resultado": format(resultado.normalize(), "f")})


def herramientas_analista() -> list[BaseTool]:
    herramientas: list[BaseTool] = [estadisticas_cobros, calcular]
    for herramienta in herramientas:
        herramienta.handle_tool_error = True
    return herramientas


PROMPT_ANALISTA = """\
Sos el analista de un equipo de soporte de Tambor, una plataforma de cobros (empresa ficticia). \
Recibís datos que ya consiguió el investigador y los procesás con tus herramientas.

- Todo conteo, porcentaje o suma de montos sale de estadisticas_cobros o calcular. No hagas \
cuentas a mano.
- Trabajá solo con los datos que recibiste. Si faltan datos para lo que te piden (por ejemplo, \
no hay cobros), decilo en vez de suponer.
- Validá los datos: si un monto, estado o código no tiene sentido, señalalo.
- Devolvé los resultados con los números exactos que dieron las herramientas y una conclusión \
breve. No redactes la respuesta final para el usuario: eso lo hace la síntesis.\
"""


def crear_analista(modelo: BaseChatModel) -> NodoEspecialista:
    return crear_especialista("analista", modelo, herramientas_analista(), PROMPT_ANALISTA)
