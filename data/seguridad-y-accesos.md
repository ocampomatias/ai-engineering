# Seguridad y accesos

Documento interno de Tambor (empresa ficticia). Resume las reglas de acceso a los sistemas y el
manejo de secretos y datos sensibles. Como la plataforma procesa tarjetas, estas reglas siguen los
requisitos de PCI DSS.

## Datos de tarjetas

El número completo de una tarjeta (PAN) solo existe dentro del servicio de tokenización, que corre
en un clúster de Kubernetes separado y en una red aislada. El resto de los servicios trabaja con un
token que no permite reconstruir el número. En los logs, las pantallas y las bases de datos fuera de
la tokenización solo pueden aparecer los primeros 6 y los últimos 4 dígitos.

El código de seguridad (CVV) no se guarda nunca, ni siquiera cifrado. Se usa para la autorización y
se descarta.

Si alguien encuentra un número de tarjeta completo en un log, un ticket o un mensaje de Slack, lo
trata como incidente de seguridad SEV1: avisa a Seguridad por el canal `#seguridad-urgente` y no
copia el dato en ningún otro lugar para "mostrarlo".

## Acceso a producción

Nadie tiene acceso permanente a producción. El acceso se pide por el sistema de accesos temporales
con un motivo y un ticket asociado, y dura como máximo 4 horas. La guardia puede aprobar su propio
acceso durante un incidente SEV1 o SEV2; en cualquier otro caso lo aprueba el líder técnico.

La conexión a los servidores y a la base de datos de producción se hace únicamente a través del
bastión, con autenticación de dos factores por llave física (FIDO2). Las sesiones en el bastión se
graban y se conservan 1 año.

Acceder a la base de datos de producción no habilita a modificar datos a mano. Una corrección de
datos se hace con un script revisado en un pull request y aprobado por dos personas, igual que un
cambio de código.

## Secretos

Los secretos (contraseñas de bases de datos, claves de adquirentes, tokens de terceros) se guardan
en AWS Secrets Manager y se inyectan en los pods al arrancar. Nunca se guardan en el repositorio,
en variables de entorno de CI visibles ni en archivos `.env` compartidos.

Los secretos se rotan cada 90 días de forma automática. Si un secreto se expone (por ejemplo, si
aparece en un commit, aunque el repositorio sea privado), se rota en el momento, sin esperar al
ciclo, y se revisan los accesos hechos con ese secreto desde que quedó expuesto.

Las claves de API que usan los comercios para llamar a la plataforma no vencen, pero el comercio
puede rotarlas desde el panel. Una clave que no se usó en 180 días se desactiva y se le avisa al
comercio por correo con 15 días de anticipación.

## Cuentas y permisos

Los permisos se asignan por rol y no por persona. Al entrar a la empresa, cada persona recibe los
permisos de su rol; cualquier permiso adicional se pide con un ticket y vence a los 30 días si no se
renueva.

Cuando una persona deja la empresa, sus accesos se revocan el mismo día, antes de las 18:00. El
equipo de Seguridad revisa todos los permisos una vez por trimestre y quita los que no se usaron en
los últimos 90 días.

## Registros de auditoría

Toda acción sobre datos de comercios o de pagos queda registrada en el log de auditoría: quién,
cuándo, desde dónde y qué cambió. El log de auditoría es de solo escritura, está separado de los
logs de aplicación y se conserva 400 días, más de lo que exige PCI DSS (un año).

## Dependencias y vulnerabilidades

El pipeline de CI escanea las dependencias de cada servicio. Una vulnerabilidad crítica bloquea el
despliegue hasta que se actualiza la dependencia. Los plazos para corregir una vulnerabilidad ya
desplegada son: 7 días para las críticas, 30 días para las altas y 90 días para las medias.

## Reporte de problemas de seguridad

Cualquier persona que sospeche un problema de seguridad lo reporta en `#seguridad-urgente` o por
correo al equipo de Seguridad. Reportar algo que después resulta no ser un problema no tiene ninguna
consecuencia negativa: se prefiere cien falsas alarmas a un incidente no reportado.
