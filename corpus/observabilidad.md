# Guía de observabilidad y alertas

Documento interno del equipo de Plataforma de Tambor (empresa ficticia). Describe qué se mide, dónde
se mira y qué alertas existen. Complementa el runbook de incidentes, que dice qué hacer cuando una
alerta se dispara.

## Qué medimos (SLI)

La plataforma tiene tres indicadores de nivel de servicio. Todos se calculan sobre ventanas de 30
días:

- **Disponibilidad de la API**: porcentaje de requests a `api-cobros` que responden sin error 5xx en
  menos de 800 ms. El objetivo (SLO) es 99,95 %.
- **Frescura de los webhooks**: porcentaje de webhooks entregados dentro de los 60 segundos desde el
  cambio de estado del cobro. El objetivo es 99 %.
- **Conciliación a tiempo**: porcentaje de días en que la conciliación termina antes de las 08:00. El
  objetivo es 95 %.

## Presupuesto de errores

Un SLO de 99,95 % deja un presupuesto de errores de unos 21 minutos de indisponibilidad por mes. El
presupuesto se revisa todos los lunes en la reunión de Plataforma.

Si en los primeros 15 días del período ya se consumió más de la mitad del presupuesto, el equipo
dueño del servicio congela las funcionalidades nuevas y dedica el trabajo a confiabilidad hasta que
el consumo vuelva a estar por debajo de la línea esperada. El congelamiento lo declara el líder
técnico y queda registrado en el canal `#plataforma`.

## Métricas, logs y trazas

Las métricas se guardan en Prometheus con una resolución de 15 segundos durante 15 días. Después se
agregan a resolución de 5 minutos y se conservan 13 meses en Thanos, para poder comparar con el mismo
mes del año anterior.

Los logs van a Loki: 30 días en caliente para consultas rápidas y después 1 año archivados en S3,
donde se pueden recuperar con un pedido al equipo de Plataforma. Los logs siempre se escriben en
formato JSON y llevan el campo `trace_id`.

Las trazas se toman con OpenTelemetry. Se guarda el 10 % de las trazas normales y el 100 % de las
que terminaron con error o tardaron más de 2 segundos. Las trazas se conservan 7 días en Tempo.

## Dashboards

Los paneles de Grafana que más se usan son:

- "Salud de cobros": requests por segundo, errores 5xx y latencia p50, p95 y p99 de `api-cobros`.
- "Workers de pagos": profundidad de la cola `payments`, réplicas activas y tasa de error.
- "PgBouncer": conexiones en uso del pool, clientes en espera (`cl_waiting`) y tiempo de espera.
- "Presupuesto de errores": consumo del mes por SLO, con la línea de consumo esperado.

Un dashboard nuevo se crea como código en el repositorio `observabilidad`, no a mano desde la
interfaz de Grafana. Los que se crean a mano se borran automáticamente a los 30 días.

## Catálogo de alertas

| Alerta | Condición | Severidad | A quién llama |
|---|---|---|---|
| `ApiCobrosLatenciaP99Alta` | p99 de `api-cobros` mayor a 800 ms durante 10 minutos | SEV2 | Guardia de Cobros |
| `ApiCobrosErrores5xx` | más del 1 % de errores 5xx durante 5 minutos | SEV1 | Guardia de Cobros |
| `RabbitMQPaymentsBacklog` | cola `payments` con más de 1.000 mensajes durante 5 minutos | SEV2 | Guardia de Cobros |
| `PgBouncerPoolSaturado` | pool completo y más de 20 clientes esperando durante 3 minutos | SEV2 | Guardia de base de datos |
| `WebhooksDemorados` | más de 500 webhooks pendientes con más de 30 minutos de demora | SEV2 | Guardia de Integraciones |
| `ConciliadorSinArchivo` | a las 05:00 no llegó el archivo de liquidación de algún banco | SEV3 | Finanzas técnicas (en horario hábil) |
| `CertificadoPorVencer` | un certificado TLS vence en menos de 14 días | SEV3 | Guardia de Plataforma |

Toda alerta tiene que tener un enlace a su procedimiento en el runbook. Una alerta sin procedimiento
no se puede activar en producción.

## Revisión de alertas ruidosas

Una alerta que se disparó más de 5 veces en un mes sin requerir ninguna acción se considera ruidosa.
En la revisión mensual de alertas, el equipo dueño decide si la ajusta, la baja de severidad o la
borra. El objetivo es que cada llamada a la guardia sea por algo que requiere hacer algo.
