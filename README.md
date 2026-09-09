# Unified Async LLM Client

Un cliente asíncrono que habla con OpenAI y con Anthropic usando la misma interfaz. Python 3.12,
streaming de tokens, validación con Pydantic y errores que no tiran abajo el programa.

Pre-entrega 1 del curso AI Engineering de Coderhouse.

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
| `ANTHROPIC_MODEL` | `claude-opus-5` | ID del modelo de Anthropic |
| `MAX_CONCURRENCY` | `5` | Cuántas llamadas pueden viajar a la vez |

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
python -m pytest -q
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
