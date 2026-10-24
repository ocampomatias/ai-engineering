# Entrega final del curso AI Engineering: "The Intelligence System"
23-oct-2026 · Proyecto: `C:\Users\ocamp\OneDrive\Desktop\CLAUDE CODE\AI ENGINEERING` (repo público https://github.com/ocampomatias/ai-engineering, rama `main`, base `d8df84f`) · Vence: 7-nov-2026 · Estado: aprobado

Todas las rutas de este plan son relativas a esa carpeta.

## Problema y criterio de éxito

Las 7 pre-entregas están aprobadas y ya forman un sistema: API FastAPI (M7) que encola pedidos en Redis, workers que corren el orquestador con supervisor (M6), cuyo investigador usa las herramientas del agente (M5) y el RAG híbrido en Pinecone (M4), con trazas en Phoenix y aprobación humana para reembolsos. Lo que falta para la entrega final es cerrar los huecos que pide la consigna y los que marcaron los profesores, y dejarlo demostrado y documentado:

1. **Un trabajo en `RUNNING` se pierde si la API se reinicia** (PE7 b). Además, si el proceso muere después de sacar un mensaje de la cola y antes de terminarlo, el mensaje se pierde y el trabajo queda en `PENDING` o `RUNNING` para siempre. Es justo el punto 3 de la consigna (que las conversaciones no se pierdan al reiniciar).
2. **El `docker-compose.yml` nunca se probó** (PE7 a) y en esta PC no hay Docker.
3. **Pydantic no cubre todo**: faltan descripciones, validadores propios, y las salidas de las herramientas y partes de la API son `dict` sin modelo (consigna 4, PE2).
4. **Los especialistas no reintentan** ante un 429/529 del LLM (PE5) y no hay tests de concurrencia (PE5).
5. **Faltan evidencias**: captura de costo por ejecución y latencia p95 de 5 pedidos concurrentes (checklist PE7), y las 5+ pruebas con trazas del sistema final (consigna 5).
6. **El README** tiene una sección por pre-entrega pero no una de la entrega final con el diagrama del grafo y el arranque en una instrucción (consigna 6 y 7).
7. **El entorno no es reproducible a futuro**: `requirements.txt` solo tiene cotas mínimas (`>=`) y LangGraph ya avisa que `create_react_agent` se elimina en la 2.0.

**Criterio de éxito (hechos observables):**
- `python -m pytest -q` en verde, con los tests nuevos de recuperación, concurrencia, reintentos y esquema (hoy son 220).
- El workflow de GitHub Actions en verde sobre el commit final: `pip install` + `pytest` en limpio, y `./run.sh` levanta API + Redis + Phoenix, responde `/health`, sirve la consola, un trabajo recorre cola, worker y checkpointer de Redis, y la traza llega a Phoenix.
- `python probar_api.py --reinicio` deja en `evidencia/` un trabajo cuyo servidor se mató a mitad del grafo y que, al volver a levantarlo, se retomó desde el checkpoint, esperó la firma y terminó en `DONE`.
- `python probar_api.py --carga` imprime p50 y p95 de 5 pedidos concurrentes y hay capturas de Phoenix con latencia y costo de cada uno.
- El README arranca con la sección "Entrega final" con el diagrama del grafo y la tabla rúbrica → evidencia.
- Gasto real en la API de Anthropic: USD 0,50 como máximo.

### Qué se verificó en el código (23-oct)

| Pregunta del encargo | Respuesta |
|---|---|
| ¿El investigador usa de verdad el RAG híbrido? | Sí. `orquestador/agents/research_agent.py` arma las herramientas con `crear_buscador(buscador)`, y `app/graph.py` pasa `buscador="auto"`: Pinecone híbrido (`rag_pinecone.RAGSystem`, BM25 + vectorial con RRF) si hay `PINECONE_API_KEY`, y BM25 local sobre el mismo `corpus/` si no. En `evidencia/log_api.log` (corrida de la PE7) no aparece la línea `documentación: BM25 local`, que no está silenciada; así que corrió con Pinecone. Pero nada lo deja a la vista: los logs de `rag_pinecone` están en WARNING y `/health` no lo dice. Se agrega un log al arrancar (paso C4). |
| ¿Hay llamadas síncronas dentro de `async def`? | Ninguna bloqueante en el camino de un pedido. SQLite (`BaseCobros`) y Pinecone (`RAGSystem.buscar`) corren con `asyncio.to_thread`. No se usa `requests`. Las dos herramientas del analista son `def` de cálculo puro, y `ToolNode` las corre en un thread. Lo único síncrono dentro de un `async def`: `iniciar_trazas()` y `cerrar_trazas()` en el `lifespan` (solo al arrancar y al apagar). Se pasan a `to_thread` (C4). |
| ¿Modelos o índices hardcodeados? | Los ids de modelos e índices se leen del entorno (`ANTHROPIC_MODEL`, `OPENAI_MODEL`, `INDEX_NAME`, `PINECONE_NAMESPACE`), con valores por defecto en un solo módulo de configuración cada uno (`llm_client/settings.py`, `rag_pinecone/config.py`). **Excepción:** el modelo de embeddings (`rag/config.py: MODELO_EMBEDDINGS = "jinaai/jina-embeddings-v2-base-es"`) no tiene variable de entorno. Además, `.env.example` dice `LLM_PROVIDER=openai`, pero toda la evidencia se hizo con Anthropic. Paso D2. |
| ¿Qué modelo usa y si reintenta? | `claude-haiku-4-5` (README de la PE7). `pipeline/chain.py: crear_modelo` deja el SDK con `max_retries=0` a propósito: los reintentos viven en `.with_retry()`. El supervisor y la síntesis lo tienen (3 intentos ante `ERRORES_REINTENTABLES`). **Los especialistas no:** el modelo que usa `create_react_agent` no reintenta, y un 429 o 529 se convierte en un `Aporte(error=True)` que gasta una delegación. No hay ningún `RetryPolicy` de LangGraph. Paso C3. |
| ¿Qué modelos Pydantic están incompletos? | **API:** `Pedido` sin descripción ni validador; `Job` con un solo campo descrito y `resultado`/`aprobacion` como `dict[str, Any]` sin esquema; `Revision` y `Accion` sin descripciones; `/health` devuelve un `dict`. **Herramientas:** las 6 entradas tienen modelo con descripciones; las 6 salidas son `json.dumps` de diccionarios, sin modelo. `Comercio` y `Cobro` (agente/datos.py) casi sin descripciones. **Estado:** `Aporte` y `Decision` están completos. Pasos C1 y C2. |
| ¿Arranca la API sin una key válida? | Sí, si la key no está vacía: `crear_modelo` lanza `ConfigurationError` solo cuando la key falta, y `ChatAnthropic` no valida la key al construirse. En el CI alcanza con `ANTHROPIC_API_KEY=ci-sin-key` y `LLM_PROVIDER=anthropic`. El compose actual además falla sin `.env` (`env_file: .env`) y no espera a que Redis esté listo (sin healthcheck, el `ping` del `lifespan` puede fallar y la API se cae). |
| ¿`redis:8` trae lo que necesita `AsyncRedisSaver`? | Sí: Redis Open Source 8 incluye Query Engine (búsqueda) y JSON (la guía de RedisVL, que usa `langgraph-checkpoint-redis`, levanta `redis:8.4`). Igual el CI lo comprueba (`MODULE LIST`), y la API no arranca sin búsqueda porque `checkpointer.asetup()` crea los índices. |
| ¿Están las capturas de costo y p95? | No. Hay 10 capturas: 01–04 y 09–10 de Phoenix, 05–08 de la consola. Se ve el costo de 2 trazas sueltas (USD 0,09 en la 02, USD 0,05 en la 10) y P50/P99 del proyecto en la 01, pero ninguna captura muestra la latencia p95 ni el costo de los 5 pedidos concurrentes. |
| ¿El repo se clona y funciona en limpio? | Clonado desde GitHub en una carpeta temporal: trae código, tests, corpus y evidencia (`d8df84f`). `pytest` en el clon: **220 passed en 12,6 s**, pero con el venv de esta PC. Falta probar un `pip install -r requirements.txt` en limpio: lo hace el job `tests` del CI (B3). |
| Otros hallazgos | `durability` de LangGraph está en `"async"` (el valor por defecto): ante una caída se puede perder el último checkpoint y repetir un nodo con gasto de LLM. Por eso la recuperación lo pasa a `"sync"`. |

## Decisión técnica (y por qué, con alternativas descartadas)

**1. Estructura unificada sin mover paquetes.** El sistema ya es uno solo: `app/` (M7) importa el orquestador (M6), que usa las herramientas del agente (M5), que usa el RAG de Pinecone (M4), que usa el modelo de `pipeline/` (M2) configurado por `llm_client/` (M1). Un proceso, un grafo, una cola. La "unificación" se resuelve **documentando esa cadena de capas** en una sección "Entrega final" arriba de todo en el README, con un solo punto de entrada (`run.sh` / `docker compose up --build`).
- *Descartado:* mover todo a `src/tambor/...`. Rompe los imports de 220 tests, el historial de `git blame` y las rutas que cita el README de cada pre-entrega, sin agregar ninguna funcionalidad. Es el clásico cambio caro que el profesor no puntúa.
- *Descartado:* un `docs/arquitectura.md` aparte. El README ya es el documento que se corrige; partirlo obliga al corrector a buscar.

**2. Recuperación con cola confiable + "el checkpoint decide".** Dos piezas chicas:
- **Cola con lista de "en proceso"** (el patrón que el propio README de la PE7 nombra como "arreglo de fondo"). El worker toma con `BLMOVE tambor:cola → tambor:procesando` en vez de `BRPOP`, y saca el mensaje de `procesando` (`LREM`) recién después de guardar el estado final del trabajo. Al arrancar, antes de lanzar los workers, la API devuelve a la cola todo lo que quedó en `procesando`. Esto cubre los tres casos del encargo: un trabajo en `RUNNING`, un `PENDING` cuyo mensaje ya se había sacado de la cola, y una firma (`resume`) que se estaba procesando. Además elimina la pérdida de mensajes por el `socket_timeout` que documentó la PE7: si el cliente corta justo cuando Redis movió el mensaje, queda en `procesando` y no se pierde.
- **El worker decide la entrada mirando el checkpoint** (`await grafo.aget_state(config)`), no el tipo de mensaje:

  | Estado del checkpoint del `thread_id` | Qué hace el worker | Span en Phoenix |
  |---|---|---|
  | No hay checkpoint | Arranca de cero con el pedido | `pedido` |
  | Tiene interrupción pendiente y el mensaje trae `resume` | `Command(resume=...)` (la firma normal) | `firma` |
  | Tiene interrupción pendiente y el mensaje no trae `resume` | Vuelve a dejar el trabajo en `WAITING_APPROVAL` sin llamar al LLM | ninguno |
  | Tiene `next` (quedó a mitad de camino) | `ainvoke(None, config)`: retoma desde el último paso guardado | `reanudacion`, etiqueta `recuperado` |
  | Sin `next` y con valores (el grafo terminó pero no se guardó el estado) | Marca `DONE` con esos valores, sin llamar al LLM | ninguno |

  Así una sola regla cubre el pedido normal, la firma y la recuperación tras una caída. Además el grafo corre con `durability="sync"`: cada paso queda guardado antes de seguir, y al retomar no se repite lo que ya terminó. Cuesta unos 145 ms por paso contra Redis Cloud, unos 2 s por trabajo, y evita pagar dos veces un nodo de LLM.
- **Idempotencia:** si el trabajo ya está `DONE` o `FAILED`, el mensaje se descarta. El reembolso ya era idempotente (`HSETNX`) y la firma también (`SET NX` en `tomar_revision`).
- **Límite documentado:** una sola instancia de la API. Con dos, la que arranca devolvería a la cola los mensajes que la otra está procesando. Para escalar: una lista de "en proceso" por instancia más un latido con TTL, y que la recuperación solo tome las listas de instancias sin latido. Queda escrito en el README, no se construye.
- *Descartado:* escanear `tambor:job:*` buscando `RUNNING` y reencolarlos (la idea del encargo). No ve el `PENDING` cuyo mensaje ya salió de la cola sin buscar con `LPOS` en la cola, que tiene carreras, y depende de que el estado del job esté al día. La lista de "en proceso" tiene la verdad sin adivinar.
- *Descartado:* el apagado ordenado con `RunControl.request_drain()` de LangGraph 1.2. Solo cubre el apagado ordenado; una caída real (OOM, `kill -9`) igual necesita la recuperación. Se menciona como mejora.

**3. Docker Compose validado en GitHub Actions**, sin keys reales, con un `.env` de CI (`LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY=ci-sin-key`). Redis con versión fija (`redis:8.4.7`) y healthcheck; la API espera a que Redis esté sano. Phoenix con versión fija `arizephoenix/phoenix:version-20.23.0`: es el mismo servidor que se usó en local (`arize-phoenix 20.23.0`) y la etiqueta existe en Docker Hub (verificado). Se agregan volúmenes para que no se pierdan las trazas ni los datos de Redis, y para la caché del modelo de embeddings. Un `run.sh` fino es la "instrucción única", y el CI lo usa, así que se prueba lo mismo que dice el README. En el mismo workflow, un job `tests` hace `pip install -r requirements.txt` + `pytest` en Ubuntu limpio: es la prueba exacta que pidió el profesor en la PE6, y es gratis.
- *Descartado:* que la API arranque sin ninguna key creando el modelo recién en el primer pedido. Es más invasivo, y un servidor mal configurado que se dice sano es peor que uno que falla al arrancar con un mensaje claro.

**4. Reintentos donde faltan, con el patrón que ya usa el repo.** Al especialista se le pasa a `create_react_agent` un modelo dinámico (`lambda estado, runtime: con_reintento`, verificado en `langgraph-prebuilt 1.1.0`), donde `con_reintento = modelo.bind_tools(herramientas).with_retry(retry_if_exception_type=ERRORES_TRANSITORIOS, stop_after_attempt=3)`. Así reintenta cada llamada al LLM, no la corrida entera del agente, y si se agotan los intentos sigue el `Aporte(error=True)` de siempre. Además, `RetryPolicy` de LangGraph en el nodo `reembolso`, ante caídas momentáneas de Redis: es seguro reintentarlo porque `HSETNX` es idempotente.
- *Descartado:* `max_retries=2` en el SDK. Se multiplicaría con el `.with_retry()` del supervisor (hasta 9 intentos) y contradice la decisión documentada en `crear_modelo`.
- *Descartado:* `RetryPolicy` en los nodos especialistas. El nodo atrapa todas las excepciones a propósito para que el supervisor decida, así que la política nunca se dispararía sin cambiar ese diseño.

**5. Pydantic exhaustivo sin cambiar el contrato JSON.** Modelos con descripción en cada campo y validadores propios en la API (`Pedido`, `Revision`, `Accion`, `Job`, nuevos `Resultado`, `SolicitudAprobacion`, `Salud` y `ErrorAPI`) y en las 6 salidas de herramientas. Las claves del JSON quedan iguales, así que la consola y los tests no cambian. Un test recorre `app.openapi()` y falla si algún campo de un modelo propio no tiene descripción: así el "esquema exhaustivo" queda verificado y no depende de acordarse.

**6. Dependencias fijadas (`==`)** a las versiones instaladas hoy (las que pasan los 220 tests). El CI comprueba que existan en Linux.
- *Descartado:* `pip freeze`. Arrastra paquetes que solo existen en Windows y rompe en Linux.

**7. Modelo: queda `claude-haiku-4-5`.** `claude-haiku-5-5` cuesta 10 veces menos (USD 0,10/0,50 por MTok contra 1/5, según `precios.json` y la skill `claude-api`), pero cambia el comportamiento justo antes de entregar:
- el pensamiento viene encendido y consume el `max_tokens=1024` de la síntesis;
- el tokenizador cuenta un 30 % más de tokens;
- Phoenix 20.23 quizás no tenga su precio, y la captura de costo saldría en USD 0.

Ahorraría unos USD 0,40 en total. No vale el riesgo. Queda documentado como la palanca de costo para escalar, y se cambia con `ANTHROPIC_MODEL` sin tocar código.

## IA: modelo, costo, qué pasa si falla

- **Dónde hay IA y dónde no:** el LLM lee y decide a quién delegar (supervisor), qué herramienta llamar (especialistas) y cómo redactar (síntesis). El código decide y calcula todo lo demás: si un pedido necesita firma humana (regex en `app/hitl.py`, no el LLM), si un cobro se puede reembolsar (consulta a la base) y todas las cuentas (herramientas del analista). No cambia en esta entrega.
- **Modelo:** `claude-haiku-4-5` (USD 1 entrada / USD 5 salida por MTok), por `ANTHROPIC_MODEL`. Costo medido en Phoenix en la PE7: USD 0,05 a 0,09 por pedido.
- **Topes de gasto, ya existentes:** como máximo 6 delegaciones por pedido, límite de pasos del grafo y de cada especialista, `JOB_TIMEOUT_S=300`, `max_tokens=1024` y 5 workers.
- **Si la API falla:**
  - 429, 5xx, 529 o un corte de red: 3 intentos con espera exponencial, ahora también en los especialistas.
  - 401, 400 o crédito agotado: no se reintenta. El trabajo queda `FAILED` con el error a la vista en `GET /tasks/{id}` y en la consola, y el span en ERROR en Phoenix. Nunca falla en silencio.
  - Si se agotan los reintentos de un especialista: aporte con error, y el supervisor decide si reintenta o sintetiza con lo que hay.
- **Gasto de esta entrega:** 6 corridas reales (paso E2 y E3), entre USD 0,30 y 0,54 según lo medido. Tope: USD 0,50 acumulados (el costo de cada traza se lee en Phoenix). Si una corrida falla y repetirla lleva el total por encima del tope, la repetición va a la bandeja. Todo lo demás (tests, CI) cuesta USD 0: los tests usan modelos guionados y el CI usa una key falsa (Anthropic responde 401 sin cobrar).

## Costo mensual estimado (hoy / x10)

| Concepto | Hoy (entrega) | Uso real hipotético: 10 pedidos/día | x10: 100 pedidos/día |
|---|---|---|---|
| Anthropic (`claude-haiku-4-5`) | USD 0,30–0,50 por única vez (evidencia) | ~USD 15–27/mes | ~USD 150–270/mes (con `claude-haiku-5-5`, unas 10 veces menos) |
| Redis Cloud (Essentials, 30 MB gratis) | USD 0 | USD 0 (~7 MB vivos con TTL de 1 día) | No alcanza: ~70 MB vivos. Plan pago o bajar `CHECKPOINT_TTL_MIN` (precio a confirmar en redis.io) |
| Pinecone (Starter gratis) | USD 0 | USD 0 | USD 0 (el corpus es chico) |
| Phoenix (local o Docker) | USD 0 | USD 0 | USD 0 |
| GitHub Actions (repo público) | USD 0 | USD 0 | USD 0 |
| **Total** | **≤ USD 0,50 una vez; USD 0/mes después** | **~USD 15–27/mes** | **~USD 150–270/mes + Redis pago** |

No hay plata nueva recurrente: después de la entrega nadie usa el sistema.

## Reuso

- **Del repo:** `JobStore` (se le agregan 3 métodos), `worker.procesar` y `_ejecutar` (se reescribe solo cómo se elige la entrada), `ERRORES_TRANSITORIOS` y `ERRORES_REINTENTABLES` de `pipeline/chain.py`, `SupervisorGuionado` y `ModeloGuionado` de los tests, el `cliente()` de `tests/test_api.py` (se le agrega poder pasar el checkpointer y el `redis` compartidos para simular reinicios), y `probar_api.py` (se le agregan p95 y `--reinicio`).
- **De LangGraph 1.2.14, ya instalado y verificado en `.venv`:** `aget_state` con `StateSnapshot.next` e `.interrupts`, `ainvoke(None, config)`, `durability="sync"`, `RetryPolicy` y el modelo dinámico de `create_react_agent`.
- **De redis-py y fakeredis:** `blmove` y `lmove` (fakeredis 2.40 los implementa, verificado en `list_mixin.py`). *Corrección (implementación, 23-oct):* el `blmove` de fakeredis no bloquea (vuelve `None` al instante), así que los tests usan en el fixture un `blmove` que sondea con `LMOVE` cada 10 ms; producción usa el BLMOVE real.
- **De Phoenix 20.23:** `/healthz` y `GET /v1/projects` existen en el servidor instalado (verificado). El CI los usa.
- **Arsenal:**
  - `context7`: FastAPI `responses=` y `summary`, Compose `depends_on.condition`, redis-py `blmove`, antes de escribir contra cada librería.
  - `playwright`: capturas de Phoenix.
  - `agent-skills:ci-cd-and-automation`: el workflow.
  - `gh run watch`: seguir el CI.

## Datos y seguridad

- **Datos:** todo es ficticio (Tambor, comercios y cobros de `agente/datos_cobros.json`, corpus propio). No hay datos de clientes reales.
- **Secretos:**
  - Las keys de Anthropic y Pinecone y la URL de Redis Cloud (con contraseña) viven solo en `.env`, que está en `.gitignore` y `.dockerignore`. Nunca se imprimen ni se loguean: la recuperación y el arranque no deben loguear `REDIS_URL`.
  - El CI no usa secretos de GitHub: escribe un `.env` con una key falsa.
- **Repo público:** antes de cada commit con evidencia nueva, buscar en `evidencia/` y `screenshots/` `redis://`, `sk-ant`, `pcsk_` y `rediss://`. Revisar que ninguna captura muestre el `.env` ni una URL con contraseña.
- **Plata (ficticia):** el reembolso sigue detrás de la firma humana. Con la recuperación, un reembolso nunca se ejecuta dos veces: `HSETNX` lo impide y hay un test para el caso "se cae durante la firma".
- **Límite conocido (se documenta, no se construye):** la API no tiene autenticación, y cualquiera con acceso al puerto puede firmar. Para el curso alcanza, en producción iría detrás de un login.

## Pasos

Ordenados por impacto en la rúbrica. Cada fase se commitea en `main` y se pushea: eso además dispara el CI desde la fase B. El README va al final porque cita la evidencia de las fases anteriores.

### Fase A: persistencia y recuperación (rúbrica: persistencia 20 %; consigna 3; corrección PE7 b)

1. [x] **Cola confiable.** En `app/jobs.py`:
   - nueva constante `PROCESANDO = "tambor:procesando"`;
   - `desencolar()` usa `BLMOVE COLA PROCESANDO RIGHT LEFT` (mismo timeout de 2 s) y devuelve `(crudo, mensaje)`;
   - nuevo `confirmar(crudo)`, que hace `LREM PROCESANDO 1 crudo`;
   - nuevo `recuperar() -> int`, que hace `LMOVE PROCESANDO COLA` en bucle hasta vaciar y devuelve cuántos movió.

   En `app/main.py`, el `lifespan` llama a `recuperar()` antes de crear los workers y loguea `[recuperación] N mensaje(s) vuelven a la cola` (sin la URL de Redis). Comentario `shortcut:` en `recuperar` sobre el límite de una sola instancia.
   — verificación: test nuevo en `tests/test_api.py`: un mensaje movido a `procesando` y no confirmado vuelve a la cola al arrancar una app nueva y termina `DONE`; `llen(COLA) == llen(PROCESANDO) == 0` al final de los tests existentes.

2. [x] **El worker confirma solo después de guardar el estado final** (`app/worker.py`). `correr_worker` desempaqueta `(crudo, mensaje)` y llama a `store.confirmar(crudo)` después de `procesar`. Un `CancelledError` (apagado) no confirma, así que el mensaje se recupera al arrancar. Al empezar `procesar`, si el trabajo está `DONE` o `FAILED`, o está `WAITING_APPROVAL` y el mensaje no trae `resume`, lo descarta, lo loguea y lo confirma.
   — verificación: test nuevo: un mensaje duplicado de un trabajo `DONE` no lo vuelve a correr (el modelo guionado registra 0 llamadas nuevas) y el resultado no cambia.

3. [x] **"El checkpoint decide" + `durability="sync"`** (`app/worker.py: _ejecutar`):
   - `snap = await grafo.aget_state(config)` y la tabla de la decisión 2 (pedido, firma, en espera, reanudación, ya terminado);
   - `ainvoke(..., durability="sync")`;
   - span `reanudacion` con etiqueta `recuperado` y `set_input` con el `next` del checkpoint;
   - log `[worker] <id> reanuda desde el checkpoint (siguiente: investigador)`.

   La anotación de la firma en Phoenix sigue igual.
   — verificación, 3 tests nuevos con fakeredis e `InMemorySaver` compartidos entre dos `lifespan` (simulan el reinicio):
   - (a) el investigador falso se bloquea en un `asyncio.Event`, se cierra el primer `lifespan` con el trabajo en `RUNNING`, el segundo lo termina en `DONE` sin repetir la primera decisión del supervisor (el guion registra exactamente 2 llamadas en total) y con un solo aporte del investigador;
   - (b) un grafo que terminó pero cuyo estado quedó en `RUNNING` pasa a `DONE` sin ninguna llamada al modelo;
   - (c) una caída durante la firma (mensaje con `resume` en `procesando`) termina en `DONE` con el reembolso registrado una sola vez.

   Además, los 12 tests actuales de `test_api.py` siguen verdes.

4. [x] **`probar_api.py --reinicio`.**
   - Levanta su propio `uvicorn` en el puerto 8001 como subproceso, con salida a `evidencia/log_reinicio_api.log`.
   - Manda el pedido de reembolso y espera en ese log la línea `[investigador] recibe`. Para entonces el checkpoint posterior al supervisor ya está guardado, por `durability="sync"`.
   - Mata el proceso con `kill()`: una caída real, sin apagado ordenado.
   - Levanta otro servidor, espera `WAITING_APPROVAL`, aprueba y espera `DONE`.
   - Imprime la línea de tiempo y guarda `evidencia/reinicio_api.json`.

   — verificación: se corre de verdad en E3. Acá alcanza con `python probar_api.py --help` y una lectura del código. No gasta.

### Fase B: Docker Compose validado y arranque en una instrucción (rúbrica: documentación y despliegue 15 %; consigna 7; corrección PE7 a)

5. [x] **`docker-compose.yml`:**
   - `redis:8.4.7`: healthcheck `redis-cli ping`, volumen `redis_data:/data`, sin puerto publicado en el host (evita choques con un Redis local);
   - `arizephoenix/phoenix:version-20.23.0` con `PHOENIX_WORKING_DIR=/mnt/data` y volumen `phoenix_data`;
   - `api`: `depends_on: redis: condition: service_healthy` y volumen `modelos:/srv/.cache` (el modelo de embeddings se baja una sola vez, en modo Pinecone).

   **`Dockerfile`:** `ENV PYTHONUNBUFFERED=1`, para que los logs salgan en `docker compose logs` al momento. **`.dockerignore`:** sumar `.git`, `.github`, `screenshots`, `evidencia` y `.playwright-mcp`.
   — verificación: `docker compose config` en el CI (la sintaxis) y el paso 7.

6. [x] **`run.sh`** (unas 8 líneas): si no hay `.env`, lo copia de `.env.example` y termina con el aviso de completar `ANTHROPIC_API_KEY` (y `PINECONE_API_KEY` para el RAG en Pinecone). Si hay, corre `docker compose up --build -d` e imprime las URLs de la consola, `/docs` y Phoenix. Además:
   - `.gitattributes` con `*.sh text eol=lf`, para que un clon en Windows no lo rompa con CRLF;
   - `git update-index --chmod=+x run.sh`, porque en Windows el bit de ejecución no se guarda solo.

   — verificación: `git ls-files -s run.sh` muestra modo `100755`. El CI lo ejecuta en el paso 7.

7. [x] **`.github/workflows/sistema.yml`**, que corre en `push` a `main` y con `workflow_dispatch`. Dos jobs:
   - `tests`: `actions/setup-python` 3.12 con caché de pip, `pip install -r requirements.txt`, `python -m pytest -q`.
   - `compose` (`timeout-minutes: 25`):
     1. escribe `.env` con `LLM_PROVIDER=anthropic` y `ANTHROPIC_API_KEY=ci-sin-key`;
     2. corre `./run.sh`;
     3. espera `/health` hasta 5 minutos;
     4. comprueba que `/` contiene "Libro de operaciones" y que `/openapi.json` responde;
     5. comprueba Phoenix `/healthz` y que `docker compose exec -T redis redis-cli MODULE LIST` incluye `search` y `ReJSON`;
     6. hace `POST /tasks` y espera que el trabajo llegue a `FAILED` con `AuthenticationError` en `error`: eso prueba que la cola, el worker y el checkpointer funcionan contra el Redis del compose (el primer checkpoint se escribe antes de llamar al LLM);
     7. espera que `GET localhost:6006/v1/projects` liste `tambor-api` (la traza llegó a Phoenix);
     8. si algo falla, `docker compose logs --no-color`; siempre, `docker compose down -v`.

   — verificación: `gh run watch` en verde. Si algo falla, se corrige con el log del job, sin plata ni keys.

8. [x] *(Cubierto por el CI: run 40039745693 arrancó con `ci-sin-key` y el trabajo llegó a FAILED con 401.)* **Probar en local, gratis, que la API arranca con una key falsa:** levantar `uvicorn` con `ANTHROPIC_API_KEY=ci-sin-key` y `LLM_PROVIDER=anthropic` en el entorno de esa terminal (pisan al `.env` porque `load_dotenv` no sobrescribe), contra Redis Cloud, y ver `/health` en 200. No se manda ningún pedido.
   — verificación: `curl localhost:8000/health` → `{"estado":"ok"}`. Ahorra vueltas de CI si algo del arranque depende de la key.

### Fase C: robustez (rúbrica 25 %; consigna 2 y 4; correcciones PE2 y PE5)

9. [x] **Pydantic en la API** (`app/main.py`, `app/jobs.py`, `app/hitl.py`):
   - descripción en cada campo de `Pedido`, `Job`, `Revision` y `Accion`;
   - validadores propios: `Pedido.pedido` se recorta y rechaza un texto que sea solo espacios; `Revision.revisor` se recorta y no puede quedar vacío; `Accion.cobro_ids` sin repetidos y con el formato `cob_XXXXX`;
   - `Job.resultado: Resultado | None` (`respuesta_final`, `resultado_accion`, `revision`, `decisiones: list[Decision]`, `aportes: list[Aporte]`) y `Job.aprobacion: SolicitudAprobacion | None` (`accion`, `analisis`, `pregunta`, `span_id`), con las mismas claves de hoy;
   - `/health` devuelve `Salud`; `ErrorAPI` documentado en `responses=` para 404, 409, 422 y 503;
   - `summary` en cada endpoint, `tags` y `version="8.0.0"`.

   — verificación:
   - test nuevo que recorre `app.openapi()["components"]["schemas"]`, sin `HTTPValidationError` ni `ValidationError` de FastAPI, y falla si algún campo no tiene `description`;
   - test de los validadores (`"     "` → 422; revisor `"  "` → 422);
   - los tests de la consola y del flujo siguen verdes sin tocar `app/static/`.

   *Nota (implementación, 23-oct):* `Accion` y `Revision` siguen en `app/hitl.py` (el serializador de los checkpoints las identifica por módulo), pero ahora extienden `AccionGuardada` y `RevisionGuardada` de `app/jobs.py`, que son las formas guardadas dentro de `Job`, sin los validadores de entrada. Así un trabajo ya guardado en Redis (por ejemplo, con un revisor de solo espacios, que antes pasaba) se sigue leyendo, y además se evita el import circular `jobs` ↔ `hitl`. `JobStore.actualizar` valida con `Job.model_validate` en vez de `model_copy`, porque el worker manda dicts. `ErrorAPI.detail` es `str | list[dict]`: en un 422 FastAPI devuelve la lista de campos. `Aporte.agente` no tenía descripción y se le agregó. Test extra: un trabajo con el formato viejo (sin `span_id`) se lee y vuelve con las mismas claves.

10. [x] **Pydantic en las salidas de las 6 herramientas** (`agente/herramientas.py` y `orquestador/agents/analyst_agent.py`):
    - modelos `SalidaBuscarComercio`, `SalidaListarCobros`, `SalidaConsultarCobro`, `SalidaBuscarDocumentacion` (con `Fragmento` y el campo `buscador`, que deja a la vista en Phoenix si respondió `pinecone-hibrido` o `bm25-local`), `Estadisticas` y `ResultadoCalculo`;
    - cada herramienta devuelve `modelo.model_dump_json()`;
    - descripciones en `Comercio` y `Cobro`;
    - validadores: `EntradaConsultarCobro` pasa el ID a mayúsculas antes del patrón (`cob_7f3k2` → `cob_7F3K2`) y las consultas de texto se recortan;
    - un log `validación OK` por llamada, con el nombre de la herramienta y los milisegundos.

    — verificación: los tests actuales de herramientas (mismas claves JSON); test nuevo que valida la salida de cada herramienta con su modelo (`Modelo.model_validate_json(salida)`); test de la normalización del ID.

    *Nota (implementación, 23-oct):* las salidas se serializan con `model_dump_json(exclude_unset=True)`, no con `model_dump_json()` a secas: así `aviso` no aparece como `null` cuando no se llenó, y las claves quedan iguales a las de antes. Se agregó `CobroResumido` (los 5 campos que devuelve `listar_cobros`). El log es `validación OK: <herramienta> -> <Modelo> en X ms`.

11. [x] **Reintentos (PE5).**
    - En `orquestador/agents/especialista.py`, el modelo dinámico con `bind_tools(...).with_retry(retry_if_exception_type=ERRORES_TRANSITORIOS, stop_after_attempt=3)` (decisión 4).
    - En `app/graph.py`, `retry_policy=RetryPolicy(max_attempts=3, retry_on=(redis ConnectionError, TimeoutError))` en el nodo `reembolso`.

    — verificación:
    - test nuevo: un modelo guionado que lanza `anthropic.APIConnectionError` una vez y después responde da un aporte sin error, con 2 llamadas al modelo;
    - test: un `AuthenticationError` no se reintenta (1 llamada) y queda como aporte con error;
    - `test_estructura_del_grafo` sigue verde.

12. [x] **Logs de validación y tiempos (PE2) + async en el arranque.**
    - `POST /tasks` loguea `validación OK: pedido de N caracteres -> job <id> encolado en X ms`, y `/approve`, `validación OK: revisión de <revisor> (aprobado=...)`.
    - El supervisor antepone `validación OK: Decision` a su log actual.
    - El worker loguea `[worker] <id> -> DONE en 23,4 s` (con `time.perf_counter`).
    - `crear_buscador` loguea `documentación: pinecone-hibrido` o `documentación: bm25-local` al crearse. *(Hecho junto con el paso 10, en `agente/documentacion.py`, con test en `tests/test_agente.py`.)*
    - El `lifespan` llama a `iniciar_trazas` y `cerrar_trazas` con `await asyncio.to_thread(...)`.

    — verificación: test con `caplog` que encuentra `validación OK` y `DONE en` después de un pedido; la suite completa en verde.

    *Nota (implementación, 23-oct):* el log del supervisor quedó `validación OK: Decision | [supervisor] evalúa: ...` (`probar_api.py` sigue contando `[supervisor] evalúa`). El worker también loguea `-> WAITING_APPROVAL en X s` y `-> FAILED en X s`; el formato del `error` no cambió. `iniciar_trazas` puede ir a un thread: el `AsyncClient` de Phoenix crea un `httpx.AsyncClient`, que no necesita el loop para construirse (probado: creado en un thread y usado en el loop).

13. [x] **Tests de concurrencia (PE5)**, en `tests/test_api.py` y `tests/test_agente.py`:
    - (a) 20 `POST /tasks` simultáneos con `asyncio.gather` y 5 workers: todos `DONE`, ids únicos, cola y `procesando` vacías, y cada trabajo con sus propios aportes (los `thread_id` no se mezclan). Se usa el supervisor guionado sin guion, que va siempre directo a la síntesis, para que no haya estado compartido entre pedidos.
    - (b) dos `POST /approve` simultáneos sobre el mismo trabajo: exactamente un 202 y un 409, y un solo reembolso.
    - (c) 20 llamadas simultáneas a `consultar_cobro` y `listar_cobros` sobre la misma `BaseCobros`: resultados idénticos a los secuenciales (el lock de SQLite funciona entre threads).

    — verificación: `pytest -q tests/test_api.py tests/test_agente.py` en verde 3 veces seguidas (que no sean inestables).

    *Nota (implementación, 23-oct):* (b) ya estaba cubierto por `test_la_firma_es_atomica_si_falla_el_encolado_se_puede_volver_a_firmar` (dos firmas a la vez: `[202, 409]` y un solo reembolso), así que no se duplicó. En (a), en vez del supervisor sin guion (que no delega y deja los aportes vacíos), un `SupervisorPorPedido` sin estado compartido que delega una vez con el pedido como instrucción: así se comprueba que cada trabajo tiene su propio aporte.

### Fase D: entorno y estándares (rúbrica 10 %)

14. [x] **`requirements.txt` con versiones fijas (`==`)**, con las versiones instaladas hoy (`pip list` del `.venv`: `langgraph==1.2.14`, `langchain-core==1.6.3`, `fastapi==0.146.0`, etc.). Se mantienen los comentarios por pre-entrega y se agrega arriba "Python 3.12+".
    — verificación: el job `tests` del CI instala en Ubuntu limpio y pasa. Si una versión no tiene wheel para Linux, se ajusta esa sola y se anota el porqué.

15. [x] **Variables de entorno.**
    - `EMBEDDINGS_MODEL` en `rag_pinecone/config.py: cargar_config`. El valor por defecto queda en `rag/config.py`, que es el único lugar.
    - En `.env.example`: `LLM_PROVIDER=anthropic`, `ANTHROPIC_MODEL=claude-haiku-4-5` y `EMBEDDINGS_MODEL`.
    - En el README, una tabla de todas las variables: para qué es cada una y su valor por defecto. Va con una frase explícita: "ningún id de modelo o de índice está en la lógica; los valores por defecto viven solo en `llm_client/settings.py`, `rag/config.py` y `rag_pinecone/config.py`, y coinciden con `.env.example`".
    - Decidido: no se sacan esos valores por defecto. Haría falta tocar unas 10 construcciones de `ConfigPinecone()` en tests y en `BuscadorLocal`, sin mejorar nada para el usuario.

    — verificación: test nuevo: `cargar_config` con `EMBEDDINGS_MODEL=x` en el entorno da `modelo_embeddings == "x"`; `grep` de `claude-|gpt-|tambor-docs|jina` en `*.py` fuera de tests da solo esos tres módulos.

### Fase E: evidencia real (rúbrica: observabilidad 20 %; consigna 5; checklist PE7). Tope: USD 0,50

16. [x] *(Hecho: `documentación: pinecone-hibrido` en el log de arranque y `/health` 200.)* **Comprobación previa, sin gastar.** `phoenix serve` en una terminal; `uvicorn app.main:app` con el `.env` real (Redis Cloud). En el log de arranque tiene que aparecer `documentación: pinecone-hibrido`. Si dice `bm25-local`, falta `PINECONE_API_KEY` en el `.env`: frenar, porque la evidencia del RAG tiene que ser con Pinecone. Anotar el costo acumulado del proyecto en Phoenix como punto de partida.
    — verificación: línea del log y `/health` en 200.

17. [x] *(Hecho: `--reinicio` en DONE; `[recuperación] 1 mensaje(s)` y `reanuda desde el checkpoint (siguiente: investigador)`, una sola decisión del supervisor antes del corte. Se corrió 3 veces: la 1.ª perdía el span `firma` porque el script mataba la API 2 antes de que salieran las trazas (se agregó una espera de 8 s); la 2.ª la cortaron timeouts de Redis Cloud (el trabajo se cerró como FAILED con ese motivo). `cob_7E8Q4` sale "ya tenía un reembolso registrado" porque se reembolsó en la PE7: la idempotencia entre corridas.)* **E3 primero (lo más valioso): `python probar_api.py --reinicio`.** Una corrida, unos USD 0,05–0,10 (el nodo que se cortó se paga dos veces).
    — verificación:
    - `evidencia/reinicio_api.json` con `DONE`, el reembolso de `cob_7E8Q4` registrado y `cob_7F3K2` rechazado;
    - en `evidencia/log_reinicio_api.log`, las líneas `[recuperación] 1 mensaje(s) vuelven a la cola` y `reanuda desde el checkpoint`, y una sola línea `[supervisor] evalúa` antes del corte;
    - en Phoenix, la sesión del trabajo con `reanudacion` (etiqueta `recuperado`) y `firma` anotada.

    Nota para el README: el span raíz de la corrida que se cortó no llega a Phoenix porque el proceso murió antes de exportarlo. Es justamente lo que pasa en una caída real.

18. [x] *(Hecho: 5/5 DONE, p50 41,6 s y p95 66,3 s; POST p95 1,2 s. Hallazgo: el SDK de Anthropic 1.15 emitía su propio span por llamada y Phoenix contaba el costo dos veces; se apagó con `ANTHROPIC_OPEN_TELEMETRY=false` en `iniciar_trazas` y la carga se repitió para que la captura muestre el costo real: USD 0,15 los 5 pedidos. Gasto real total de la Fase E: ~USD 0,35, medido por tokens de los spans `ChatAnthropic`.)* **E2: `python probar_api.py --carga` (5 pedidos concurrentes).** Unos USD 0,30–0,40. El script suma p50 y p95 de la latencia total y del `POST`, calculados por rango más cercano: con 5 muestras, p95 es la más lenta, y se dice así. Guarda `evidencia/salida_carga_final.txt` y `carga_api.json`.
    - Antes de correr, sumar el costo de E3 al punto de partida: si lleva más de USD 0,15, no correr E2 todavía y mandarlo a la bandeja.
    - Si algún pedido termina `FAILED` y repetirlo lleva el total por encima de USD 0,50, la repetición va a la bandeja.

    — verificación: los 5 en `DONE`; p95 impreso; costo acumulado en Phoenix ≤ USD 0,50.

19. [x] *(Hecho: 11 es la vista Sessions con latencia y costo por trabajo, porque Traces y Metrics de Phoenix 20.23 no terminaban de cargar; 12 el span `buscar_documentacion` con EnsembleRetriever y `pinecone-hibrido`; 13 la sesión recuperada con la anotación humana; 14 la página de proyectos. El costo de 13 y de las sesiones previas a las 21:40 aparece duplicado por el hallazgo del paso 18.)* **Capturas con Playwright** en `screenshots/`:
    - `11_phoenix_carga_latencia_costo.png`: las 5 trazas raíz de la carga con las columnas de latencia y costo;
    - `12_phoenix_rag_pinecone.png`: el span `buscar_documentacion` con `"buscador": "pinecone-hibrido"` y sus fragmentos;
    - `13_phoenix_sesion_recuperada.png`: la sesión del trabajo de E3;
    - `14_phoenix_proyecto.png`: totales del proyecto.

    Luego, la búsqueda de secretos de la sección "Datos y seguridad" sobre `evidencia/` y `screenshots/`.
    — verificación: abrir cada PNG; el `grep` de secretos no encuentra nada.

### Fase F: documentación (rúbrica: arquitectura 30 % y documentación 15 %; consigna 1 y 6)

20. [x] *(Hecho: regenerado con `InMemorySaver`, `SupervisorGuionado` y nodos falsos para los especialistas, en un one-off sin script nuevo. `git diff` vacío: la topología del código es la misma que la de la PE7; el `RetryPolicy` de `reembolso` no se dibuja en el Mermaid.)* **Regenerar el diagrama del grafo desde el código** (`construir_grafo(...).get_graph().draw_mermaid()` con un checkpointer en memoria y modelos guionados, sin keys) en `evidencia/grafo_api.mmd`, para que el README no muestre un diagrama que el código ya no tiene.
    — verificación: `git diff evidencia/grafo_api.mmd` vacío o con cambios explicables.

21. [x] *(Hecho: sección de ~320 líneas, más que las 150–250 previstas por los dos diagramas Mermaid y las 5 tablas pedidas; la prosa queda en ~200. Los dos Mermaid se renderizaron con `mermaid-cli` 11 sin errores. 53 enlaces relativos y anclas verificados, ninguno roto. La tabla de variables de la PE1 se reemplazó por un enlace a la nueva, para no tener dos. En la PE7 también se corrigió `BRPOP` → `BLMOVE` en el diagrama y `SET NX` → `WATCH` + `MULTI` en la firma.)* **README, sección "Entrega final: The Intelligence System" arriba de todo** (unas 150–250 líneas; el detalle sigue en las secciones de cada pre-entrega). Contenido:
    - qué tarea compleja resuelve, con un ejemplo de pedido que usa RAG, varios pasos y firma;
    - **arranque en una instrucción** (`./run.sh`, o `docker compose up --build` con `.env`) y el camino sin Docker en 3 comandos;
    - **diagrama de flujo del grafo de agentes** en Mermaid, con las herramientas y el RAG híbrido colgando del investigador;
    - diagrama de arquitectura (API, Redis, workers, Pinecone, Phoenix);
    - mapa de capas M1→M7 (la decisión 1);
    - persistencia y recuperación (la tabla "el checkpoint decide" y el límite de una instancia);
    - observabilidad (las 5+ pruebas con capturas, p95 y costo);
    - robustez (async, Pydantic, reintentos, concurrencia);
    - tabla de variables de entorno;
    - tests y badge del CI;
    - tabla "corrección de cada profesor → qué se hizo → dónde se ve";
    - tabla rúbrica → evidencia (la del final de este plan);
    - límites conocidos (una instancia, sin autenticación, `create_react_agent` deprecado y fijado a <2).

    En la PE7: reemplazar "No lo pude probar" del compose y "Lo que no resolví" por lo hecho; actualizar la cuenta de tests y la lista de archivos. Sumar una fila "Entrega final" a la tabla de entregas.
    — verificación: vista previa del Markdown en GitHub después del push (el Mermaid se dibuja); todos los enlaces relativos existen (`ls` de cada ruta citada).

22. [ ] **Verificación final en limpio:** clonar de GitHub en una carpeta temporal del scratchpad, `pytest -q` en el clon, y CI en verde sobre ese mismo commit (`gh run list -L 1`).
    — verificación: la salida de los dos, citada en el resumen de la implementación.

## Riesgos

| Riesgo | Cómo se nota | Qué se hace |
|---|---|---|
| El build de la imagen en el CI falla (algún paquete pide compilador en `python:3.12-slim`, o una versión fijada no tiene wheel para Linux) | Job `compose` o `tests` en rojo, con el log | Ajustar esa dependencia, o agregar `build-essential` en el Dockerfile solo si hace falta. No cuesta plata. |
| La imagen tarda o pesa mucho (`arize-phoenix`, `chromadb`, `onnxruntime`) | El job tarda más de 15 min | `timeout-minutes: 25`. Si molesta, separar las dependencias del servidor de Phoenix (fuera de alcance salvo que bloquee). |
| `BLMOVE` se comporta distinto en fakeredis y en Redis Cloud | Los tests pasan y E3 no recupera | E3 lo prueba contra Redis Cloud real. Si falla, `BRPOPLPUSH` es equivalente y está en los dos. |
| E3 mata el servidor antes del primer checkpoint | El log dice que arrancó de cero, no `reanuda` | El script espera la línea `[investigador] recibe`, que con `durability="sync"` viene después del checkpoint del supervisor. |
| Las corridas reales superan USD 0,50 | El costo acumulado en Phoenix | Orden E3 → E2, el corte antes de E2 y la regla de bandeja para repetir. |
| Un trabajo viejo de la PE7 en Redis Cloud no valida con los modelos nuevos de `Job` | 500 en `GET /tasks/{id}` de un trabajo de octubre | Mismas claves que hoy, así que no debería pasar. Además el TTL de 7 días los borra el 25-oct. |
| Varias instancias de la API comparten `procesando` | Trabajos procesados dos veces a la vez | Límite documentado; el sistema corre en una sola instancia. |
| La ruta `/v1/projects` de Phoenix responde distinto en el contenedor | Falla solo ese paso del CI | Consultar `context7` y ajustar el chequeo. Lo importante (API, Redis, worker) ya pasó. |
| Un CRLF o la falta del bit de ejecución rompen `run.sh` en Linux | `./run.sh: Permission denied` o `bad interpreter` en el CI | `.gitattributes` + `git update-index --chmod=+x` del paso 6. |

Cuando algo se rompe en producción se nota así: un trabajo en `FAILED` con el error en la API y la consola; el span en ERROR en Phoenix; 503 claro si se cae Redis; en el log, `[recuperación] N` al arrancar, y `DONE en X s` o `falló` por trabajo.

## Fuera de alcance

- Mover los paquetes a `src/` o reordenar los scripts de la raíz.
- Varias instancias de workers con latido y lease, y el apagado ordenado con `RunControl.request_drain()`.
- Cliente async nativo de Pinecone: `to_thread` alcanza y no bloquea el event loop.
- Migrar a `claude-haiku-5-5` o a `langchain.agents.create_agent`: quedan documentados como próximos pasos; las versiones fijadas evitan que LangGraph 2.0 rompa el proyecto.
- Prompt caching y costo por trabajo calculado por la API (Phoenix ya lo da).
- Autenticación de la API.
- CI/CD de despliegue a la nube (la consigna dice que no suma ni resta). El workflow solo valida.

## Bandeja (lo que espera a Matías)

1. **Entregar en la plataforma de Coderhouse** el link al repo (`https://github.com/ocampomatias/ai-engineering`, commit final) antes del 7-nov-2026. Es una publicación hacia afuera: solo él puede hacerla.
2. **Condicional:** si las corridas reales (pasos 17 y 18) llegan a USD 0,50 acumulados y hace falta repetir alguna, esa repetición espera su visto bueno.

## Mapa rúbrica → evidencia

| Criterio (peso) | Qué lo demuestra |
|---|---|
| **Arquitectura y coherencia (30 %):** LLMs + RAG + agentes en un flujo | README "Entrega final" (diagrama del grafo y de arquitectura, mapa de capas M1→M7); `app/graph.py` (el grafo único); `orquestador/agents/research_agent.py` (el investigador con el RAG); `evidencia/grafo_api.mmd`; captura `12_phoenix_rag_pinecone.png` (el RAG híbrido dentro de una traza del orquestador); `evidencia/salida_carga_final.txt` (pedidos que usan investigador → analista → síntesis) |
| **Robustez técnica (25 %):** async/await en 3.12 y Pydantic | `app/` (todo `async`, Redis con `redis.asyncio`, `AsyncRedisSaver`, `to_thread` para SQLite y Pinecone); modelos de `app/main.py`, `app/jobs.py`, `app/hitl.py`, `agente/herramientas.py`, `orquestador/agents/analyst_agent.py`, `orquestador/state.py`; el test del esquema OpenAPI exhaustivo; `/docs`; tests de reintentos y de concurrencia; log `validación OK` y `DONE en X s` en `evidencia/log_reinicio_api.log` |
| **Persistencia y observabilidad (20 %):** checkpointers y trazas externas | `AsyncRedisSaver` en `app/main.py`; recuperación en `app/jobs.py` y `app/worker.py`; tests de reinicio en `tests/test_api.py`; `evidencia/reinicio_api.json` y `log_reinicio_api.log`; capturas 11–14 (y 01–04, 09–10 de la PE7); la anotación HUMAN de la firma |
| **Documentación y despliegue (15 %):** README, diagrama, facilidad de ejecución | README "Entrega final"; `run.sh`; `docker-compose.yml`; `Dockerfile`; `.github/workflows/sistema.yml` en verde (badge); job `compose` que prueba el arranque conjunto |
| **Entorno y estándares (10 %):** Python 3.12+, dependencias y variables | `requirements.txt` con versiones fijas; `Dockerfile` `python:3.12-slim`; `setup-python 3.12` en el CI; `.env.example` completo; tabla de variables en el README; job `tests` (instalación y tests en limpio) |

| Corrección del profesor | Paso | Evidencia |
|---|---|---|
| PE7 a: compose sin probar | 5–8 | CI `compose` en verde |
| PE7 b: `RUNNING` no se recupera | 1–4, 17 | tests de reinicio y `evidencia/reinicio_api.json` |
| PE7: capturas de costo y p95 | 18–19 | `salida_carga_final.txt`, captura 11 |
| PE2: descripciones, validadores y logs | 9, 10, 12 | test del esquema, `/docs`, líneas `validación OK` |
| PE5: reintentos y concurrencia | 11, 13 | tests nuevos |
| PE6: clon en limpio | 7, 22 | job `tests` y verificación final |
