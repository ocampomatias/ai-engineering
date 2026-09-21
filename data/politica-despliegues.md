# Política de despliegues

Documento interno de Tambor (empresa ficticia). Define cuándo y cómo se lleva un cambio a
producción, y cuándo se revierte.

## Principios

Los despliegues son frecuentes y chicos. Un cambio que toca más de 400 líneas de código de
producción (sin contar tests) tiene que dividirse o justificar por escrito por qué no se puede. Un
despliegue chico se revisa mejor, falla menos y, si falla, se entiende rápido qué lo rompió.

Todo cambio llega a producción por el pipeline. Nadie despliega desde su computadora.

## Requisitos antes de desplegar

Un cambio puede ir a producción solo si cumple todo esto:

1. El pull request tiene la aprobación de al menos una persona del equipo dueño del servicio. Si el
   cambio toca pagos, migraciones de base de datos o permisos, necesita dos aprobaciones.
2. El pipeline de CI pasó completo: tests unitarios, tests de integración, análisis estático y
   escaneo de dependencias.
3. El cambio estuvo desplegado en staging al menos 30 minutos sin alertas nuevas.
4. Si incluye una migración de base de datos, la migración es compatible hacia atrás: la versión
   anterior del código tiene que seguir funcionando con el esquema nuevo. Las columnas se borran en
   un despliegue posterior, nunca en el mismo que deja de usarlas.

## Ventanas de despliegue

Se puede desplegar a producción de lunes a jueves entre las 10:00 y las 17:00, y los viernes entre
las 10:00 y las 15:00 (hora de Buenos Aires). Fuera de esos horarios no se despliega, porque hay
menos gente disponible para responder si algo sale mal.

Tampoco se despliega durante los períodos de congelamiento: la semana del Hot Sale, la semana del
Black Friday y del 20 de diciembre al 6 de enero. Las fechas exactas de cada año se publican en el
calendario del equipo de Plataforma con un mes de anticipación.

La única excepción a las ventanas y a los congelamientos es un hotfix que resuelve un incidente SEV1
abierto. Ese hotfix necesita la aprobación del coordinador del incidente y del gerente de
ingeniería, y se registra en el documento del incidente.

## Despliegue progresivo (canary)

Los servicios que atienden tráfico de comercios (`api-cobros`, `payments-worker` y `notificador`) se
despliegan en canary:

1. La versión nueva recibe el 5 % del tráfico durante 30 minutos.
2. Si las métricas están bien, pasa al 25 % durante 15 minutos.
3. Después pasa al 100 %.

Argo Rollouts compara automáticamente la versión nueva con la anterior en cada paso. El canary se
aborta solo y vuelve a la versión anterior si pasa cualquiera de estas cosas:

- la tasa de errores 5xx de la versión nueva supera el 2 %,
- la latencia p99 de la versión nueva es más de un 30 % mayor que la de la versión anterior,
- la tasa de pagos rechazados sube más de 5 puntos porcentuales respecto de la versión anterior.

## Rollback manual

Cualquier persona del equipo puede pedir un rollback, y quien está desplegando lo ejecuta sin
discutir: primero se revierte y después se investiga. El rollback se hace con
`argocd app rollback <servicio>` y tiene que quedar completo en menos de 10 minutos.

Si el despliegue incluía una migración de base de datos, no se revierte la migración: por la regla
de compatibilidad hacia atrás, el código anterior funciona con el esquema nuevo. Revertir una
migración requiere un plan escrito aprobado por la guardia de base de datos.

## Feature flags

Las funcionalidades nuevas que cambian el comportamiento para los comercios salen apagadas detrás
de un feature flag y se encienden de forma gradual. Un flag que lleva más de 90 días encendido al
100 % se considera deuda: el equipo dueño tiene que borrarlo del código en el sprint siguiente.

## Registro

Cada despliegue a producción queda registrado automáticamente en el canal `#despliegues` con el
servicio, la versión, la persona que lo aprobó y el enlace al pull request. Ese registro es lo
primero que se mira cuando empieza un incidente: la mayoría de los incidentes empiezan poco después
de un cambio.
