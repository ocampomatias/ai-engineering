# Referencia de la API de cobros

Documento para los comercios que integran con Tambor (empresa ficticia). Describe cómo autenticarse,
los endpoints principales, los límites de uso y cómo verificar los webhooks. La versión vigente de
la API es la `v2`.

## Autenticación

Cada request lleva la clave de API del comercio en el header `X-Tambor-Key`. Hay dos tipos de clave:
las que empiezan con `tk_test_` operan contra el entorno de pruebas (sandbox) y nunca mueven dinero,
y las que empiezan con `tk_live_` operan en producción. Una clave de producción usada contra el
sandbox, o al revés, devuelve el error `TMB-1003`.

Las claves se crean y se rotan desde el panel de comercios, en la sección "Desarrolladores". Un
comercio puede tener hasta 5 claves activas al mismo tiempo, así puede rotar una sin cortar el
servicio.

## Endpoints principales

| Método | Ruta | Qué hace |
|---|---|---|
| `POST` | `/v2/cobros` | Crea un cobro. Responde `201` con el identificador y el estado `pendiente` |
| `GET` | `/v2/cobros/{id}` | Devuelve el estado actual de un cobro |
| `GET` | `/v2/cobros` | Lista cobros, paginados de a 50, filtrables por fecha y estado |
| `POST` | `/v2/cobros/{id}/reembolsos` | Reembolsa un cobro aprobado, en forma total o parcial |
| `POST` | `/v2/cobros/{id}/cancelacion` | Cancela un cobro que sigue `pendiente` |

Un cobro se puede reembolsar durante 180 días desde que se aprobó. Se permiten hasta 10 reembolsos
parciales por cobro, y la suma de los reembolsos nunca puede superar el monto original.

La paginación usa cursores: cada respuesta de `GET /v2/cobros` incluye el campo `siguiente`, que se
manda como parámetro `cursor` para pedir la página siguiente. No hay paginación por número de página.

## Idempotencia

Los `POST` aceptan el header `Idempotency-Key`, un valor único que genera el comercio (se recomienda
un UUID v4). Si el comercio repite un pedido con la misma clave dentro de las 24 horas, la API
devuelve la respuesta original en vez de crear un cobro nuevo. Así, un reintento por un corte de red
no termina en un cobro duplicado.

Reusar una `Idempotency-Key` con un cuerpo distinto al del pedido original devuelve `409 Conflict`
con el error `TMB-1009`.

## Límites de uso

Cada comercio puede hacer hasta 300 requests por minuto contra la API, contando todos los endpoints.
Los endpoints de listado (`GET /v2/cobros`) tienen además un límite propio de 60 requests por minuto.
Al superar un límite, la API responde `429 Too Many Requests` con el error `TMB-1429` y el header
`Retry-After`, que indica cuántos segundos esperar antes de reintentar.

Los comercios del plan Empresa pueden pedir un límite mayor, de hasta 1.200 requests por minuto, con
un ticket a Atención a Comercios.

## Webhooks

Cuando un cobro cambia de estado, Tambor envía un `POST` a la URL de webhook que el comercio
configuró en el panel. El comercio tiene que responder con un código `2xx` en menos de 10 segundos;
cualquier otra respuesta, o un timeout, cuenta como fallo y el webhook se reintenta.

Cada webhook viene firmado en el header `X-Tambor-Firma`, que es un HMAC-SHA256 del cuerpo del
mensaje calculado con el secreto de webhook del comercio. El header `X-Tambor-Timestamp` lleva la
hora del envío: el comercio debería rechazar un webhook con más de 5 minutos de antigüedad, para
evitar que alguien reenvíe un mensaje viejo capturado (ataque de repetición).

Los eventos que se envían son `cobro.aprobado`, `cobro.rechazado`, `cobro.reembolsado` y
`cobro.contracargo`.

## Versiones

La `v1` de la API está deprecada y deja de funcionar el 31 de marzo de 2027. Desde el 1 de octubre de
2026, las respuestas de la `v1` incluyen el header `Deprecation` con la fecha de baja. Las
diferencias principales con la `v2` son los montos, que en la `v2` se expresan en centavos como
número entero y no como decimal, y la paginación por cursores.
