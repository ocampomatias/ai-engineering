"""Base de datos simulada de comercios y cobros, en SQLite en memoria.

Las herramientas del agente consultan esta base, pero el modelo nunca escribe
SQL: elige una herramienta y sus argumentos, Pydantic los valida, y la consulta
es siempre una de las de esta clase, parametrizada. Es el principio de mínimo
privilegio del módulo: el LLM no tiene forma de llegar a un `DROP TABLE`.
"""

import json
import sqlite3
import threading
import unicodedata
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ARCHIVO_DATOS = Path(__file__).resolve().parent / "datos_cobros.json"

EstadoCobro = Literal["aprobado", "rechazado", "en_revision", "reembolsado"]


def normalizar(texto: str) -> str:
    """Minúsculas y sin tildes: 'Ferreteria lopez' encuentra 'Ferretería López'."""
    descompuesto = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in descompuesto if not unicodedata.combining(c)).strip()


class Comercio(BaseModel):
    id: int
    nombre: str
    plan: str
    alta: str
    estado: str


class Cobro(BaseModel):
    id: str
    comercio_id: int
    fecha: str
    monto: str = Field(description="Monto formateado, ej. '45.990,00 ARS'.")
    estado: EstadoCobro
    codigo_error: str | None
    medio: str
    intentos: int


def _formatear_monto(centavos: int, moneda: str) -> str:
    entero, resto = divmod(centavos, 100)
    miles = f"{entero:,}".replace(",", ".")
    return f"{miles},{resto:02d} {moneda}"


class BaseCobros:
    """Las únicas consultas que pueden hacer las herramientas."""

    def __init__(self, archivo: Path = ARCHIVO_DATOS) -> None:
        datos = json.loads(archivo.read_text(encoding="utf-8"))
        # Las herramientas corren en threads (asyncio.to_thread): la conexión se
        # comparte entre threads y un lock serializa el acceso.
        self._conexion = sqlite3.connect(":memory:", check_same_thread=False)
        self._conexion.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._conexion.executescript(
            """
            CREATE TABLE comercios (
                id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, nombre_normalizado TEXT NOT NULL,
                plan TEXT NOT NULL, alta TEXT NOT NULL, estado TEXT NOT NULL
            );
            CREATE TABLE cobros (
                id TEXT PRIMARY KEY, comercio_id INTEGER NOT NULL REFERENCES comercios(id),
                fecha TEXT NOT NULL, monto_centavos INTEGER NOT NULL, moneda TEXT NOT NULL,
                estado TEXT NOT NULL, codigo_error TEXT, medio TEXT NOT NULL, intentos INTEGER NOT NULL
            );
            """
        )
        self._conexion.executemany(
            "INSERT INTO comercios VALUES (:id, :nombre, :nombre_normalizado, :plan, :alta, :estado)",
            [{**c, "nombre_normalizado": normalizar(c["nombre"])} for c in datos["comercios"]],
        )
        self._conexion.executemany(
            "INSERT INTO cobros VALUES (:id, :comercio_id, :fecha, :monto_centavos, :moneda, "
            ":estado, :codigo_error, :medio, :intentos)",
            datos["cobros"],
        )

    def _consultar(self, sql: str, parametros: tuple[object, ...]) -> list[sqlite3.Row]:
        with self._lock:
            return self._conexion.execute(sql, parametros).fetchall()

    def buscar_comercios(self, nombre: str) -> list[Comercio]:
        """Comercios cuyo nombre contiene todas las palabras buscadas (sin importar tildes)."""
        palabras = normalizar(nombre).split()
        if not palabras:
            return []
        condiciones = " AND ".join("nombre_normalizado LIKE ?" for _ in palabras)
        filas = self._consultar(
            f"SELECT id, nombre, plan, alta, estado FROM comercios WHERE {condiciones} ORDER BY id",
            tuple(f"%{p}%" for p in palabras),
        )
        return [Comercio(**dict(f)) for f in filas]

    def nombres_comercios(self) -> list[str]:
        return [f["nombre"] for f in self._consultar("SELECT nombre FROM comercios ORDER BY id", ())]

    def comercio(self, comercio_id: int) -> Comercio | None:
        filas = self._consultar(
            "SELECT id, nombre, plan, alta, estado FROM comercios WHERE id = ?", (comercio_id,)
        )
        return Comercio(**dict(filas[0])) if filas else None

    def _a_cobro(self, fila: sqlite3.Row) -> Cobro:
        datos = dict(fila)
        datos["monto"] = _formatear_monto(datos.pop("monto_centavos"), datos.pop("moneda"))
        return Cobro(**datos)

    def listar_cobros(
        self, comercio_id: int, estado: EstadoCobro | None = None, limite: int = 5
    ) -> list[Cobro]:
        """Cobros del comercio, del más reciente al más viejo."""
        sql = "SELECT * FROM cobros WHERE comercio_id = ?"
        parametros: tuple[object, ...] = (comercio_id,)
        if estado is not None:
            sql += " AND estado = ?"
            parametros += (estado,)
        sql += " ORDER BY fecha DESC LIMIT ?"
        return [self._a_cobro(f) for f in self._consultar(sql, (*parametros, limite))]

    def cobro(self, cobro_id: str) -> Cobro | None:
        filas = self._consultar("SELECT * FROM cobros WHERE id = ?", (cobro_id,))
        return self._a_cobro(filas[0]) if filas else None
