# Arquitectura de la plataforma de cobros de Tambor

Documento interno del equipo de Plataforma. Última revisión: agosto de 2026. Tambor es una empresa
ficticia creada para este proyecto; ningún dato de este documento corresponde a una empresa real.

## Visión general

La plataforma de cobros procesa pagos con tarjeta y transferencias para comercios medianos. En un día
normal entran unos 180.000 pagos, con picos de 45 pagos por segundo entre las 19:00 y las 22:00. El
objetivo de disponibilidad (SLO) de la API de cobros es 99,95 % mensual, medido como el porcentaje de
requests que responden sin error 5xx en menos de 800 ms.

El sistema está dividido en cinco servicios. Cada uno tiene su propio repositorio, su propio pipeline
de CI y un equipo dueño responsable de las guardias.

| Servicio | Lenguaje | Qué hace | Equipo dueño |
|---|---|---|---|
| `api-cobros` | Python 3.12, FastAPI | Recibe los pedidos de pago de los comercios | Cobros |
| `payments-worker` | Python 3.12, Celery | Procesa los pagos contra los adquirentes | Cobros |
| `conciliador` | Go 1.23 | Cruza lo cobrado con lo liquidado por los bancos | Finanzas técnicas |
| `notificador` | Node.js 22 | Envía webhooks y correos a los comercios | Integraciones |
| `panel-comercios` | TypeScript, React | Panel web donde el comercio ve sus ventas | Producto |

## Flujo de un pago

1. El comercio llama a `POST /v2/cobros` en `api-cobros`. La API valida el pedido, lo guarda en
   PostgreSQL con estado `pendiente` y devuelve un identificador de cobro en menos de 150 ms.
2. La API publica un mensaje en la cola `payments` de RabbitMQ con el identificador del cobro. No
   publica los datos de la tarjeta: el número de tarjeta nunca sale del servicio de tokenización.
3. Un `payments-worker` toma el mensaje, pide la autorización al adquirente y actualiza el estado a
   `aprobado` o `rechazado`.
4. El `notificador` escucha los cambios de estado y envía el webhook al comercio. Si el webhook
   falla, reintenta con espera exponencial durante 24 horas: a los 1, 5, 30 minutos y después cada
   2 horas.
5. Una vez por día, a las 04:00, el `conciliador` compara los pagos aprobados con los archivos de
   liquidación de los bancos y marca las diferencias para revisión manual.

## Datos y almacenamiento

La base principal es PostgreSQL 16 en un clúster con un nodo primario y dos réplicas de lectura.
Las escrituras van siempre al primario. Los reportes del `panel-comercios` leen de las réplicas, que
pueden tener hasta 5 segundos de retraso respecto del primario.

Las aplicaciones no se conectan directo a PostgreSQL: pasan por PgBouncer en modo `transaction`.
El pool de PgBouncer tiene un tamaño de 40 conexiones por base de datos y un máximo de 2.000
conexiones de clientes. El primario acepta como máximo 200 conexiones reales; el resto de la
capacidad queda reservada para tareas de mantenimiento y para las réplicas.

Redis 7 se usa para dos cosas: la caché de sesiones del panel (con expiración de 30 minutos) y las
claves de idempotencia de la API, que se guardan durante 24 horas. Una clave de idempotencia evita
que un comercio que repite el mismo pedido por un error de red termine cobrando dos veces.

Los backups de PostgreSQL son completos todos los días a las 02:00 y además hay archivado continuo
de WAL, lo que permite restaurar la base a cualquier minuto de los últimos 14 días. Los backups se
guardan cifrados en un bucket de otra región.

## Colas y procesamiento asíncrono

RabbitMQ 3.13 corre en un clúster de tres nodos con colas de tipo quorum. La cola `payments` tiene
un límite de 50.000 mensajes; si se llena, la API deja de aceptar cobros nuevos y responde 503 para
proteger la base de datos. Los mensajes que fallan cinco veces seguidas pasan a la cola
`payments.dlq` (dead letter queue) y requieren revisión manual.

Los `payments-worker` escalan de forma automática entre 4 y 24 réplicas según la profundidad de la
cola: se agrega una réplica por cada 500 mensajes pendientes. Cada worker procesa como máximo 8
tareas en paralelo (`worker_concurrency = 8`).

## Infraestructura

Todo corre en Kubernetes sobre AWS, en la región `sa-east-1` (São Paulo), repartido en tres zonas
de disponibilidad. Hay tres entornos: `dev`, `staging` y `produccion`. Staging tiene los mismos
servicios que producción pero con la base de datos anonimizada y un solo nodo por componente.

La infraestructura se declara con Terraform y los manifiestos de Kubernetes se aplican con Argo CD.
Ningún cambio de infraestructura se hace a mano desde la consola de AWS: si alguien lo hace durante
un incidente, tiene que reflejarlo en Terraform dentro de las 48 horas siguientes.

## Observabilidad

Las métricas se recolectan con Prometheus y se ven en Grafana. Los logs se envían a Loki y se
conservan 30 días en caliente. Las trazas distribuidas usan OpenTelemetry. Cada request que entra a
`api-cobros` recibe un `trace_id` que viaja en los mensajes de RabbitMQ, así que un pago se puede
seguir de punta a punta aunque pase por tres servicios.

Las alertas se envían a PagerDuty. Solo generan una llamada a la persona de guardia las alertas que
afectan a los comercios; el resto llega como mensaje al canal `#plataforma-alertas`.
