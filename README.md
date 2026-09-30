# AI Engineering (Coderhouse)

| Entrega | Qué es | Dónde |
|---|---|---|
| Pre-entrega 4 | RAG escalable en Pinecone con búsqueda híbrida (BM25 + vectorial) y evaluación | `rag_pinecone/`, `setup_pinecone.py`, `ingestar_pinecone.py`, `evaluate.py`, `corpus/` |
| Pre-entrega 3 | Sistema de recuperación semántica local (RAG) con ChromaDB | `rag/`, `ingestar.py`, `probar_rag.py`, `data/` |
| Pre-entrega 2 | Pipeline de extracción de entidades técnicas con LangChain y Pydantic | `pipeline/`, `probar_pipeline.py` |
| Pre-entrega 1 | Cliente asíncrono unificado para OpenAI y Anthropic | `llm_client/`, `main.py` |

La instalación y las variables de entorno son las mismas para las cuatro: están en
[Instalación](#instalación) y [Variables de entorno](#variables-de-entorno).

# Pre-entrega 4: RAG escalable en Pinecone con búsqueda híbrida

El RAG de la pre-entrega 3 pasa de ChromaDB local a un índice **Pinecone Serverless**, y la búsqueda
deja de ser solo por similitud: un `RAGSystem` combina Pinecone con BM25 mediante un
`EnsembleRetriever`. Un `evaluate.py` mide Recall@5 y Precision@5 sobre un golden set de 14
preguntas. Todo lo que sigue sale de una corrida real contra Pinecone; las salidas completas están
en `evidencia/`.

## Replicar el índice

Con el entorno instalado (ver [Instalación](#instalación)):

1. Crear una cuenta gratis en [app.pinecone.io](https://app.pinecone.io) y una API key en
   **API Keys**.
2. Completar en el `.env` `PINECONE_API_KEY` e `INDEX_NAME` (ver `.env.example`). El resto tiene
   defaults: namespace `plataforma`, `aws` / `us-east-1`, que es la región del plan gratuito.
3. Crear el índice:

```bash
python setup_pinecone.py
```

Si no existe, lo crea en modo serverless, con dimensión 768 y métrica coseno, y espera a que esté
listo. Si ya existe, comprueba que la dimensión, la métrica y el modelo de embeddings coincidan y no
lo toca. Este paso es opcional: la ingesta lo hace igual.

4. Subir el corpus:

```bash
python ingestar_pinecone.py
```

5. Evaluar:

```bash
python evaluate.py --detalle
```

Para una búsqueda puntual, con la posición que le dio cada recuperador y filtros opcionales:

```bash
python buscar_pinecone.py "¿Qué significa el error TMB-1009?" --categoria producto
```

Desde código:

```python
from rag_pinecone import RAGSystem

sistema = RAGSystem()
for doc in sistema.buscar("¿Cómo se calcula el header X-Tambor-Firma?"):
    print(doc.metadata["fuente"], doc.metadata["seccion"])

# Con filtro de metadatos (sintaxis de Pinecone), aplicado a las dos mitades
sistema.buscar("límites de la API", filtro={"audiencia": {"$eq": "comercios"}})
```

## El corpus

`corpus/` amplía el dataset de la pre-entrega 3 (la empresa ficticia Tambor) con tres formatos:

| Archivo | Formato | Cómo se parte | Fragmentos |
|---|---|---|---|
| `arquitectura.md`, `politica-despliegues.md`, `runbook-incidentes.md`, `seguridad-y-accesos.md` | Markdown | splitter por títulos | 15 |
| `api-cobros.md`, `observabilidad.md` | Markdown | splitter por títulos | 6 |
| `catalogo-errores.json` | JSON | un fragmento por código de error | 14 |
| `sla-y-soporte.pdf` | PDF de 3 páginas | una unidad por página, después el splitter | 3 |

Los dos Markdown nuevos y el JSON tienen lo que le cuesta a una búsqueda por embeddings:
identificadores exactos, como códigos de error (`TMB-2012`), nombres de alertas
(`PgBouncerPoolSaturado`), headers (`X-Tambor-Firma`) y comandos. El PDF permite guardar la página
como metadato. Lo generé con `fpdf2` a partir de un texto, pero la ingesta lo lee con `pypdf` como a
cualquier PDF.

`data/` no se tocó: sigue siendo el corpus de la pre-entrega 3 y su evidencia sigue valiendo.

## Ingesta

`rag_pinecone/carga.py` y `rag_pinecone/ingesta.py`.

**Unidades antes del splitter.** Cada formato se parte primero en pedazos con sentido propio: el
Markdown entero, cada página del PDF, cada registro del JSON. Recién ahí entra el mismo splitter de la
pre-entrega 3: 500 tokens de tiktoken con 60 de solapamiento, cortando primero en los títulos. Un
registro del JSON nunca se pega con el siguiente, porque un vector con dos códigos de error mezclados
no representa bien a ninguno. Del PDF se sacan antes las líneas que se repiten en todas las páginas
(el pie "página N de 3"), comparándolas con los números reemplazados.

**Metadatos con esquema fijo.** Cada archivo tiene su entrada en `corpus/_metadatos.json`
(categoría, audiencia, etiquetas y fecha de actualización). Un archivo sin entrada, o una entrada sin
archivo, frena la ingesta. Cada vector se sube con exactamente estos campos, validados por el modelo
Pydantic `MetadatosFragmento` con `extra="forbid"`:

| Campo | Ejemplo | Para qué |
|---|---|---|
| `chunk_id` | `catalogo-errores#8a72db49124e` | ID del vector: documento + hash del contenido |
| `documento_id`, `fuente`, `tipo` | `sla-y-soporte`, `sla-y-soporte.pdf`, `pdf` | citar la fuente; medir en la evaluación |
| `categoria`, `audiencia`, `etiquetas` | `comercial`, `comercios`, `["sla", ...]` | filtrar (ej. solo documentos para comercios) |
| `actualizado` | `20260701` | filtrar por fecha con `$gte` |
| `pagina` | `2` | solo en PDFs |
| `seccion`, `chunk`, `total_chunks`, `tokens` | `Compromiso de disponibilidad y créditos` | contexto del fragmento |
| `texto` | el fragmento | la búsqueda devuelve el contenido sin consultar otra base |

Dos decisiones que salieron de las limitaciones de Pinecone:

- `actualizado` es un número `AAAAMMDD` y no un string `"2026-07-01"`, porque Pinecone solo compara
  números con `$gt` / `$lt`.
- `pagina` se omite en lo que no es PDF, en vez de ir en `null`, porque Pinecone no acepta `null`
  en los metadatos. El esquema obliga a que esté en todos los PDF y en ningún otro archivo.

Esto es lo que el módulo llama *schema drift*. Si un proceso guarda `fecha` y otro `actualizado`, un
filtro por `actualizado` deja de encontrar la mitad de los documentos sin dar ningún error. Con el
esquema cerrado, ese cambio falla al ingestar.

**Lotes.** Los vectores se suben de a 100 por request (`tamano_lote`). Con 38 fragmentos es un
solo lote, pero el código no cambia con 38.000. Pinecone limita un request a 1.000 vectores o 2 MB.

**Incremental.** Los IDs son deterministas, así que antes de calcular embeddings la ingesta lista los
IDs que ya están en el namespace (`index.list`), sube solo los nuevos y borra los que ya no existen.
Una segunda corrida sin cambios no calcula ningún embedding:

```
ingesta: lote 1/1: 38 vectores (embeddings 14.8 s, upsert 2.17 s)
ingesta: 38 vectores visibles en el namespace
Índice 'tambor-docs', namespace 'plataforma': 8 documentos, 38 fragmentos (38 subidos en 1 lote(s), 0 borrados) en 22.8 s.

# segunda corrida
Índice 'tambor-docs', namespace 'plataforma': 8 documentos, 38 fragmentos (0 subidos en 0 lote(s), 0 borrados) en 5.5 s.
No había cambios: no se calculó ningún embedding ni se subió nada.
```

**Namespace.** Todo va al namespace `plataforma`. `--reiniciar` vacía solo ese namespace, así que en
el mismo índice puede convivir otro corpus, u otro entorno, sin mezclarse. Hay un test que lo
comprueba.

## Infraestructura y el modelo de embeddings

Sigo sin key de OpenAI, así que los embeddings son los locales de la pre-entrega 3
(`jinaai/jina-embeddings-v2-base-es`, con fastembed), que generan vectores de **768** dimensiones y
no los 1.536 de `text-embedding-3-small` que menciona la consigna. Para no escribir ese número a mano,
la dimensión del índice se mide con el modelo: se calcula un embedding y se cuenta su largo.

Los errores de dimensión y de métrica que menciona el módulo se chequean al abrir el índice:

- **Dimensión distinta**: un índice de 1.536 con un modelo de 768 da `IndiceError` antes de subir
  nada, con el nombre de los dos números.
- **Métrica distinta**: lo mismo si el índice existente no es coseno.
- **Mismo tamaño, otro modelo**: es el caso que no da ningún error por sí solo. Si mañana cambio a
  otro modelo de 768 dimensiones, Pinecone acepta los vectores y las búsquedas devuelven ruido. Por
  eso el índice se crea con el tag `embeddings: jinaai_jina-embeddings-v2-base-es` y se valida en
  cada apertura.

La API key se lee del `.env` como `SecretStr`, así que no aparece si se imprime la configuración.
Si falta, `ConfigError` dice dónde crearla.

## Recuperador híbrido: `RAGSystem`

`rag_pinecone/sistema.py`:

```
consulta ─┬─> PineconeVectorStore (coseno, top 10)  ─┐
          └─> BM25Retriever (léxico, top 10)        ─┴─> EnsembleRetriever (RRF, pesos 0,5 / 0,5) -> top 5
```

- **Vectorial**: `PineconeVectorStore` sobre el índice y el namespace de la ingesta, con
  `text_key="texto"`.
- **Léxica**: `BM25Retriever`. BM25 necesita todo el corpus en memoria; se arma leyendo los
  fragmentos del mismo namespace de Pinecone (`list` + `fetch`), así las dos mitades ven siempre el
  mismo corpus y no hay una segunda fuente de verdad.
- **Fusión**: `EnsembleRetriever` con Reciprocal Rank Fusion: cada fragmento suma
  `peso / (60 + posición)` por cada lista en la que aparece. Usa posiciones y no puntajes, porque un
  12,3 de BM25 y un 0,81 de coseno no están en la misma escala. Uso `id_key="chunk_id"` para que un
  mismo fragmento que llega por las dos vías se cuente una vez.

Tres cambios sobre BM25 tal como viene:

1. **Tokenizador propio.** El de LangChain es `texto.split()`, así que `"TMB-1009?"` y `"TMB-1009"`
   son tokens distintos y `"rotación"` no coincide con `"rotacion"`. El mío pasa a minúsculas, saca
   tildes y stopwords, y deja los compuestos enteros y partidos: `TMB-1009` da `tmb-1009`, `tmb` y
   `1009`.
2. **Sin puntajes 0.** `BM25Retriever` devuelve siempre k documentos, aunque no compartan ni una
   palabra con la consulta. En la fusión esos documentos suman puntaje RRF solo por estar en la
   lista, así que los descarto.
3. **El mismo filtro que Pinecone.** Si la parte vectorial filtra por categoría y BM25 no, la fusión
   mete fragmentos de otras categorías. `cumple_filtro` evalúa la sintaxis de filtros de Pinecone
   (`$eq`, `$in`, `$gte`, `$and`, etc.) sobre los metadatos en memoria.

`buscar(consulta, k=5, filtro=None, modo="hibrido")` acepta además `modo="vectorial"` y
`modo="bm25"`, que es lo que usa la evaluación para comparar. `explicar()` devuelve el top 5 con la
posición que le dio cada recuperador. Así se ve con un filtro, en `evidencia/salida_buscar_pinecone.txt`:

```
Consulta: ¿Cuánto tiempo tengo para reclamar?
Filtro: {'$and': [{'audiencia': {'$eq': 'comercios'}}, {'actualizado': {'$gte': 20260701}}]}

1. sla-y-soporte.pdf, página 1 — SLA y soporte para comercios  [RRF 0.0161 | Pinecone #3 · BM25 #1]
2. sla-y-soporte.pdf, página 3 — Exclusiones, mantenimiento y escalamiento  [RRF 0.0082 | Pinecone #1 · BM25 #-]
3. catalogo-errores.json — TMB-2012  [RRF 0.0081 | Pinecone #2 · BM25 #-]
...
```

## Evaluación

`evaluacion/golden_set.json` tiene 14 preguntas con el formato que pide la consigna
(`pregunta`, `documento_id_esperado`) y dos campos más:

- `contiene`: un texto que aparece en el fragmento exacto que responde. `evaluate.py` comprueba antes
  de medir que ese texto esté en el documento esperado, y un test hace lo mismo.
- `tipo`: `lexica` (6 preguntas que nombran un código, un header, una alerta o un comando) o
  `semantica` (8 que usan otras palabras que el documento, como "¿cuánto me descuentan si la
  plataforma estuvo caída?" para la tabla de créditos del SLA).

Métricas, con k = 5:

- **Recall@5**: la pregunta suma 1 si al menos un fragmento del top 5 es del documento esperado.
- **Precision@5**: fracción de los 5 que son del documento esperado. Se divide por 5 aunque vengan
  menos.
- **F1**, **MRR** (1 / posición del primer acierto) y **acierto de fragmento@5** (si vino el
  fragmento que contiene la respuesta, no solo el documento).

### Resultados

`python evaluate.py --detalle`, contra Pinecone (salida completa en `evidencia/salida_evaluate.txt`,
JSON en `evidencia/evaluacion_pinecone.json`):

| Modo | Recall@5 | Precision@5 | F1 | MRR | Fragmento@5 | ms / consulta |
|---|---|---|---|---|---|---|
| Híbrido (0,5 / 0,5) | **100 %** | 47,1 % | 0,641 | 0,845 | 92,9 % | 336 |
| Solo vectorial (Pinecone) | **100 %** | **51,4 %** | **0,679** | 0,845 | **100 %** | 249 |
| Solo BM25 | 92,9 % | 40,0 % | 0,559 | 0,839 | 85,7 % | 1 |

Por tipo de pregunta (Recall@5 / MRR):

| Tipo | Híbrido | Vectorial | BM25 |
|---|---|---|---|
| Léxicas (6) | 100 % / **0,92** | 100 % / 0,83 | 100 % / **1,00** |
| Semánticas (8) | 100 % / 0,79 | 100 % / **0,85** | 87,5 % / 0,72 |

La Precision@5 máxima posible con este corpus es 71,4 %: cinco de los ocho documentos tienen 3
fragmentos, y con k = 5 una pregunta sobre ellos no puede pasar de 0,6.

## Lo que encontré

**El híbrido no le ganó al vectorial en este corpus.** Esperaba lo contrario y no toqué el golden set
para que cambiara. Los dos llegan a Recall@5 de 100 %, pero el vectorial solo tiene mejor precisión y
trae siempre el fragmento exacto. Mirando pregunta por pregunta, el reparto es claro:

- En las **léxicas** BM25 es imbatible (MRR 1,00: siempre pone el documento correcto primero) y el
  híbrido mejora al vectorial (0,92 contra 0,83). Es lo que promete la búsqueda híbrida.
- En las **semánticas** BM25 mete ruido. En "¿Qué pasa con una funcionalidad nueva que queda activada
  para todos durante meses?", BM25 pone primero la sección *Ventanas de despliegue*, por palabras
  sueltas que comparte con la pregunta, y la fusión la sube al primer puesto. La sección que responde,
  *Feature flags*, la trae Pinecone, pero termina afuera del top 5.

Con un corpus de 38 fragmentos y un modelo de embeddings bueno, la parte vectorial ya resuelve casi
todo, y los códigos exactos son pocos. En un corpus grande, con cientos de códigos de error parecidos
entre sí, la balanza debería inclinarse al revés. Hoy, si tuviera que elegir por las métricas, usaría
el vectorial solo; el híbrido lo justifican las consultas por identificador exacto.

**Con pesos iguales, RRF empata #1 con #2.** En "Recibí un TMB-2012, ¿tengo que crear el cobro de
nuevo?", BM25 pone el registro de `TMB-2012` primero y Pinecone lo pone segundo, detrás de la tabla
de endpoints (la pregunta dice "crear el cobro"). Con pesos 0,5 / 0,5, los dos fragmentos suman
exactamente lo mismo (1/61 + 1/62), y el empate lo gana el que aparece primero en la lista del
primer recuperador. Probé otros pesos contra el mismo índice (`--peso-vectorial`): con 0,7 para el vectorial
el MRR sube a 0,881 pero la precisión baja a 44,3 %; con 0,3, el Recall@5 cae a 92,9 %. Dejé 0,5, porque
ajustar el peso con 14 preguntas sería ajustarlo a estas 14 preguntas.

**`BM25Retriever` devuelve documentos que no tienen nada que ver.** Sin el cambio que descarta los
puntajes 0, una consulta como "zzz" devuelve igual 10 fragmentos, y en la fusión cada uno suma algo.

**Pinecone es eventualmente consistente.** Un vector recién subido tarda unos segundos en aparecer
en `list` y en las búsquedas. Si la evaluación corre justo después de la ingesta, puede medir un
índice a medio llenar sin dar ningún error. La ingesta espera a que `describe_index_stats` muestre la
cantidad esperada antes de terminar.

**El tag del índice no es un `dict`.** `describe_index().tags` devuelve un modelo propio del SDK; con
`dict(...)` falla. Lo encontré en la primera corrida contra Pinecone, porque el índice falso de los
tests devolvía un `dict`.

**Límite de escala conocido.** BM25 vive en memoria y se arma leyendo todo el namespace al crear el
`RAGSystem`. Con 38 fragmentos es instantáneo; con millones no. A esa escala la parte léxica tendría
que ser un índice híbrido de Pinecone (vectores densos más *sparse*) o un motor aparte, como
Elasticsearch.

## Tests

```bash
python -m pytest -q tests/test_rag_pinecone.py
```

Son 62 tests sin API keys y sin red. Pinecone es un índice falso en memoria, con la misma interfaz
que usa el código (`upsert`, `query`, `list`, `fetch`, `delete`, `describe_index_stats`), similitud
coseno y los filtros de Pinecone. `PineconeVectorStore`, `BM25Retriever` y `EnsembleRetriever` son
los reales. Cubren:

- configuración: límites, key faltante, que la key no aparezca en el `repr`;
- catálogo: archivo sin metadatos, metadatos sin archivo, categoría inválida, campo extra;
- carga: secciones del Markdown, un fragmento por registro del JSON, página y pie del PDF, IDs
  deterministas, que `pagina` no vaya como `null`;
- el esquema de metadatos (schema drift, página en un Markdown, PDF sin página, ID de otro
  documento);
- tokenizador y filtros;
- infraestructura: crea el índice serverless con tags, y falla con otra dimensión, otra métrica u
  otro modelo;
- ingesta: lotes, texto en los metadatos, idempotencia, reindexado parcial, reinicio sin tocar
  otros namespaces;
- `RAGSystem`: índice inexistente o vacío, top k sin repetidos, BM25 con el código exacto, fusión,
  filtros en las dos mitades, filtro por página, versión asíncrona;
- métricas, y que el golden set real sea válido y sus respuestas existan en el corpus.

Con los de las pre-entregas anteriores son 170:

```bash
python -m pytest -q
```

## Archivos

```
corpus/                     8 documentos (.md, .json, .pdf) y _metadatos.json
rag_pinecone/
  config.py                 ConfigPinecone: key, índice, namespace, chunking, pesos de la fusión
  schemas.py                MetadatosDocumento, MetadatosFragmento (esquema fijo), ResumenIngesta
  carga.py                  catálogo, lectores de Markdown / PDF / JSON y fragmentación
  infra.py                  crear y validar el índice serverless
  ingesta.py                embeddings y upsert por lotes, incremental, por namespace
  sistema.py                RAGSystem: Pinecone + BM25 + EnsembleRetriever, filtros
  metricas.py               golden set, Recall@k, Precision@k, F1, MRR
setup_pinecone.py           inicialización del índice
ingestar_pinecone.py        ingesta
evaluate.py                 evaluación: híbrido vs. vectorial vs. BM25
buscar_pinecone.py          búsqueda puntual con explicación y filtros
evaluacion/golden_set.json  14 preguntas con su documento esperado
evidencia/                  salidas de la corrida contra Pinecone
tests/test_rag_pinecone.py  62 tests sin red
```

# Pre-entrega 3: sistema RAG local con ChromaDB

Le hacés una pregunta sobre la documentación interna de una empresa y te responde usando solo lo
que dicen los documentos, con las fuentes de donde sacó la respuesta. Si la respuesta no está en los
documentos, dice "No lo sé.", aunque el modelo la sepa de memoria.

Los documentos se fragmentan, se convierten en embeddings y se guardan en ChromaDB, en disco. La
consulta busca los fragmentos más parecidos a la pregunta y se los pasa al LLM en una cadena LCEL. La
salida pasa por un `PydanticOutputParser` y se valida contra lo que se recuperó.

Usa la configuración de las pre-entregas anteriores: el mismo `.env`, las mismas keys y el mismo
`LLM_PROVIDER`. Alcanza con una sola key, de OpenAI o de Anthropic.

## Correrlo

Con el entorno instalado y el `.env` completo, primero se indexan los documentos de `data/`:

```bash
python ingestar.py
```

La primera vez descarga el modelo de embeddings (unos 640 MB, un par de minutos) a `.cache/`. Las
siguientes lo carga de disco en unos 3 segundos. Si la colección ya está al día, `ingestar.py` no
recalcula nada y termina en medio segundo.

Después, las pruebas de punta a punta:

```bash
python probar_rag.py
```

Corre la ingesta (que no hace nada si ya está al día), dos preguntas cuya respuesta está en los
documentos, dos preguntas trampa, y compara las cuatro consultas en serie contra las cuatro en
paralelo. Deja los resultados en `evidencia/pruebas_rag.json`.

Para hacer una pregunta propia:

```bash
python probar_rag.py --pregunta "¿Cada cuánto se rotan los secretos?"
```

Para reconstruir la colección de cero (por ejemplo, después de cambiar el tamaño de chunk):

```bash
python ingestar.py --reiniciar
```

Desde código:

```python
import asyncio
from rag import get_rag_response

async def main():
    respuesta = await get_rag_response("¿Puedo desplegar a producción un viernes a las 16:00?")
    print(respuesta.model_dump_json(indent=2))

asyncio.run(main())
```

## El dataset

`data/` tiene cuatro documentos Markdown de un equipo de Plataforma: arquitectura, runbook de
incidentes, política de despliegues y seguridad y accesos. Suman unos 5.000 tokens.

La empresa, Tambor, es inventada, y a propósito. Si los documentos fueran sobre algo público, como la
documentación de PostgreSQL, no habría forma de saber si una respuesta correcta salió del contexto o
de lo que el modelo ya sabía. Con datos inventados (el pool de PgBouncer es de 40 conexiones, los
viernes se despliega hasta las 15:00) una respuesta correcta solo puede venir de los documentos.

## Ingesta

`rag/ingesta.py`, en cuatro pasos:

1. **Carga**: lee los `.md` y `.txt` de `data/`. El resto de los archivos se ignora.
2. **Limpieza**: normaliza Unicode a NFC (una "á" puede venir como un carácter o como "a" más la
   tilde, y el tokenizador las trata distinto), saca caracteres de control, espacios al final de
   línea y saltos de línea de más.
3. **Fragmentación**: `RecursiveCharacterTextSplitter.from_tiktoken_encoder` con 500 tokens por
   fragmento y 50 de solapamiento, medidos con tiktoken (`cl100k_base`), no en caracteres. Los
   separadores son los de Markdown, así que primero intenta cortar en los títulos, después en los
   párrafos, las líneas y las palabras. Con los documentos de `data/` salen 15 fragmentos de entre
   158 y 478 tokens, y cada uno empieza en un título.
4. **Persistencia**: los guarda en una colección de ChromaDB en `./vectorstore`, con distancia
   coseno. Cada fragmento lleva como metadatos el archivo, la sección (el último título antes del
   fragmento), su número y cuántos tokens tiene. Con eso la respuesta puede citar la fuente.

### No reindexar lo que no cambió

El ID de cada fragmento es un hash del archivo y del texto. El mismo documento produce siempre los
mismos IDs, así que antes de calcular embeddings la ingesta compara los IDs de `data/` con los que ya
están en la colección:

- si son los mismos, no hace nada;
- si un archivo cambió, indexa solo los fragmentos nuevos y borra los viejos de ese archivo;
- si se borró un archivo, borra sus fragmentos.

Es lo que pide la consigna: no volver a indexar todo en cada corrida. Lo comprobé al cambiar la
configuración del splitter: la ingesta borró 12 fragmentos, indexó 13 y dejó 2 que no habían
cambiado.

### El mismo modelo para indexar y para consultar

La colección guarda en sus metadatos el nombre del modelo de embeddings con que se creó. Al abrirla,
si el modelo configurado es otro, lanza `BaseVectorialError`. Comparar vectores de dos modelos
distintos no da error por sí solo: devuelve resultados que parecen normales y son ruido. Prefiero que
falle.

### Embeddings locales

Anthropic no tiene API de embeddings, y yo tengo solo la key de Anthropic. Los embeddings se calculan
en la máquina con [fastembed](https://github.com/qdrant/fastembed), que corre el modelo en ONNX, sin
PyTorch ni GPU.

El modelo es `jinaai/jina-embeddings-v2-base-es`, bilingüe español-inglés. Lo elegí porque acepta
hasta 8.192 tokens de entrada. El que usa ChromaDB por defecto, `all-MiniLM-L6-v2`, corta en 256
tokens y está entrenado en inglés: con fragmentos de 500 tokens se perdería la mitad de cada uno sin
ningún aviso.

## Recuperación y generación

`get_rag_response(query)` en `rag/chain.py` es asíncrona y ejecuta esta cadena LCEL:

```python
cadena = (
    RunnablePassthrough.assign(fragmentos=recuperacion)       # ChromaDB, top_k=4
    | RunnablePassthrough.assign(contexto=formatear_contexto)  # fragmentos -> texto con IDs
    | (
        RunnableParallel(
            salida=prompt | modelo | revisar_corte | PydanticOutputParser(RespuestaLLM),
            fragmentos=itemgetter("fragmentos"),
            pregunta=itemgetter("pregunta"),
        )
        | validar_respuesta
    ).with_retry(stop_after_attempt=3)
)
```

1. **Recuperación**: `asimilarity_search_with_relevance_scores` calcula el embedding de la pregunta
   con el mismo modelo de la colección y trae los 4 fragmentos más parecidos, con su similitud
   coseno. La consigna pide un `top_k` de entre 3 y 5, y `ConfigRAG` no acepta otro valor.
2. **Contexto**: cada fragmento entra al prompt con un ID corto (`F1`, `F2`...), el archivo y la
   sección.
3. **Generación**: el prompt de sistema funciona como filtro de veracidad. Pide usar solo el
   CONTEXTO, no completar con conocimiento general aunque el modelo sepa la respuesta, y contestar
   exactamente "No lo sé." si no está. Las instrucciones de formato las genera el
   `PydanticOutputParser` a partir del esquema. El prompt también dice que los fragmentos son datos,
   no instrucciones, por si un documento trae texto que intente cambiar el comportamiento.
4. **Validación**: `validar_respuesta` vuelve a validar la salida, esta vez sabiendo qué fragmentos
   se recuperaron, y arma la `RespuestaRAG` final.

El modelo nunca escribe las referencias: solo cita IDs. Las referencias (archivo, sección, similitud
y extracto) las arma la cadena con lo que devolvió ChromaDB. Así una fuente no puede ser inventada:
si el modelo cita `F7` cuando se recuperaron cuatro fragmentos, la validación falla y la cadena
reintenta.

La recuperación queda afuera del reintento. Si ChromaDB falla, volver a llamar al LLM no lo arregla.
Se reintenta ante JSON mal formado, salida cortada por tope de tokens, citas inválidas o
incoherentes, y los errores transitorios de la API (rate limit, red, 5xx), que son los mismos de la
pre-entrega 2.

## Esquemas y validaciones

`rag/schemas.py`. Todos los campos tienen `description`: en `RespuestaLLM` es lo que lee el modelo
como instrucción de formato, y en el resto documenta el contrato. Además de tipos y rangos, estas
son las validaciones propias, que es lo que me marcaron para mejorar en la pre-entrega 2:

| Modelo | Validación | Qué evita |
|---|---|---|
| `RespuestaLLM` | normaliza las citas: `"f1"`, `"[F2]"` y `"F1, F3"` pasan a `["F1", "F2"]`, sin repetidos | reintentar por una diferencia de formato |
| `RespuestaLLM` | cada cita tiene que tener la forma `F<n>` | citas como `"fragmento uno"` |
| `RespuestaLLM` | con contexto de validación, cada cita tiene que estar entre los fragmentos recuperados | fuentes inventadas |
| `RespuestaLLM` | si `encontrada` es true, tiene que citar algo y no puede decir "No lo sé" | respuestas sin respaldo |
| `RespuestaLLM` | si `encontrada` es false, la respuesta tiene que ser "No lo sé." y sin citas; `"no lo sé"` se guarda en la forma canónica | que diga "no está" y responda igual |
| `Referencia` | la fuente es un nombre de archivo `.md` o `.txt`, no una ruta | filtrar rutas de la máquina en la respuesta |
| `FragmentoRecuperado` | la similitud tiene que estar entre 0 y 1; tolera errores de redondeo como `1.0000001` | puntajes de una distancia mal configurada |
| `Metricas` | el tiempo total no puede ser menor que recuperación más generación | un cronómetro mal puesto |
| `RespuestaRAG` | `encontrada` y las referencias tienen que ser coherentes; sin referencias repetidas | una salida final contradictoria |
| `ConfigRAG` | `chunk_size` de al menos 500, `chunk_overlap` de al menos 50 y menor que la mitad del chunk, `top_k` entre 3 y 5 | salirse de la consigna por un cambio de configuración |

La validación de citas contra lo recuperado usa el contexto de validación de Pydantic
(`model_validate(..., context={"ids_recuperados": ...})`). El parser no sabe qué fragmentos se
recuperaron, así que esa comprobación se hace en un segundo paso, dentro del reintento.

Salida real de una pregunta respondida con `claude-haiku-4-5`:

```json
{
  "pregunta": "¿Puedo desplegar a producción un viernes a las 16:00?",
  "respuesta": "No, no puedes desplegar a producción un viernes a las 16:00. Los viernes se puede desplegar entre las 10:00 y las 15:00 (hora de Buenos Aires). A las 16:00 ya está fuera de la ventana de despliegue.",
  "encontrada": true,
  "referencias": [
    {
      "id_fragmento": "F1",
      "fuente": "politica-despliegues.md",
      "seccion": "Ventanas de despliegue",
      "similitud": 0.4696,
      "extracto": "## Ventanas de despliegue Se puede desplegar a producción de lunes a jueves entre las 10:00 y las 17:00, y los viernes entre las 10:00 y las 15:00 (hora de Buenos Aires). Fuera de esos horarios no se despliega, porque hay menos gente disponible para responder si algo sale mal. Tampoco se desplieg..."
    }
  ],
  "metricas": {
    "recuperacion_ms": 139.5,
    "generacion_ms": 1681.3,
    "total_ms": 1840.3,
    "intentos_llm": 1,
    "fragmentos_recuperados": 4
  }
}
```

## Logs y tiempos

La otra corrección de la pre-entrega 2 fue loguear de forma explícita la validación y los tiempos del
flujo asíncrono. Un callback de LangChain, `ObservadorRAG`, se hereda a toda la cadena y mide cada
etapa por separado. Cada consulta deja esto:

```
INFO    | rag.chain | consulta: '¿Puedo desplegar a producción un viernes a las 16:00?'
INFO    | rag.chain | recuperación: 4 fragmento(s): F1=politica-despliegues.md (0.47), F2=politica-despliegues.md (0.44), F3=politica-despliegues.md (0.35), F4=arquitectura.md (0.30)
INFO    | rag.chain | recuperación terminada en 139 ms
INFO    | rag.chain | intento 1: llamando al modelo
INFO    | rag.chain | intento 1: respuesta del modelo en 1681 ms (fin: end_turn, tokens: 2442 entrada / 109 salida)
INFO    | rag.chain | validación OK: encontrada=True, 1 referencia(s) (F1)
INFO    | rag.chain | consulta resuelta en 1840 ms (recuperación 140 ms, LLM 1681 ms, 1 intento(s)): respuesta encontrada
```

Cuando una salida no valida, el log dice en qué paso se rechazó y por qué, y el intento siguiente
aparece abajo:

```
WARNING | rag.chain | intento 1: salida rechazada en validar_respuesta: fragmentos_citados: Value error, el modelo citó fragmentos que no estaban en el contexto: ['F9'] (recuperados: ['F1', 'F2'])
INFO    | rag.chain | intento 2: llamando al modelo
```

Los mismos tiempos vuelven en el campo `metricas` de la respuesta, así que también se pueden usar
desde el código y no solo leer en el log.

## Pruebas

Resultado de `python probar_rag.py` con `claude-haiku-4-5` (la salida completa está en
`evidencia/salida_probar_rag.txt` y el JSON en `evidencia/pruebas_rag.json`):

| Pregunta | Esperado | Resultado |
|---|---|---|
| ¿Qué hay que hacer si la cola de pagos de RabbitMQ crece sin parar? | Los pasos del runbook, citando `runbook-incidentes.md` | Pasa: los 5 pasos, con el comando `kubectl` y los umbrales |
| ¿Puedo desplegar a producción un viernes a las 16:00? | "No", citando `politica-despliegues.md` | Pasa: "No [...] los viernes entre las 10:00 y las 15:00" |
| ¿Qué CDN usa Tambor para servir el panel de comercios? | "No lo sé." | Pasa |
| ¿En qué año se publicó la primera versión de PostgreSQL? | "No lo sé." | Pasa |

Las dos preguntas trampa están elegidas para que cuesten. La del CDN es del mismo tema que los
documentos: la búsqueda trae fragmentos de arquitectura con similitud alta y es tentador armar una
respuesta con ellos. La de PostgreSQL el modelo la sabe de memoria, y PostgreSQL aparece en los
documentos. En las dos respondió "No lo sé." sin referencias.

Las cuatro consultas en serie tardaron 9,5 s y en paralelo con `asyncio.gather` 4,7 s. Mientras una
consulta espera al LLM, el event loop atiende las otras, y la búsqueda en ChromaDB corre en un thread
aparte, así que tampoco lo bloquea.

## Lo que encontré

**Un umbral de similitud no alcanza para filtrar lo que no está.** Mi primera idea fue descartar los
fragmentos con similitud baja y responder "No lo sé" sin llamar al LLM. Los números no dan: la
pregunta del CDN, que no está en los documentos, trae un fragmento con similitud 0,59, y la de los
viernes, que sí está, trae su mejor fragmento con 0,47. Cualquier umbral que deje pasar la segunda deja
pasar la primera. La similitud dice que un fragmento es del mismo tema, no que responde la pregunta.
Eso lo tiene que decidir el modelo, con el prompt y el esquema obligándolo a decir que no sabe.

**El splitter de Markdown no cortaba en los títulos.** Los separadores que devuelve
`get_separators_for_language(Language.MARKDOWN)` son expresiones regulares (`"\n#{1,6} "`), pero el
splitter los busca como texto literal si no se le pasa `is_separator_regex=True`. No da error:
simplemente nunca corta en un título. Me di cuenta porque algunos fragmentos empezaban en la mitad de
una sección.

**`add_start_index` falla con chunks medidos en tokens.** Para saber a qué sección pertenece cada
fragmento necesitaba su posición en el documento. LangChain la calcula restando el solapamiento
como si fueran caracteres, pero son 50 tokens, unos 200 caracteres, así que busca desde después
del inicio real y devuelve -1. La posición la calculo aparte, buscando cada fragmento a partir del
anterior.

**La primera consulta tarda más.** En la primera pregunta la recuperación tarda unos 5 segundos,
porque carga el modelo de embeddings en memoria. Las siguientes tardan entre 100 y 200 ms.

## Seguridad de las credenciales

- Las keys van en `.env`, que está en `.gitignore`. En el repo solo está `.env.example`, vacío.
- Dentro del programa las keys son `pydantic.SecretStr`, así que no aparecen si se imprime la
  configuración.
- `vectorstore/` y `.cache/` tampoco se suben: se generan con `python ingestar.py`.

## Tests

```bash
python -m pytest -q tests/test_rag.py
```

Son 47 tests y no usan API keys ni descargan el modelo. Los embeddings son una bolsa de palabras con
hashing (deterministas, y los textos con palabras en común quedan cerca), el LLM es un modelo falso
que devuelve respuestas de un guion y ChromaDB es real, en una carpeta temporal. Cubren:

- configuración: los límites de la consigna;
- limpieza, carga y fragmentación: tope de tokens, secciones, IDs deterministas;
- persistencia: ingesta idempotente, reindexado de un solo archivo, reinicio, colección inexistente
  y modelo de embeddings distinto;
- cada validación de los esquemas;
- la cadena: respuesta con referencias reales, pregunta trampa, contenido del prompt, reintento ante
  texto en vez de JSON, JSON cortado, cita inventada, respuesta sin citas, corte por tope de tokens y
  error de red, error permanente sin reintento, reintentos agotados y consultas en paralelo;
- que el log incluya la validación y los tiempos.

Con los de las pre-entregas anteriores son 108:

```bash
python -m pytest -q
```

## Archivos

```
data/                       dataset: 4 documentos .md de una empresa ficticia
rag/
  config.py                 ConfigRAG: carpetas, colección, modelo, chunking y top_k
  embeddings.py             EmbeddingsLocales: fastembed con la interfaz de LangChain
  ingesta.py                limpieza, chunking, ChromaDB persistente, ingesta incremental
  schemas.py                RespuestaLLM, RespuestaRAG, Referencia, Metricas (Pydantic)
  prompts.py                prompt de grounding y formato del contexto
  chain.py                  cadena LCEL, reintento, ObservadorRAG y get_rag_response()
ingestar.py                 script de ingesta
probar_rag.py               pruebas de punta a punta (asíncrono)
evidencia/                  salida de la última corrida de probar_rag.py
tests/
  test_rag.py               47 tests sin API keys
```

# Pre-entrega 2: pipeline de extracción de entidades técnicas

Le pasás un párrafo técnico, como una descripción de arquitectura o un log de error, y te devuelve
un objeto validado con las tecnologías que menciona, qué tan crítico es y un resumen. Está armado
con LangChain (LCEL) y Pydantic, y usa la configuración de la pre-entrega 1: el mismo `.env`, las
mismas keys y el mismo `LLM_PROVIDER`.

## Correrlo

Con el entorno instalado y el `.env` completo:

```bash
python probar_pipeline.py
```

```bash
python probar_pipeline.py --provider anthropic
```

```bash
python probar_pipeline.py --texto "El worker de Celery no llega a Redis y la cola no para de crecer"
```

El script analiza tres textos (una arquitectura, un log de error y una propuesta de mejora) y
después hace dos pruebas de estrés: un texto ambiguo sin nombres de tecnologías y una llamada con un
tope de 30 tokens, que obliga al modelo a cortar la respuesta a la mitad.

Desde código:

```python
import asyncio
from pipeline import process_text

async def main():
    entidades = await process_text(
        "La API en FastAPI tarda 4 segundos porque se agota el pool de PostgreSQL."
    )
    print(entidades.model_dump_json(indent=2))

asyncio.run(main())
```

## Ejemplo de salida

Entrada:

```
2026-09-14T03:12:44Z ERROR [payments-worker] celery.task.process_payment failed:
psycopg.OperationalError: connection to server at 'db-prod.internal' (10.0.3.12), port 5432
failed: FATAL: remaining connection slots are reserved. Retries exhausted (5/5). RabbitMQ queue
'payments' depth=1243 and growing.
```

Salida real con `claude-haiku-4-5`:

```json
{
  "tecnologias": [
    "Celery",
    "PostgreSQL",
    "RabbitMQ",
    "psycopg"
  ],
  "nivel_de_criticidad": "alta",
  "resumen_tecnico": "El worker de pagos basado en Celery no puede conectarse a PostgreSQL porque se agotaron los slots de conexión disponibles, causando fallos en el procesamiento y acumulación de mensajes en la cola RabbitMQ sin capacidad de recuperación."
}
```

## La cadena

Está en `pipeline/chain.py`:

```python
extraccion = modelo.with_structured_output(
    EntidadesTecnicas, method="function_calling", include_raw=True
) | RunnableLambda(_validar_salida)

cadena = crear_prompt() | extraccion.with_retry(
    retry_if_exception_type=ERRORES_REINTENTABLES,
    stop_after_attempt=3,
)
```

1. `crear_prompt()` es un `ChatPromptTemplate` con un mensaje de sistema y uno humano. Las
   instrucciones de formato entran como variable (`{instrucciones_de_formato}`, fijada con
   `.partial()`) y el texto como `{texto}`. No hay f-strings.
2. `with_structured_output` le pasa el esquema al modelo como herramienta obligatoria y parsea lo
   que vuelve a un `EntidadesTecnicas`. Uso `method="function_calling"` en los dos proveedores para
   que se comporten igual.
3. `_validar_salida` decide si la salida se acepta. Si no, lanza una excepción.
4. `.with_retry()` vuelve a correr los pasos 2 y 3. El prompt queda afuera: si falla al armarse es un
   error de programación, y repetirlo no lo arregla.

`process_text(text)` ejecuta la cadena con `.ainvoke()`, loguea cada intento y devuelve el objeto
validado. Si se agotan los reintentos lanza `ExtraccionError`, que dice cuántos intentos hubo y
guarda la causa original en `__cause__`.

## El esquema

`pipeline/schemas.py`:

| Campo | Tipo | Restricciones |
|---|---|---|
| `tecnologias` | `list[str]` | entre 1 y 30; se recortan espacios y se sacan repetidos sin distinguir mayúsculas |
| `nivel_de_criticidad` | `NivelCriticidad` | enum `baja` / `media` / `alta`; acepta `"Alta"` y lo normaliza |
| `resumen_tecnico` | `str` | entre 20 y 400 caracteres |

Tiene `extra="forbid"`, así que un campo inventado por el modelo también es un error. Cada campo
lleva una `description` que explica qué va ahí, porque LangChain la manda al modelo como parte del
esquema. La descripción de `nivel_de_criticidad` define el criterio de cada nivel.

## Resiliencia

Se reintenta ante:

- `ValidationError` y `OutputParserException`: JSON que no respeta el esquema, un campo que falta,
  un valor fuera del enum, o un modelo que contestó texto en vez de llenar la estructura.
- `SalidaIncompletaError`: el proveedor avisó que cortó por tope de tokens.
- Rate limit, red, timeout y errores 5xx de los dos SDKs. En Anthropic eso incluye
  `OverloadedError` (529) y `ServiceUnavailableError`, que no heredan de `InternalServerError` y se
  escapaban si solo miraba esa clase.

No se reintenta ante 401, 404, 400 ni errores de programación: salen al primer intento.

Lo del tope de tokens es lo que advierte la consigna. Por eso la cadena usa `include_raw=True`:
además del objeto parseado, devuelve el mensaje crudo con su `stop_reason` (Anthropic) o
`finish_reason` (OpenAI). `_validar_salida` mira eso antes que nada. Si dice `max_tokens` o
`length`, descarta la salida aunque haya validado, porque un objeto a medias puede pasar la
validación de casualidad.

Como en la pre-entrega 1, los SDKs se crean con `max_retries=0`. La política de reintentos vive en
un solo lugar, el `.with_retry()`.

## Logs

`.with_retry()` no avisa cuando reintenta. Para ver cada intento hay un callback de LangChain,
`ObservadorDeExtraccion`, que se pasa en la config de `.ainvoke()` y se hereda a toda la cadena.
Cuenta cada llamada al modelo y loguea con qué motivo terminó y cuántos tokens usó. La validación
loguea por su lado si aceptó o por qué rechazó.

La prueba del tope de 30 tokens se ve así:

```
INFO    | pipeline.chain | procesando texto de 303 caracteres
INFO    | pipeline.chain | intento 1: llamando al modelo
INFO    | pipeline.chain | intento 1: respuesta recibida (fin: max_tokens, tokens: 1303 entrada / 30 salida)
WARNING | pipeline.chain | validación: respuesta cortada por tope de tokens (max_tokens), se descarta
INFO    | pipeline.chain | intento 2: llamando al modelo
INFO    | pipeline.chain | intento 2: respuesta recibida (fin: max_tokens, tokens: 1303 entrada / 30 salida)
WARNING | pipeline.chain | validación: respuesta cortada por tope de tokens (max_tokens), se descarta
ERROR   | pipeline.chain | extracción fallida tras 2 intento(s): SalidaIncompletaError: la respuesta se cortó por tope de tokens (max_tokens)
```

Reintentar con el mismo tope no lo arregla, y está bien que no lo haga. El reintento está pensado
para fallas de formato ocasionales. Lo que muestra esta prueba es que un objeto cortado nunca llega
a quien llama: termina en un error controlado.

## Lo que encontré en la prueba de estrés

Con el texto ambiguo ("anda lento, puede ser la base o el deploy del viernes") esperaba que la
validación fallara, porque no nombra ninguna tecnología y el esquema exige al menos una. No falla.
El modelo llena el campo con lo que puede: en una corrida devolvió `["Base de datos", "Deployment"]`
y en la siguiente `["<UNKNOWN>"]`. Las dos salidas pasan la validación.

Es esperable: la lista no está vacía, y Pydantic no tiene forma de saber que "Base de datos" es
genérico y "PostgreSQL" no. Si esto importara en producción, habría que validar contra un catálogo
de tecnologías conocidas, o agregar un paso que verifique que cada nombre aparece textualmente en el
texto de entrada. Lo dejo anotado en vez de taparlo. El esquema controla la forma de la salida; que
los nombres sean ciertos es otro problema.

También muestra por qué conviene fijar `temperature` en 0 donde el proveedor lo permite (en OpenAI
está en 0; Anthropic ya no acepta el parámetro). Con un texto sin señal clara, dos corridas
idénticas no devuelven lo mismo.

## Tests

```bash
python -m pytest -q tests/test_pipeline.py
```

Son 24 tests y no usan API keys: el LLM es un chat model falso que devuelve respuestas de un guion.
Cubren las restricciones del esquema, que el prompt solo espere `{texto}`, el camino feliz, el
reintento ante criticidad inválida, campo faltante, texto en vez de estructura, respuesta cortada
por `stop_reason` y por `finish_reason`, error de red transitorio, que un error permanente no se
reintente, que se agoten los intentos, y `abatch` con varios textos.

## Archivos

```
pipeline/
  schemas.py          EntidadesTecnicas y NivelCriticidad (Pydantic)
  prompts.py          ChatPromptTemplate con instrucciones de formato
  chain.py            modelo, cadena LCEL, validación, reintento, logs y process_text()
probar_pipeline.py    mini-script de prueba asíncrono
tests/
  test_pipeline.py    24 tests con modelo falso
```

# Pre-entrega 1: Unified Async LLM Client

Un cliente asíncrono que habla con OpenAI y con Anthropic usando la misma interfaz. Python 3.12,
streaming de tokens, validación con Pydantic y errores que no tiran abajo el programa.

## El problema

Si instanciás el SDK de OpenAI directamente en tu lógica, el día que quieras probar Claude tenés
que reescribir todo. Los dos SDKs no se parecen: en OpenAI el texto sale de
`choices[0].message.content`, en Anthropic viene como una lista de bloques que hay que filtrar. El
system prompt en uno es un mensaje más y en el otro es un parámetro aparte. Y así.

La solución es una interfaz común arriba de los dos, y que las diferencias queden encerradas en
cada cliente concreto.

Estas son las que hubo que absorber:

| | OpenAI | Anthropic |
|---|---|---|
| System prompt | un mensaje con `role="system"` | parámetro `system=` |
| Texto de la respuesta | `choices[0].message.content` | lista de bloques, filtrar los `text` |
| Tope de tokens | `max_completion_tokens`, opcional | `max_tokens`, obligatorio |
| `temperature` / `top_p` | 0 a 2 | los modelos actuales ya no los aceptan |
| Tokens usados | `prompt_tokens` / `completion_tokens` | `input_tokens` / `output_tokens` |

Lo de `temperature` en Anthropic me sorprendió: el SDK 1.x ni siquiera expone el parámetro. Si se
lo mandás, la API devuelve 400. Así que `ModelConfig` lo valida de 0 a 2 igual, porque es lo que
pide la consigna, y el cliente de Anthropic lo descarta y lo anota en el log de debug.

## Instalación

Hace falta Python 3.12 o más nuevo, porque uso `asyncio.timeout`, `StrEnum` y la sintaxis
`X | None`.

```bash
git clone https://github.com/ocampomatias/ai-engineering.git
```

```bash
cd ai-engineering
```

```bash
python -m venv .venv
```

Activar el entorno en Windows:

```bash
.venv\Scripts\activate
```

En Linux o macOS es `source .venv/bin/activate`. Después:

```bash
pip install -r requirements.txt
```

## Variables de entorno

Copiá la plantilla:

```bash
copy .env.example .env
```

En PowerShell, `Copy-Item .env.example .env`. En Linux o macOS, `cp .env.example .env`.

| Variable | Default | Para qué |
|---|---|---|
| `LLM_PROVIDER` | `openai` | Qué proveedor usar: `openai` o `anthropic` |
| `OPENAI_API_KEY` | vacío | Key de OpenAI, si vas a usar OpenAI |
| `ANTHROPIC_API_KEY` | vacío | Key de Anthropic, si vas a usar Anthropic |
| `OPENAI_MODEL` | `gpt-4o-mini` | ID del modelo de OpenAI |
| `ANTHROPIC_MODEL` | `claude-haiku-4-5` | ID del modelo de Anthropic |
| `MAX_CONCURRENCY` | `5` | Cuántas llamadas pueden viajar a la vez |
| `PINECONE_API_KEY` | vacío | Key de Pinecone (pre-entrega 4) |
| `INDEX_NAME` | `tambor-docs` | Índice serverless de Pinecone; si no existe, se crea |
| `PINECONE_NAMESPACE` | `plataforma` | Partición del índice donde va el corpus |
| `PINECONE_CLOUD` / `PINECONE_REGION` | `aws` / `us-east-1` | Dónde se crea el índice (la región del plan gratuito) |

Los dos modelos por defecto son los más baratos de cada proveedor, que para probar el cliente
alcanzan de sobra. Si necesitás más capacidad, cambiá `ANTHROPIC_MODEL` o `OPENAI_MODEL` en el
`.env`.

Con una sola key alcanza para probarlo. El proveedor que no tenga key devuelve un error
controlado, no un crash.

El `.env` está en `.gitignore`. Las keys se guardan envueltas en `pydantic.SecretStr`, así que si
alguna vez imprimís la configuración por error, no salen en pantalla.

Si el modelo por defecto no está habilitado en tu cuenta, la llamada devuelve un
`ModelNotFoundError`. Para ver cuáles tenés:

```bash
python main.py --list-models
```

## Correr el script de prueba

```bash
python main.py
```

Le pregunta "¿Qué es la entropía?" al proveedor configurado y hace cuatro cosas:

1. Pide la respuesta completa con `await generate()` y muestra tokens y latencia.
2. Pide lo mismo en streaming, imprimiendo cada fragmento a medida que llega.
3. Pide un modelo que no existe, para mostrar que el error vuelve como dato y el programa sigue.
4. Lanza tres prompts en paralelo con `asyncio.gather`.

Otras opciones:

```bash
python main.py --provider anthropic
```

```bash
python main.py --all
```

```bash
python main.py --compare
```

```bash
python main.py --prompt "Explicá qué es un event loop"
```

`--all` prueba todos los proveedores que tengan key. `--compare` le manda el mismo prompt a los
dos en paralelo y muestra las dos respuestas con sus latencias.

## Usarlo como librería

Respuesta completa:

```python
import asyncio
from llm_client import AsyncLLMManager

async def main():
    async with AsyncLLMManager() as manager:
        respuesta = await manager.generate("¿Qué es la entropía?")
        if respuesta.ok:
            print(respuesta.text)
        else:
            print("falló:", respuesta.error.message)

asyncio.run(main())
```

Streaming, si solo querés el texto:

```python
async with AsyncLLMManager() as manager:
    async for texto in manager.stream_text("¿Qué es la entropía?"):
        print(texto, end="", flush=True)
```

Si además necesitás saber cuándo terminó y cuántos tokens gastó, usá `stream()`:

```python
async for chunk in manager.stream("¿Qué es la entropía?"):
    if chunk.type == "delta":
        print(chunk.delta, end="", flush=True)
    elif chunk.type == "done":
        print("\ntokens:", chunk.usage.output_tokens)
    elif chunk.type == "error":
        print("\nerror:", chunk.error.message)
```

Cambiar de proveedor no cambia nada del código de arriba:

```python
async with AsyncLLMManager(provider="anthropic") as manager:
    respuesta = await manager.generate("Hola")
```

Con historial de conversación y parámetros propios:

```python
from llm_client import ChatMessage

mensajes = [
    ChatMessage.system("Sos un profesor de termodinámica."),
    ChatMessage.user("¿Qué es la entropía?"),
    ChatMessage.assistant("Es una medida del desorden de un sistema."),
    ChatMessage.user("Dame un ejemplo cotidiano."),
]

respuesta = await manager.generate(mensajes, temperature=0.3, max_tokens=500)
```

Varias llamadas de una, con el semáforo controlando cuántas salen a la vez:

```python
respuestas = await manager.generate_many([
    "Definí entropía.",
    "Definí entalpía.",
    "Definí energía libre de Gibbs.",
])
```

## Cómo está armado

```
             tu código
                 |
          AsyncLLMManager        fábrica, timeout, reintentos, semáforo
                 |
           BaseLLMClient         interfaz común (ABC)
            /          \
    OpenAIClient    AnthropicClient
          |               |
    AsyncOpenAI     AsyncAnthropic
```

Cuando llamás a `generate()` pasa esto:

1. El manager decide qué proveedor va y arma un `ModelConfig` que Pydantic valida.
2. La fábrica devuelve el cliente concreto. La primera vez lo instancia, después lo reutiliza para
   no rearmar la conexión HTTP en cada llamada.
3. La llamada se envuelve en un semáforo y un `asyncio.timeout`.
4. El cliente concreto traduce los mensajes al formato de su SDK, hace `await`, y devuelve un
   `ModelResponse` con la misma forma para los dos proveedores.
5. Si el SDK lanza una excepción, el cliente la traduce a la jerarquía de `llm_client.errors`. Si
   el error es transitorio el manager reintenta con backoff exponencial; si se agotan los
   reintentos, devuelve el `ModelResponse` con el error adentro.

## Por qué algunas cosas están así

Los errores vuelven como dato en lugar de excepción. `generate()` siempre devuelve un
`ModelResponse`; si algo se rompió, `respuesta.ok` es `False` y `respuesta.error` dice qué pasó, si
vale la pena reintentar y cuántos intentos hubo. Un 429 o una key mal escrita no matan el proceso.

En streaming el generador cierra con un chunk de tipo `error` en vez de lanzar una excepción a
mitad de camino. Si estás sirviendo eso a un frontend, podés mostrar un cartel y cerrar el stream
prolijo, en lugar de que se corte sin explicación.

El streaming no se reintenta solo. Si ya salieron fragmentos en pantalla, repetir la llamada
duplicaría el texto. Quien consume el stream decide si vuelve a pedirlo.

Los dos SDKs se instancian con `max_retries=0`. Ellos reintentan solos por defecto, pero preferí
que la política viva en un lugar único y visible (`AsyncLLMManager._con_reintentos`) en vez de
repartida entre dos capas. Reintenta ante rate limit, red, timeout y errores 5xx. No reintenta
ante 401, 404 ni 400, porque insistir con un error permanente solo gasta cuota.

El semáforo está desde el principio. Tirar mil llamadas con `asyncio.gather` te garantiza un
`429: Too Many Requests`. Con el semáforo se encolan todas pero corren N a la vez.

Los modelos de Pydantic usan `extra="forbid"`. Si escribís `temperatura=0.5` en lugar de
`temperature`, falla al construir el objeto y no en medio de la llamada a la API.

No usé `pydantic-settings` para leer el `.env`, aunque sería lo natural, porque la consigna lista
cuatro dependencias y no quise agregar una quinta. Se resuelve con `python-dotenv` más un
`BaseModel` normal.

## Tests

```bash
python -m pytest -q tests/test_llm_client.py
```

Son 37 tests y corren sin API keys: los proveedores se reemplazan por dobles. Cubren los rangos de
`temperature` y `max_tokens`, roles inválidos, campos con nombre mal escrito, que la key no
aparezca en el `repr`, la traducción de formato de cada proveedor, el orden de los chunks del
stream, que una llamada no bloquee el event loop, y la parte de resiliencia: reintento ante 429,
nada de reintentos ante 401, timeout, error en medio del stream, y un prompt fallido que no arrastra
al resto del lote.

## Archivos

```
llm_client/
  schemas.py               ChatMessage, ModelConfig, ModelResponse, StreamChunk
  base.py                  BaseLLMClient (ABC) con generate() y stream()
  errors.py                jerarquía de errores, con marca de reintentable
  settings.py              lectura del .env
  manager.py               AsyncLLMManager
  providers/
    openai_client.py       AsyncOpenAI
    anthropic_client.py    AsyncAnthropic
tests/
  test_llm_client.py       37 tests con dobles
main.py                    script de prueba
requirements.txt
.env.example
pytest.ini
```
