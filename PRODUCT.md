# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Delegado: HTML, CSS y JavaScript sin build step, servido por la misma API FastAPI (`app/main.py`) en `/`. Sin dependencias de npm. Así la consola corre con `uvicorn app.main:app` y nada más.

## Users

El corrector del curso AI Engineering de Coderhouse. Clona el repo, levanta Redis, Phoenix y la API, y quiere comprobar en pocos minutos que el sistema hace lo que pide la consigna de la pre-entrega 7: encolar sin bloquear, ver los estados en vivo, la aprobación humana y la trazabilidad. No conoce el código; no quiere leer el README para entender qué mirar.

## Product Purpose

Una consola para operar la API del orquestador multi-agente de Tambor: mandar un pedido, verlo pasar por PENDING, RUNNING, WAITING_APPROVAL, DONE o FAILED, leer la respuesta y el trabajo de cada agente, y aprobar o rechazar un reembolso. Éxito: el corrector ve el sistema funcionando de punta a punta sin usar curl.

## Positioning

No es un chat. Es el tablero de una cola de trabajos asíncrona: el pedido no se responde en el momento, se encola, lo toma un worker, pasa por un supervisor y dos especialistas, y si mueve plata se frena hasta que una persona lo aprueba. La consola tiene que hacer visible ese recorrido, que es lo que distingue a esta entrega.

## Operating Context

- Corre en `localhost:8000`, al lado de Phoenix en `localhost:6006` (las trazas de cada trabajo).
- Datos de Tambor, empresa ficticia de cobros: comercios (Ferretería López, Librería Andina, Café del Puerto, Farmacia López Hermanos, Vivero Las Lomas), cobros con IDs `cob_XXXXX`, códigos de error `TMB-XXXX`.
- Un pedido tarda entre 15 y 60 segundos con `claude-haiku-4-5` y cuesta entre 0,06 y 0,09 USD.
- La API no tiene un endpoint para listar trabajos: la consola recuerda los IDs que mandó (en el navegador).

## Capabilities and Constraints

- Endpoints: `POST /tasks`, `GET /tasks/{id}`, `POST /tasks/{id}/approve`, `GET /health`.
- Un trabajo trae: estado, pedido, fechas, `aprobacion` (acción, análisis, pregunta) cuando espera, `resultado` (respuesta final en markdown, aportes de agentes, decisiones del supervisor, revisión, resultado de la acción) o `error`.
- La aprobación humana solo se dispara con un verbo de reembolso más IDs `cob_XXXXX` escritos en el pedido.
- Todo en español rioplatense, como el resto del proyecto.

## Brand Commitments

Tambor, plataforma de cobros ficticia. No hay logo ni paleta previos.

## Evidence on Hand

Corridas reales en `evidencia/` y capturas de Phoenix en `screenshots/`. No hay usuarios, clientes ni métricas reales de Tambor: no inventar ninguna.

## Product Principles

1. El estado del trabajo es lo primero que se lee.
2. Mostrar el recorrido (quién trabajó, qué decidió el supervisor), no solo la respuesta.
3. La aprobación humana es un momento serio: datos a la vista antes de decidir.
4. Nada de lo que muestra la consola puede contradecir lo que devuelve la API.
