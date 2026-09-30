"""Carga y fragmentación del corpus: Markdown, PDF y JSON a fragmentos con metadatos.

    catálogo (_metadatos.json) + archivos -> unidades -> fragmentos

Cada formato se parte en "unidades" con sentido propio antes del splitter:

- Markdown: el documento entero. El splitter corta primero en los títulos.
- PDF: una unidad por página, para que cada fragmento sepa de qué página vino.
  Antes se sacan el encabezado y el pie que se repiten en todas las páginas.
- JSON: una unidad por registro (en el catálogo de errores, un código). Un
  registro es chico y autocontenido: partirlo o pegarlo con el siguiente
  mezclaría dos códigos en el mismo vector.

La limpieza y el splitter son los de la pre-entrega 3 (tokens de tiktoken y
separadores de Markdown), así que los dos sistemas fragmentan igual.
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from langchain_text_splitters import Language, RecursiveCharacterTextSplitter
from pydantic import ValidationError

from rag.ingesta import ENCODING_TIKTOKEN, IngestaError, _posiciones, _seccion_en, limpiar_texto

from .config import ConfigPinecone
from .schemas import MetadatosDocumento, MetadatosFragmento, TipoDocumento

logger = logging.getLogger(__name__)

ARCHIVO_CATALOGO = "_metadatos.json"
TIPOS: dict[str, TipoDocumento] = {".md": "markdown", ".pdf": "pdf", ".json": "json"}


@dataclass(frozen=True)
class Unidad:
    """Un pedazo de documento con sentido propio: el Markdown entero, una página, un registro."""

    texto: str
    pagina: int | None = None
    seccion: str | None = None


# --- Catálogo --------------------------------------------------------------


def leer_catalogo(carpeta: Path) -> dict[str, MetadatosDocumento]:
    """Lee `_metadatos.json` y lo cruza con los archivos de la carpeta.

    Un archivo sin entrada en el catálogo, o una entrada sin archivo, es un
    error: es la forma de que ningún fragmento se suba sin sus metadatos.
    """
    if not carpeta.is_dir():
        raise IngestaError(f"no existe la carpeta del corpus: {carpeta}")
    ruta = carpeta / ARCHIVO_CATALOGO
    if not ruta.is_file():
        raise IngestaError(f"falta el catálogo de metadatos {ruta}")

    crudo = json.loads(ruta.read_text(encoding="utf-8"))
    catalogo: dict[str, MetadatosDocumento] = {}
    for nombre, valores in crudo.items():
        try:
            catalogo[nombre] = MetadatosDocumento.model_validate(valores)
        except ValidationError as exc:
            raise IngestaError(f"metadatos inválidos para {nombre} en {ARCHIVO_CATALOGO}:\n{exc}")

    archivos = {
        p.name
        for p in carpeta.iterdir()
        if p.is_file() and p.suffix.lower() in TIPOS and p.name != ARCHIVO_CATALOGO
    }
    sin_metadatos = sorted(archivos - catalogo.keys())
    sin_archivo = sorted(catalogo.keys() - archivos)
    if sin_metadatos:
        raise IngestaError(f"archivos sin entrada en {ARCHIVO_CATALOGO}: {sin_metadatos}")
    if sin_archivo:
        raise IngestaError(f"entradas de {ARCHIVO_CATALOGO} sin archivo: {sin_archivo}")
    return dict(sorted(catalogo.items()))


# --- Lectores por formato --------------------------------------------------


def _leer_markdown(ruta: Path) -> list[Unidad]:
    return [Unidad(limpiar_texto(ruta.read_text(encoding="utf-8")))]


def _quitar_repetidas(paginas: list[str]) -> list[str]:
    """Saca las líneas que aparecen en todas las páginas (encabezado y pie).

    Se comparan con los números reemplazados, porque el pie cambia de página
    en página ("página 1 de 3", "página 2 de 3"). Con una sola página no hay
    forma de distinguir un pie de un párrafo, así que no se toca.
    """
    if len(paginas) < 2:
        return paginas

    def clave(linea: str) -> str:
        return re.sub(r"\d+", "#", linea.strip())

    por_pagina = [{clave(linea) for linea in p.splitlines() if linea.strip()} for p in paginas]
    repetidas = set.intersection(*por_pagina)
    if repetidas:
        logger.info("carga: se descartan %d línea(s) repetidas en todas las páginas", len(repetidas))
    return [
        "\n".join(linea for linea in p.splitlines() if clave(linea) not in repetidas)
        for p in paginas
    ]


def _leer_pdf(ruta: Path) -> list[Unidad]:
    from pypdf import PdfReader

    paginas = [pagina.extract_text() or "" for pagina in PdfReader(ruta).pages]
    paginas = [limpiar_texto(p) for p in _quitar_repetidas(paginas)]
    unidades = []
    for numero, texto in enumerate(paginas, start=1):
        if not texto:
            continue
        primera_linea = texto.splitlines()[0].strip()
        unidades.append(Unidad(texto, pagina=numero, seccion=primera_linea))
    return unidades


def _leer_json(ruta: Path) -> list[Unidad]:
    """Una unidad por registro de la primera lista de objetos del JSON.

    Cada registro se escribe como texto "clave: valor", con el título del
    archivo adelante: sin él, "Monto inválido" no dice de qué sistema es.
    """
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    if not isinstance(datos, dict):
        raise IngestaError(f"{ruta.name}: se espera un objeto JSON con una lista de registros")
    registros = next(
        (v for v in datos.values() if isinstance(v, list) and v and isinstance(v[0], dict)),
        None,
    )
    if registros is None:
        raise IngestaError(f"{ruta.name}: no tiene ninguna lista de registros")

    titulo = str(datos.get("titulo", ruta.stem))
    unidades = []
    for registro in registros:
        identificador = str(next(iter(registro.values())))
        cuerpo = "\n".join(f"{clave}: {valor}" for clave, valor in registro.items())
        unidades.append(
            Unidad(limpiar_texto(f"{titulo}\n\n{cuerpo}"), seccion=identificador)
        )
    return unidades


LECTORES = {"markdown": _leer_markdown, "pdf": _leer_pdf, "json": _leer_json}


# --- Fragmentación ---------------------------------------------------------


def crear_splitter(config: ConfigPinecone) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name=ENCODING_TIKTOKEN,
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
        separators=RecursiveCharacterTextSplitter.get_separators_for_language(Language.MARKDOWN),
        is_separator_regex=True,
    )


def id_de_fragmento(documento_id: str, pagina: int | None, texto: str) -> str:
    """ID determinista y con el documento como prefijo.

    El prefijo `documento#` permite listar en Pinecone los vectores de un
    documento (`index.list(prefix=...)`) sin tener que recorrer todo el índice.
    """
    huella = hashlib.sha256(f"{documento_id}\x00{pagina}\x00{texto}".encode()).hexdigest()[:12]
    return f"{documento_id}#{huella}"


def fragmentar_documento(
    ruta: Path, meta: MetadatosDocumento, config: ConfigPinecone
) -> list[MetadatosFragmento]:
    tipo = TIPOS[ruta.suffix.lower()]
    splitter = crear_splitter(config)
    contar = splitter._length_function  # el mismo contador de tokens que usa el splitter

    partes: list[tuple[str, Unidad]] = []
    for unidad in LECTORES[tipo](ruta):
        textos = splitter.split_text(unidad.texto) if unidad.texto else []
        inicios = _posiciones(unidad.texto, textos)
        for texto, inicio in zip(textos, inicios, strict=True):
            seccion = unidad.seccion
            if seccion is None:
                seccion = _seccion_en(unidad.texto, inicio) if inicio >= 0 else None
            partes.append((texto, Unidad(texto, unidad.pagina, seccion or "")))

    documento_id = ruta.stem.lower()
    fragmentos = []
    for indice, (texto, unidad) in enumerate(partes):
        fragmentos.append(
            MetadatosFragmento(
                chunk_id=id_de_fragmento(documento_id, unidad.pagina, texto),
                documento_id=documento_id,
                fuente=ruta.name,
                tipo=tipo,
                categoria=meta.categoria,
                audiencia=meta.audiencia,
                etiquetas=list(meta.etiquetas),
                actualizado=int(meta.actualizado.strftime("%Y%m%d")),
                pagina=unidad.pagina,
                seccion=unidad.seccion or "",
                chunk=indice,
                total_chunks=len(partes),
                tokens=contar(texto),
                texto=texto,
            )
        )
    return fragmentos


def cargar_corpus(config: ConfigPinecone) -> list[MetadatosFragmento]:
    """Lee el corpus completo y lo devuelve fragmentado, con los metadatos validados."""
    catalogo = leer_catalogo(config.carpeta_corpus)
    fragmentos: list[MetadatosFragmento] = []
    for nombre, meta in catalogo.items():
        del_documento = fragmentar_documento(config.carpeta_corpus / nombre, meta, config)
        if not del_documento:
            logger.warning("carga: %s no tiene texto, se saltea", nombre)
            continue
        fragmentos.extend(del_documento)

    if not fragmentos:
        raise IngestaError(f"el corpus de {config.carpeta_corpus} no tiene texto para indexar")

    ids = [f.chunk_id for f in fragmentos]
    if len(ids) != len(set(ids)):
        raise IngestaError("hay fragmentos repetidos (mismo documento, página y texto)")

    tokens = [f.tokens for f in fragmentos]
    por_tipo: dict[str, int] = {}
    for f in fragmentos:
        por_tipo[f.tipo] = por_tipo.get(f.tipo, 0) + 1
    logger.info(
        "carga: %d documento(s), %d fragmento(s) (%s), entre %d y %d tokens",
        len({f.documento_id for f in fragmentos}),
        len(fragmentos),
        ", ".join(f"{n} {t}" for t, n in sorted(por_tipo.items())),
        min(tokens),
        max(tokens),
    )
    return fragmentos
