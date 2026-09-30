"""Tests del RAG en Pinecone (pre-entrega 4). Sin API keys, sin red y sin descargar modelos.

- Pinecone es un índice falso en memoria con la misma interfaz que usa el
  código (upsert, query, list, fetch, delete, describe_index_stats), con
  similitud coseno y los filtros de metadatos de Pinecone.
- Los embeddings son una bolsa de palabras con hashing, como en test_rag.py.
- BM25, EnsembleRetriever y PineconeVectorStore son los reales.

    python -m pytest -q tests/test_rag_pinecone.py
"""

from __future__ import annotations

import json
import math
import shutil
import zlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.embeddings import Embeddings
from pydantic import ValidationError

from rag_pinecone import (
    ConfigError,
    ConfigPinecone,
    GoldenSet,
    IndiceError,
    IngestaError,
    MetadatosFragmento,
    RAGSystem,
    agregar,
    asegurar_indice,
    cargar_config,
    cargar_corpus,
    cumple_filtro,
    ingestar,
    leer_catalogo,
    medir_pregunta,
    tokenizar,
)
from rag_pinecone.carga import id_de_fragmento
from rag_pinecone.infra import TAG_MODELO, _valor_tag
from rag_pinecone.metricas import PreguntaGolden, Recuperado, precision_maxima

RAIZ = Path(__file__).resolve().parent.parent
DIMENSION = 64

# --- Dobles -----------------------------------------------------------------


class EmbeddingsHash(Embeddings):
    """Bolsa de palabras con hashing: textos con palabras en común quedan cerca."""

    def _vector(self, texto: str) -> list[float]:
        v = [0.0] * DIMENSION
        for token in tokenizar(texto):
            v[zlib.crc32(token.encode()) % DIMENSION] += 1.0
        norma = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norma for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class IndexFalso:
    """Lo que el código usa de `pinecone.Index`, en memoria."""

    def __init__(self) -> None:
        self.config = SimpleNamespace(host="indice-falso.pinecone.io", api_key="falsa")
        self.namespaces: dict[str, dict[str, dict[str, Any]]] = {}
        self.lotes_upsert: list[int] = []

    def upsert(self, vectors: list[dict[str, Any]], namespace: str) -> SimpleNamespace:
        destino = self.namespaces.setdefault(namespace, {})
        for v in vectors:
            if len(v["values"]) != DIMENSION:
                raise ValueError("dimensión incorrecta")
            destino[v["id"]] = {"values": v["values"], "metadata": dict(v["metadata"])}
        self.lotes_upsert.append(len(vectors))
        return SimpleNamespace(upserted_count=len(vectors))

    def query(
        self,
        vector: list[float],
        top_k: int,
        namespace: str,
        include_metadata: bool = True,
        filter: dict[str, Any] | None = None,  # noqa: A002 - nombre del SDK
        **_: Any,
    ) -> dict[str, Any]:
        candidatos = []
        for id_, v in self.namespaces.get(namespace, {}).items():
            if not cumple_filtro(v["metadata"], filter):
                continue
            score = sum(a * b for a, b in zip(vector, v["values"], strict=True))
            candidatos.append({"id": id_, "score": score, "metadata": dict(v["metadata"])})
        candidatos.sort(key=lambda m: m["score"], reverse=True)
        return {"matches": candidatos[:top_k]}

    def list(self, namespace: str, prefix: str | None = None):  # noqa: ANN201
        ids = sorted(i for i in self.namespaces.get(namespace, {}) if i.startswith(prefix or ""))
        for inicio in range(0, len(ids), 100):
            yield ids[inicio : inicio + 100]

    def fetch(self, ids: list[str], namespace: str) -> SimpleNamespace:
        datos = self.namespaces.get(namespace, {})
        return SimpleNamespace(
            vectors={
                i: SimpleNamespace(metadata=dict(datos[i]["metadata"])) for i in ids if i in datos
            }
        )

    def delete(
        self, ids: list[str] | None = None, delete_all: bool = False, namespace: str = ""
    ) -> dict[str, Any]:
        if delete_all:
            self.namespaces.pop(namespace, None)
        for i in ids or []:
            self.namespaces.get(namespace, {}).pop(i, None)
        return {}

    def describe_index_stats(self) -> SimpleNamespace:
        return SimpleNamespace(
            namespaces={
                ns: SimpleNamespace(vector_count=len(v)) for ns, v in self.namespaces.items()
            }
        )


class ClienteFalso:
    """Lo que el código usa de `pinecone.Pinecone`."""

    def __init__(self) -> None:
        self.indices: dict[str, SimpleNamespace] = {}
        self.index = IndexFalso()

    def has_index(self, name: str) -> bool:
        return name in self.indices

    def create_index(self, name: str, dimension: int, metric: str, spec: Any, tags: dict) -> None:  # noqa: ANN401
        self.indices[name] = SimpleNamespace(
            dimension=dimension,
            metric=metric,
            tags=tags,
            host=self.index.config.host,
            status={"ready": True},
            spec=spec,
        )

    def describe_index(self, name: str) -> SimpleNamespace:
        return self.indices[name]

    def Index(self, name: str) -> IndexFalso:  # noqa: N802 - nombre del SDK
        return self.index


# --- Corpus de prueba -----------------------------------------------------------

MARKDOWN = (
    "# Despliegues\n\n## Ventanas\n\nLos viernes se despliega solo entre las 10:00 y las 15:00. "
    "Fuera de ese horario no se despliega a producción.\n\n## Rollback\n\nEl rollback se hace con "
    "argocd app rollback y tiene que quedar completo en menos de 10 minutos."
)
SEGURIDAD = (
    "# Seguridad\n\n## Secretos\n\nLos secretos se rotan cada 90 días de forma automática. El "
    "acceso a producción dura como máximo 4 horas."
)
ERRORES = {
    "titulo": "Catálogo de errores",
    "errores": [
        {"codigo": "TMB-1009", "mensaje": "Conflicto de idempotencia", "accion": "Generar otra clave"},
        {"codigo": "TMB-2012", "mensaje": "Timeout del adquirente", "accion": "Consultar el estado"},
        {"codigo": "TMB-3001", "mensaje": "Reembolso fuera de plazo", "accion": "Transferir"},
    ],
}
CATALOGO = {
    "despliegues.md": {
        "categoria": "procesos",
        "audiencia": "interna",
        "etiquetas": ["despliegues", "rollback"],
        "actualizado": "2026-07-15",
    },
    "seguridad.md": {
        "categoria": "seguridad",
        "audiencia": "interna",
        "etiquetas": ["secretos"],
        "actualizado": "2026-06-30",
    },
    "errores.json": {
        "categoria": "producto",
        "audiencia": "comercios",
        "etiquetas": ["api", "errores"],
        "actualizado": "2026-09-01",
    },
    "sla-y-soporte.pdf": {
        "categoria": "comercial",
        "audiencia": "comercios",
        "etiquetas": ["sla"],
        "actualizado": "2026-07-01",
    },
}


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    carpeta = tmp_path / "corpus"
    carpeta.mkdir()
    (carpeta / "despliegues.md").write_text(MARKDOWN, encoding="utf-8")
    (carpeta / "seguridad.md").write_text(SEGURIDAD, encoding="utf-8")
    (carpeta / "errores.json").write_text(json.dumps(ERRORES, ensure_ascii=False), encoding="utf-8")
    shutil.copy(RAIZ / "corpus" / "sla-y-soporte.pdf", carpeta / "sla-y-soporte.pdf")
    (carpeta / "_metadatos.json").write_text(json.dumps(CATALOGO), encoding="utf-8")
    return carpeta


@pytest.fixture
def config(corpus: Path) -> ConfigPinecone:
    return ConfigPinecone(
        api_key="falsa",
        indice="test-indice",
        namespace="test",
        carpeta_corpus=corpus,
        top_k=3,
        candidatos=5,
        tamano_lote=4,
    )


@pytest.fixture
def cliente() -> ClienteFalso:
    return ClienteFalso()


@pytest.fixture
def sistema(config: ConfigPinecone, cliente: ClienteFalso) -> RAGSystem:
    ingestar(config, cliente=cliente, embeddings=EmbeddingsHash(), esperar=False)
    return RAGSystem(config, cliente=cliente, embeddings=EmbeddingsHash())


# --- Configuración ---------------------------------------------------------


def test_config_rechaza_overlap_grande() -> None:
    with pytest.raises(ValidationError, match="chunk_overlap"):
        ConfigPinecone(chunk_size=400, chunk_overlap=200)


def test_config_rechaza_menos_candidatos_que_top_k() -> None:
    with pytest.raises(ValidationError, match="candidatos"):
        ConfigPinecone(top_k=5, candidatos=3)


def test_config_pesos_suman_uno() -> None:
    assert ConfigPinecone(peso_vectorial=0.7).pesos == [0.7, 0.3]


def test_config_sin_key_da_error_claro() -> None:
    with pytest.raises(ConfigError, match="PINECONE_API_KEY"):
        ConfigPinecone().key()


def test_config_no_muestra_la_key() -> None:
    assert "secreta" not in repr(ConfigPinecone(api_key="secreta"))


def test_cargar_config_lee_el_entorno(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text("PINECONE_API_KEY=pk-123\nINDEX_NAME=mi-indice\n", encoding="utf-8")
    for nombre in ("PINECONE_API_KEY", "INDEX_NAME", "PINECONE_NAMESPACE"):
        monkeypatch.delenv(nombre, raising=False)
    config = cargar_config(env, namespace="otro")
    assert config.key() == "pk-123"
    assert config.indice == "mi-indice"
    assert config.namespace == "otro"


def test_config_rechaza_nombre_de_indice_invalido() -> None:
    with pytest.raises(ValidationError):
        ConfigPinecone(indice="Mi_Indice")


# --- Catálogo y carga ------------------------------------------------------


def test_catalogo_exige_metadatos_para_cada_archivo(corpus: Path) -> None:
    (corpus / "nuevo.md").write_text("# Nuevo\n\nTexto.", encoding="utf-8")
    with pytest.raises(IngestaError, match="sin entrada"):
        leer_catalogo(corpus)


def test_catalogo_exige_archivo_para_cada_entrada(corpus: Path) -> None:
    (corpus / "seguridad.md").unlink()
    with pytest.raises(IngestaError, match="sin archivo"):
        leer_catalogo(corpus)


def test_catalogo_rechaza_categoria_desconocida(corpus: Path) -> None:
    catalogo = dict(CATALOGO)
    catalogo["seguridad.md"] = {**CATALOGO["seguridad.md"], "categoria": "legales"}
    (corpus / "_metadatos.json").write_text(json.dumps(catalogo), encoding="utf-8")
    with pytest.raises(IngestaError, match="seguridad.md"):
        leer_catalogo(corpus)


def test_catalogo_rechaza_campos_extra(corpus: Path) -> None:
    catalogo = dict(CATALOGO)
    catalogo["seguridad.md"] = {**CATALOGO["seguridad.md"], "fecha": "2026-01-01"}
    (corpus / "_metadatos.json").write_text(json.dumps(catalogo), encoding="utf-8")
    with pytest.raises(IngestaError):
        leer_catalogo(corpus)


def test_carga_markdown_con_secciones(config: ConfigPinecone) -> None:
    fragmentos = [f for f in cargar_corpus(config) if f.documento_id == "despliegues"]
    assert fragmentos and all(f.tipo == "markdown" and f.pagina is None for f in fragmentos)
    assert fragmentos[0].categoria == "procesos"
    assert fragmentos[0].actualizado == 20260715


def test_carga_json_un_fragmento_por_registro(config: ConfigPinecone) -> None:
    fragmentos = [f for f in cargar_corpus(config) if f.documento_id == "errores"]
    assert [f.seccion for f in fragmentos] == ["TMB-1009", "TMB-2012", "TMB-3001"]
    assert all("Catálogo de errores" in f.texto for f in fragmentos)
    assert all(f.total_chunks == 3 for f in fragmentos)


def test_carga_pdf_con_pagina_y_sin_pie(config: ConfigPinecone) -> None:
    fragmentos = [f for f in cargar_corpus(config) if f.documento_id == "sla-y-soporte"]
    assert sorted({f.pagina for f in fragmentos}) == [1, 2, 3]
    assert not any("página 1 de 3" in f.texto for f in fragmentos)
    credito = next(f for f in fragmentos if "crédito del 50 %" in f.texto)
    assert credito.pagina == 2


def test_ids_deterministas_y_con_prefijo(config: ConfigPinecone) -> None:
    primera = [f.chunk_id for f in cargar_corpus(config)]
    segunda = [f.chunk_id for f in cargar_corpus(config)]
    assert primera == segunda
    assert all(i.split("#")[0] in {"despliegues", "seguridad", "errores", "sla-y-soporte"} for i in primera)
    assert id_de_fragmento("a", 1, "x") != id_de_fragmento("a", 2, "x")


def test_metadatos_sin_pagina_no_mandan_null(config: ConfigPinecone) -> None:
    md = next(f for f in cargar_corpus(config) if f.tipo == "markdown")
    assert "pagina" not in md.para_pinecone()
    assert md.para_pinecone()["texto"] == md.texto


def _fragmento(**cambios: Any) -> dict[str, Any]:  # noqa: ANN401
    base = {
        "chunk_id": "doc#0123456789ab",
        "documento_id": "doc",
        "fuente": "doc.md",
        "tipo": "markdown",
        "categoria": "procesos",
        "audiencia": "interna",
        "etiquetas": ["x"],
        "actualizado": 20260101,
        "seccion": "",
        "chunk": 0,
        "total_chunks": 1,
        "tokens": 3,
        "texto": "hola",
    }
    return {**base, **cambios}


def test_metadatos_fragmento_validos() -> None:
    MetadatosFragmento.model_validate(_fragmento())


@pytest.mark.parametrize(
    "cambios",
    [
        {"fecha": "2026-01-01"},  # schema drift: un campo con otro nombre
        {"pagina": 2},  # página en un Markdown
        {"tipo": "pdf"},  # PDF sin página
        {"chunk_id": "otro#0123456789ab"},  # ID de otro documento
        {"chunk": 1},  # fuera de rango
        {"categoria": "legales"},
    ],
)
def test_metadatos_fragmento_invalidos(cambios: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        MetadatosFragmento.model_validate(_fragmento(**cambios))


# --- Tokenización y filtros --------------------------------------------------


def test_tokenizar_codigos_tildes_y_stopwords() -> None:
    tokens = tokenizar("¿Qué significa el error TMB-1009? Rotación de la clave.")
    assert "tmb-1009" in tokens and "tmb" in tokens and "1009" in tokens
    assert "rotacion" in tokens
    assert "que" not in tokens and "el" not in tokens and "de" not in tokens


@pytest.mark.parametrize(
    ("filtro", "esperado"),
    [
        ({"categoria": "seguridad"}, True),
        ({"categoria": {"$eq": "producto"}}, False),
        ({"etiquetas": {"$in": ["pci-dss", "otra"]}}, True),
        ({"etiquetas": {"$eq": "secretos"}}, True),
        ({"actualizado": {"$gte": 20260701}}, False),
        ({"pagina": {"$ne": 2}}, True),
        ({"pagina": {"$gt": 0}}, False),
        ({"$and": [{"categoria": "seguridad"}, {"audiencia": "interna"}]}, True),
        ({"$or": [{"categoria": "producto"}, {"audiencia": "interna"}]}, True),
        (None, True),
    ],
)
def test_cumple_filtro(filtro: dict[str, Any] | None, esperado: bool) -> None:
    metadatos = {
        "categoria": "seguridad",
        "audiencia": "interna",
        "etiquetas": ["secretos", "pci-dss"],
        "actualizado": 20260630,
    }
    assert cumple_filtro(metadatos, filtro) is esperado


def test_cumple_filtro_operador_desconocido() -> None:
    with pytest.raises(ValueError, match="no soportado"):
        cumple_filtro({"a": 1}, {"a": {"$regex": "x"}})


# --- Infraestructura -------------------------------------------------------


def test_crea_indice_serverless_con_tags(config: ConfigPinecone, cliente: ClienteFalso) -> None:
    assert asegurar_indice(cliente, config, DIMENSION) is True
    indice = cliente.indices["test-indice"]
    assert indice.dimension == DIMENSION and indice.metric == "cosine"
    assert indice.spec.cloud == "aws" and indice.spec.region == "us-east-1"
    assert indice.tags[TAG_MODELO] == _valor_tag(config.modelo_embeddings)
    assert asegurar_indice(cliente, config, DIMENSION) is False  # la segunda vez no lo crea


def test_indice_con_otra_dimension(config: ConfigPinecone, cliente: ClienteFalso) -> None:
    asegurar_indice(cliente, config, 1536)
    with pytest.raises(IndiceError, match="dimensión 1536"):
        asegurar_indice(cliente, config, DIMENSION)


def test_indice_con_otra_metrica(config: ConfigPinecone, cliente: ClienteFalso) -> None:
    asegurar_indice(cliente, config, DIMENSION)
    cliente.indices["test-indice"].metric = "euclidean"
    with pytest.raises(IndiceError, match="euclidean"):
        asegurar_indice(cliente, config, DIMENSION)


def test_indice_de_otro_modelo(config: ConfigPinecone, cliente: ClienteFalso) -> None:
    asegurar_indice(cliente, config, DIMENSION)
    cliente.indices["test-indice"].tags[TAG_MODELO] = "otro_modelo"
    with pytest.raises(IndiceError, match="otro_modelo"):
        asegurar_indice(cliente, config, DIMENSION)


# --- Ingesta ---------------------------------------------------------------


def _ingestar(config: ConfigPinecone, cliente: ClienteFalso, **kwargs: Any):  # noqa: ANN202, ANN401
    return ingestar(config, cliente=cliente, embeddings=EmbeddingsHash(), esperar=False, **kwargs)


def test_ingesta_por_lotes_con_texto_en_metadatos(
    config: ConfigPinecone, cliente: ClienteFalso
) -> None:
    resumen = _ingestar(config, cliente)
    total = len(cargar_corpus(config))
    assert resumen.agregados == resumen.fragmentos == total
    assert cliente.index.lotes_upsert == [4] * (total // 4) + ([total % 4] if total % 4 else [])
    assert resumen.lotes == len(cliente.index.lotes_upsert)
    guardado = next(iter(cliente.index.namespaces["test"].values()))["metadata"]
    assert guardado["texto"] and guardado["categoria"] and guardado["chunk_id"]


def test_ingesta_idempotente(config: ConfigPinecone, cliente: ClienteFalso) -> None:
    _ingestar(config, cliente)
    lotes_antes = len(cliente.index.lotes_upsert)
    resumen = _ingestar(config, cliente)
    assert resumen.sin_cambios
    assert len(cliente.index.lotes_upsert) == lotes_antes


def test_ingesta_solo_sube_lo_que_cambio(config: ConfigPinecone, cliente: ClienteFalso) -> None:
    _ingestar(config, cliente)
    (config.carpeta_corpus / "seguridad.md").write_text(
        SEGURIDAD.replace("90 días", "60 días"), encoding="utf-8"
    )
    resumen = _ingestar(config, cliente)
    assert resumen.agregados == 1 and resumen.eliminados == 1
    textos = [v["metadata"]["texto"] for v in cliente.index.namespaces["test"].values()]
    assert any("60 días" in t for t in textos) and not any("90 días" in t for t in textos)


def test_ingesta_reiniciar(config: ConfigPinecone, cliente: ClienteFalso) -> None:
    _ingestar(config, cliente)
    resumen = _ingestar(config, cliente, reiniciar=True)
    assert resumen.reiniciado and resumen.agregados == resumen.fragmentos


def test_ingesta_no_toca_otros_namespaces(config: ConfigPinecone, cliente: ClienteFalso) -> None:
    _ingestar(config, cliente)
    otro = config.model_copy(update={"namespace": "otro"})
    _ingestar(otro, cliente, reiniciar=True)
    assert set(cliente.index.namespaces) == {"test", "otro"}


# --- RAGSystem -------------------------------------------------------------


def test_rag_system_falla_si_no_hay_indice(config: ConfigPinecone, cliente: ClienteFalso) -> None:
    with pytest.raises(IndiceError, match="no existe"):
        RAGSystem(config, cliente=cliente, embeddings=EmbeddingsHash())


def test_rag_system_falla_si_el_namespace_esta_vacio(
    config: ConfigPinecone, cliente: ClienteFalso
) -> None:
    asegurar_indice(cliente, config, DIMENSION)
    with pytest.raises(IndiceError, match="vacío"):
        RAGSystem(config, cliente=cliente, embeddings=EmbeddingsHash())


def test_bm25_se_arma_desde_pinecone(sistema: RAGSystem, config: ConfigPinecone) -> None:
    assert len(sistema.corpus) == len(cargar_corpus(config))
    assert all("texto" not in d.metadata and d.page_content for d in sistema.corpus)


def test_buscar_devuelve_top_k_sin_repetidos(sistema: RAGSystem) -> None:
    documentos = sistema.buscar("¿Cuándo se despliega a producción los viernes?")
    ids = [d.metadata["chunk_id"] for d in documentos]
    assert len(ids) == 3 and len(set(ids)) == 3
    assert documentos[0].metadata["documento_id"] == "despliegues"


def test_bm25_encuentra_el_codigo_exacto(sistema: RAGSystem) -> None:
    documentos = sistema.buscar("TMB-2012", modo="bm25")
    assert documentos[0].metadata["seccion"] == "TMB-2012"


def test_bm25_no_devuelve_documentos_sin_coincidencias(sistema: RAGSystem) -> None:
    assert sistema.buscar("zzz inexistente", modo="bm25") == []


def test_hibrido_combina_las_dos_listas(sistema: RAGSystem) -> None:
    resultado = sistema.explicar("error TMB-2012 timeout adquirente")
    assert resultado[0].seccion == "TMB-2012"
    assert resultado[0].posicion_bm25 == 1
    assert resultado[0].puntaje_rrf >= resultado[-1].puntaje_rrf
    assert [r.posicion for r in resultado] == [1, 2, 3]


def test_filtro_se_aplica_en_las_dos_mitades(sistema: RAGSystem) -> None:
    filtro = {"categoria": {"$eq": "seguridad"}}
    for modo in ("hibrido", "vectorial", "bm25"):
        documentos = sistema.buscar("despliegues viernes secretos", filtro=filtro, modo=modo)
        assert documentos and all(d.metadata["categoria"] == "seguridad" for d in documentos)


def test_filtro_por_pagina_del_pdf(sistema: RAGSystem) -> None:
    documentos = sistema.buscar("crédito disponibilidad", filtro={"pagina": {"$eq": 2}}, modo="bm25")
    assert documentos and all(d.metadata["pagina"] == 2 for d in documentos)


def test_consulta_vacia(sistema: RAGSystem) -> None:
    with pytest.raises(ValueError, match="vacía"):
        sistema.buscar("   ")


async def test_abuscar(sistema: RAGSystem) -> None:
    documentos = await sistema.abuscar("argocd app rollback", k=2)
    assert len(documentos) == 2
    assert documentos[0].metadata["documento_id"] == "despliegues"


# --- Métricas --------------------------------------------------------------

PREGUNTA = PreguntaGolden(
    id="p1",
    pregunta="¿Qué es TMB-1009?",
    documento_id_esperado="errores",
    contiene="TMB-1009",
    tipo="lexica",
)


def _r(documento_id: str, texto: str = "x") -> Recuperado:
    return Recuperado(documento_id=documento_id, texto=texto)


def test_medir_pregunta() -> None:
    recuperados = [_r("otro"), _r("errores", "codigo: TMB-1009"), _r("errores"), _r("otro"), _r("x")]
    m = medir_pregunta(PREGUNTA, recuperados, k=5)
    assert m.recall == 1.0
    assert m.precision == pytest.approx(0.4)
    assert m.reciprocal_rank == 0.5 and m.posicion == 2
    assert m.acierto_fragmento == 1.0


def test_medir_pregunta_sin_acierto_y_con_menos_de_k() -> None:
    m = medir_pregunta(PREGUNTA, [_r("otro")], k=5)
    assert (m.recall, m.precision, m.reciprocal_rank, m.acierto_fragmento) == (0, 0, 0, 0)
    assert m.posicion is None


def test_medir_pregunta_ignora_lo_que_pasa_de_k() -> None:
    m = medir_pregunta(PREGUNTA, [_r("otro")] * 5 + [_r("errores", "TMB-1009")], k=5)
    assert m.recall == 0 and m.acierto_fragmento == 0


def test_agregar() -> None:
    a = medir_pregunta(PREGUNTA, [_r("errores", "TMB-1009")] + [_r("otro")] * 4, k=5)
    b = medir_pregunta(PREGUNTA, [_r("otro")] * 5, k=5)
    total = agregar([a, b], k=5)
    assert total.recall == 0.5 and total.precision == pytest.approx(0.1)
    assert total.mrr == 0.5 and total.acierto_fragmento == 0.5
    assert total.f1 == pytest.approx(2 * 0.1 * 0.5 / 0.6, abs=1e-4)


def test_precision_maxima() -> None:
    assert precision_maxima(PREGUNTA, {"errores": 3}, k=5) == 0.6
    assert precision_maxima(PREGUNTA, {"errores": 14}, k=5) == 1.0


def test_golden_set_rechaza_ids_repetidos_y_pocas_preguntas() -> None:
    p = PREGUNTA.model_dump()
    with pytest.raises(ValidationError, match="mismo id"):
        GoldenSet(descripcion="x", preguntas=[p] * 5)
    with pytest.raises(ValidationError):
        GoldenSet(descripcion="x", preguntas=[p])


def test_golden_set_real_es_valido_y_sus_respuestas_existen() -> None:
    golden = GoldenSet.model_validate_json(
        (RAIZ / "evaluacion" / "golden_set.json").read_text(encoding="utf-8")
    )
    fragmentos = cargar_corpus(ConfigPinecone())
    for p in golden.preguntas:
        del_documento = [
            " ".join(f.texto.split()) for f in fragmentos if f.documento_id == p.documento_id_esperado
        ]
        assert any(p.contiene in t for t in del_documento), p.id
