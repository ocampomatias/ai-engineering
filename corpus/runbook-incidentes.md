# Runbook de incidentes de la plataforma de cobros

Documento interno de Tambor (empresa ficticia). Este runbook describe cómo se clasifica un
incidente, quién responde y qué pasos seguir en los problemas más frecuentes.

## Niveles de severidad

| Nivel | Cuándo se usa | Tiempo de respuesta | Quién se suma |
|---|---|---|---|
| SEV1 | La API de cobros no procesa pagos o hay riesgo de cobro duplicado | 5 minutos | Guardia, líder técnico y gerente de ingeniería |
| SEV2 | Degradación: latencia alta, errores parciales o webhooks demorados más de 30 minutos | 15 minutos | Guardia y líder técnico |
| SEV3 | Problema sin impacto directo en comercios, como un reporte interno que falla | Siguiente día hábil | Guardia |

Ante la duda entre dos niveles, se elige el más alto. Bajar la severidad después no tiene costo;
subirla tarde sí.

## Guardias

La guardia es semanal y rota entre las personas del equipo dueño del servicio afectado. El cambio de
guardia es los lunes a las 10:00, con una reunión de 15 minutos donde quien sale le cuenta a quien
entra los temas abiertos. La persona de guardia tiene que poder estar frente a una computadora en
menos de 15 minutos a cualquier hora.

Si la persona de guardia no reconoce la alerta en PagerDuty en 10 minutos, la alerta escala sola a
la guardia secundaria, y a los 20 minutos al líder técnico.

## Roles durante un incidente SEV1 o SEV2

- **Coordinador del incidente**: toma las decisiones y reparte las tareas. No investiga: coordina.
  Por defecto es la persona de guardia hasta que llegue el líder técnico.
- **Responsable de comunicación**: actualiza la página de estado y el canal `#incidentes` cada 30
  minutos, aunque no haya novedades. En un SEV1 también avisa a Atención a Comercios.
- **Investigadores**: el resto de las personas convocadas. Reportan al coordinador, no entre ellos
  por mensajes privados.

Todo incidente SEV1 o SEV2 se abre con el comando `/incidente nuevo` en Slack, que crea el canal
dedicado, el documento de seguimiento y el registro de tiempos.

## Procedimiento: la cola de pagos crece sin parar

Síntoma: la alerta `RabbitMQPaymentsBacklog` se dispara cuando la cola `payments` supera los 1.000
mensajes durante más de 5 minutos.

1. Revisar en Grafana el panel "Workers de pagos". Si hay menos de 24 réplicas, confirmar que el
   autoescalado está funcionando; si está trabado, escalar a mano con
   `kubectl scale deployment payments-worker --replicas=24 -n cobros`.
2. Revisar la tasa de error de los workers. Si más del 20 % de las tareas falla, el problema no es
   de capacidad: agregar workers solo multiplica los errores. Pasar al paso 3.
3. Mirar los logs de los workers en Loki filtrando por `level=error`. Las dos causas más comunes son
   la falta de conexiones a PostgreSQL (ver el procedimiento siguiente) y timeouts del adquirente.
4. Si el adquirente responde con timeouts, activar el adquirente de respaldo desde el feature flag
   `adquirente_respaldo_activo`. El cambio tarda unos 2 minutos en propagarse.
5. Si la cola supera los 40.000 mensajes, declarar SEV1: a los 50.000 la API deja de aceptar cobros.

## Procedimiento: se agotan las conexiones a PostgreSQL

Síntoma: errores `remaining connection slots are reserved` o `sorry, too many clients already` en
los logs, o la alerta `PgBouncerPoolSaturado`.

1. Confirmar en Grafana, panel "PgBouncer", si el pool de 40 conexiones está completo y cuántos
   clientes esperan (`cl_waiting`).
2. Buscar transacciones largas con la consulta guardada `transacciones_largas` en la réplica de
   administración. Una transacción abierta más de 60 segundos casi siempre es la causa.
3. Se puede cancelar una consulta con `pg_cancel_backend`. Solo el líder técnico o la guardia de
   base de datos pueden terminar una conexión con `pg_terminate_backend`, porque corta la
   transacción a la mitad.
4. No aumentar el tamaño del pool de PgBouncer por encima de 60 sin hablar con la guardia de base de
   datos: el primario acepta 200 conexiones reales y hay que dejar margen para mantenimiento.

## Procedimiento: webhooks demorados

Si los webhooks a comercios tienen más de 30 minutos de demora, es un SEV2. Revisar primero si el
problema es de un solo comercio (su servidor responde con error) o general. Si es de un solo
comercio, no es un incidente: se abre un ticket para Atención a Comercios y el `notificador` sigue
reintentando solo durante 24 horas.

## Después del incidente

Todo SEV1 y SEV2 lleva un postmortem sin culpables. El borrador se escribe dentro de los 3 días
hábiles posteriores al cierre, y la reunión de revisión se hace dentro de los 5 días hábiles. Cada
acción correctiva tiene una persona responsable y una fecha. El postmortem describe qué pasó, el
impacto en comercios (cantidad de pagos afectados y minutos de indisponibilidad), la línea de
tiempo, la causa raíz y las acciones.
