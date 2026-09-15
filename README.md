# AI Engineering (Coderhouse)

Proyecto integrador del curso. Cada pre-entrega se apoya en la anterior.

| Entrega | Qué es | Dónde |
|---|---|---|
| Pre-entrega 2 | Pipeline de extracción de entidades técnicas con LangChain y Pydantic | `pipeline/`, `probar_pipeline.py` |
| Pre-entrega 1 | Cliente asíncrono unificado para OpenAI y Anthropic | `llm_client/`, `main.py` |

La instalación y las variables de entorno son las mismas para las dos: están en
[Instalación](#instalación) y [Variables de entorno](#variables-de-entorno).

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
texto de entrada. Lo dejo anotado en vez de esconderlo, porque es justo lo que la prueba tenía que
mostrar: el contrato garantiza la forma de la salida, no que sea verdad.

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
