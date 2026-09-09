# Unified Async LLM Client

Capa de abstracción asíncrona sobre las APIs de **OpenAI** y **Anthropic**, en Python 3.12.
Un solo objeto (`AsyncLLMManager`) expone la misma interfaz para los dos proveedores, con
streaming de tokens, validación de esquemas con Pydantic y manejo de errores que no rompe
el programa.

> Pre-entrega 1 — curso **AI Engineering**, Coderhouse.

---

## Índice

- [Qué resuelve](#qué-resuelve)
- [Instalación](#instalación)
- [Variables de entorno](#variables-de-entorno)
- [Cómo ejecutar el script de prueba](#cómo-ejecutar-el-script-de-prueba)
- [Uso como librería](#uso-como-librería)
- [Arquitectura](#arquitectura)
- [Decisiones de diseño](#decisiones-de-diseño)
- [Tests](#tests)
- [Estructura del repositorio](#estructura-del-repositorio)

---

## Qué resuelve

Instanciar el SDK de OpenAI o de Anthropic directamente en la lógica de negocio trae tres
problemas: acoplamiento (cambiar de proveedor obliga a reescribir código), dificultad para
testear, e inconsistencia entre las firmas de cada SDK. Este proyecto los resuelve con
cuatro piezas:

| Requisito | Cómo se implementa |
|---|---|
| **Intercambiabilidad** | `BaseLLMClient` (ABC) + patrón Factory en `AsyncLLMManager`. Cambiar de proveedor es cambiar una variable de entorno. |
| **Asincronía** | `AsyncOpenAI` y `AsyncAnthropic`. Todas las llamadas son `await`; nada bloquea el event loop. |
| **Streaming** | Generadores asíncronos: `yield` dentro de un `async for` que recorre el stream del SDK. |
| **Validación** | Pydantic v2 para mensajes, configuración del modelo, respuestas y errores. `SecretStr` para las API keys. |

Diferencias entre proveedores que la abstracción absorbe, para que quien consume el cliente
no tenga que conocerlas:

| | OpenAI | Anthropic |
|---|---|---|
| System prompt | un mensaje más, con `role="system"` | parámetro aparte (`system=`) |
| Texto de la respuesta | `choices[0].message.content` | lista de bloques; hay que filtrar los de tipo `text` |
| Tope de tokens | `max_completion_tokens` (opcional) | `max_tokens` (obligatorio) |
| `temperature` / `top_p` | soportados (0 a 2) | **los modelos actuales los quitaron**; el cliente los descarta y avisa por log |
| Tokens consumidos | `usage.prompt_tokens` / `completion_tokens` | `usage.input_tokens` / `output_tokens` |

---

## Instalación

Requiere **Python 3.12** o superior (`asyncio.timeout`, `StrEnum`, sintaxis `X | None`).

```bash
git clone <URL-de-este-repo>
cd <carpeta-del-repo>
```

Crear el entorno virtual e instalar dependencias:

```bash
python -m venv .venv
```

Activarlo:

```bash
.venv\Scripts\activate
```

En Linux o macOS es `source .venv/bin/activate`. Después:

```bash
pip install -r requirements.txt
```

---

## Variables de entorno

Copiar la plantilla y completar:

```bash
copy .env.example .env
```

En PowerShell: `Copy-Item .env.example .env`. En Linux o macOS: `cp .env.example .env`.

| Variable | Obligatoria | Default | Para qué sirve |
|---|---|---|---|
| `LLM_PROVIDER` | no | `openai` | Proveedor activo: `openai` o `anthropic`. Es la variable de configuración que elige el cliente. |
| `OPENAI_API_KEY` | sí, si se usa OpenAI | — | Key de OpenAI ([consola](https://platform.openai.com/api-keys)). |
| `ANTHROPIC_API_KEY` | sí, si se usa Anthropic | — | Key de Anthropic ([consola](https://console.anthropic.com/settings/keys)). |
| `OPENAI_MODEL` | no | `gpt-4o-mini` | ID del modelo de OpenAI. |
| `ANTHROPIC_MODEL` | no | `claude-opus-5` | ID del modelo de Anthropic. |
| `MAX_CONCURRENCY` | no | `5` | Tope de llamadas simultáneas (semáforo). |

**Alcanza con una sola API key** para probar el proyecto: el proveedor sin key devuelve un
error controlado, no un crash.

El `.env` está en `.gitignore` y nunca se sube. Las keys se guardan en memoria envueltas en
`pydantic.SecretStr`, así que no aparecen en logs ni en tracebacks.

Si un modelo por defecto no está habilitado para tu cuenta, la llamada devuelve un
`ModelNotFoundError` controlado. Para ver cuáles tenés disponibles:

```bash
python main.py --list-models
```

---

## Cómo ejecutar el script de prueba

`main.py` es el script de validación. Prueba los dos modos que pide la consigna —respuesta
completa y streaming— con la pregunta "¿Qué es la entropía?".

```bash
python main.py
```

Corre contra el proveedor de `LLM_PROVIDER` y ejecuta cuatro bloques:

1. **Modo normal** — `await generate()`, respuesta completa con tokens y latencia.
2. **Modo streaming** — `async for` sobre el generador, imprimiendo fragmento por fragmento.
3. **Resiliencia** — pide un modelo inexistente a propósito y muestra que el error vuelve
   estructurado, sin cortar el programa.
4. **Concurrencia** — tres prompts en paralelo con `asyncio.gather` y semáforo.

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
python main.py --list-models
```

```bash
python main.py --prompt "Explicá qué es un event loop"
```

`--all` prueba todos los proveedores que tengan API key; `--compare` manda el mismo prompt a
los dos en paralelo y compara respuestas y latencias.

---

## Uso como librería

Respuesta completa:

```python
import asyncio
from llm_client import AsyncLLMManager

async def main():
    async with AsyncLLMManager() as manager:
        respuesta = await manager.generate("¿Qué es la entropía?")
        if respuesta.ok:
            print(respuesta.text)
            print(respuesta.usage.total_tokens, "tokens")
        else:
            print("falló:", respuesta.error.message)

asyncio.run(main())
```

Streaming:

```python
async with AsyncLLMManager() as manager:
    async for texto in manager.stream_text("¿Qué es la entropía?"):
        print(texto, end="", flush=True)
```

Si además querés el evento de cierre y el consumo de tokens, usá `stream()` en lugar de
`stream_text()`:

```python
async for chunk in manager.stream("¿Qué es la entropía?"):
    if chunk.type == "delta":
        print(chunk.delta, end="", flush=True)
    elif chunk.type == "done":
        print("\ntokens:", chunk.usage.output_tokens)
    elif chunk.type == "error":
        print("\nerror:", chunk.error.message)
```

Cambiar de proveedor sin tocar la lógica:

```python
async with AsyncLLMManager(provider="anthropic") as manager:
    respuesta = await manager.generate("Hola")
```

Conversación con historial y parámetros validados:

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

Varias llamadas en paralelo, con el semáforo controlando el flujo:

```python
respuestas = await manager.generate_many([
    "Definí entropía.",
    "Definí entalpía.",
    "Definí energía libre de Gibbs.",
])
```

---

## Arquitectura

```
                       tu código
                           |
                   AsyncLLMManager          <- Factory + timeout + reintentos + semáforo
                           |
                    BaseLLMClient           <- interfaz común (ABC)
                     /            \
             OpenAIClient      AnthropicClient
                    |                |
              AsyncOpenAI      AsyncAnthropic
```

Flujo de una llamada a `generate()`:

1. `AsyncLLMManager` resuelve qué proveedor usar y arma un `ModelConfig` validado.
2. La fábrica devuelve el cliente concreto (lo instancia la primera vez y lo reutiliza).
3. La capa de resiliencia envuelve la llamada en un semáforo y un `asyncio.timeout`.
4. El cliente concreto traduce mensajes y parámetros al formato de su SDK, hace `await`, y
   normaliza la respuesta a `ModelResponse`.
5. Si el SDK lanza una excepción, el cliente la traduce a la jerarquía de `llm_client.errors`.
   Si es transitoria, el manager reintenta con backoff exponencial; si se agotan los
   reintentos, devuelve un `ModelResponse` con `error` cargado.

---

## Decisiones de diseño

**Los errores vuelven como dato, no como excepción.** `generate()` siempre devuelve un
`ModelResponse`. Si algo falló, `respuesta.ok` es `False` y `respuesta.error` trae tipo,
mensaje, si es reintentable y cuántos intentos se hicieron. Un 429 o una key inválida no
tumban el proceso.

**El streaming cierra con un chunk de error, no con una excepción a mitad de camino.** El
generador emite `StreamChunk(type="delta")` por cada fragmento, `type="done"` al terminar, y
`type="error"` si algo se rompe. Un consumidor —una API web, una CLI— puede cerrar el stream
con un mensaje en pantalla en lugar de cortarse sin explicación.

**El streaming no se reintenta solo.** Si ya se emitieron fragmentos al usuario, repetir la
llamada duplicaría texto. Quien consume el stream decide si vuelve a pedirlo.

**Una sola política de reintentos.** Los SDKs se instancian con `max_retries=0` a propósito:
la lógica de reintentos vive en `AsyncLLMManager._con_reintentos`, visible y testeable, en
lugar de repartida entre dos capas. Se reintenta ante rate limit, red, timeout y 5xx; no se
reintenta ante 401, 404 o 400, porque reintentar un error permanente solo gasta cuota.

**Semáforo desde el día uno.** Disparar mil llamadas con `asyncio.gather` garantiza un
`429: Too Many Requests`. El semáforo (`MAX_CONCURRENCY`) limita cuántas viajan en simultáneo:
se encolan todas, corren N.

**`temperature` se valida 0–2 aunque Anthropic ya no la acepte.** La consigna pide ese rango,
que es el de OpenAI. Los modelos actuales de Anthropic quitaron los parámetros de sampling
—el SDK 1.x ni los expone—, así que `AnthropicClient` descarta el valor y lo registra en el
log de debug, en vez de mandarlo y comerse un 400. Esconder esa clase de diferencia es
justamente el trabajo de la capa de abstracción.

**Pydantic con `extra="forbid"`.** Un typo como `temperatura=0.5` falla al construir el
objeto, no en la mitad de una llamada a la API.

**Sin `pydantic-settings`.** La configuración se lee con `python-dotenv` y se valida con un
`BaseModel` común, para no agregar dependencias fuera de las cuatro que pide la consigna.

---

## Tests

37 tests que corren **sin API keys**: los proveedores se reemplazan por dobles.

```bash
python -m pytest -q
```

Cubren la validación de esquemas (rangos de `temperature` y `max_tokens`, roles inválidos,
campos desconocidos), que la API key no se filtre en el `repr`, la traducción de formato de
cada proveedor, el orden de los chunks del stream, que el event loop no se bloquee, y la
resiliencia completa: reintento ante 429, no-reintento ante 401, timeout, error en medio del
stream y un prompt fallido que no tumba el lote.

---

## Estructura del repositorio

```
.
├── llm_client/
│   ├── __init__.py              # API pública del paquete
│   ├── schemas.py               # Pydantic: ChatMessage, ModelConfig, ModelResponse, StreamChunk
│   ├── base.py                  # BaseLLMClient (ABC) con generate() y stream()
│   ├── errors.py                # jerarquía de errores propia, con flag de reintentable
│   ├── settings.py              # carga del .env con SecretStr
│   ├── manager.py               # AsyncLLMManager: fábrica + resiliencia
│   └── providers/
│       ├── openai_client.py     # AsyncOpenAI
│       └── anthropic_client.py  # AsyncAnthropic
├── tests/
│   └── test_llm_client.py       # 37 tests, sin API keys
├── main.py                      # script de validación (modo normal + streaming)
├── requirements.txt
├── .env.example                 # plantilla de variables de entorno
├── pytest.ini
└── README.md
```
