# Despliegue y validación controlada de WhatsApp

Guía para el operador. No se ha ejecutado ningún comando de producción por preparar este archivo. Sustituir rutas y opciones por las verificadas en el servidor; no pegar secretos en el chat, en archivos del repositorio ni en videos.

## Antes de desplegar

1. Confirmar el repositorio, la rama/commit revisado, el entorno virtual, el servicio web, el archivo de configuración y el motor de base de datos reales del servidor.
2. Separar los cambios de esta integración de modificaciones ajenas. Revisar el diff completo que se va a publicar.
3. Ejecutar las pruebas locales relevantes con una base de pruebas aislada. El pase de mocks demuestra comportamiento del código, no aprobación de Meta ni entrega real.

   Comando local aislado: `venv\Scripts\python.exe manage.py test crm --settings=config.settings_whatsapp_tests --verbosity 1`. Este perfil fuerza SQLite en memoria y correo de prueba. No usarlo para el servicio productivo. La prueba de bloqueo entre conexiones simultáneas requiere una base PostgreSQL de pruebas independiente; se omite en SQLite.

   El registro del navegador también tiene una prueba sin red: `node crm/test_whatsapp_signup.js`, que ejecuta el script real de la plantilla con eventos sintéticos y comprueba orden de callbacks, duplicados, origen, cancelación y selección del flujo.
4. Confirmar el plan de migraciones. Obtener un respaldo verificable de base de datos y configuración, con acceso restringido. Comprobar el procedimiento de restauración antes de necesitarlo.
5. Pausar los envíos de la empresa piloto antes de guardar sus credenciales reales. Conservar el funcionamiento autorizado de las otras empresas. Si se necesita pausar un timer global, coordinar su alcance y la ventana, y verificar el resultado.

## Script de despliegue existente

`deploy/deploy_crm_whatsapp.sh` detecta `.env.production` o `/etc/dvsolutions-erp.env`, detecta `.venv` o `venv`, respalda PostgreSQL, ejecuta comprobación/migraciones/estáticos y reinicia `dvsolutions.service`.

La versión revisada al preparar esta guía también hace `git pull` al principio, instala dependencias y habilita inmediatamente `dvsolutions-crm-automatizaciones.timer`. Ese timer ejecuta `procesar_recordatorios_citas` cada cinco minutos. Por ello no se debe ejecutar el script a ciegas como si fuera una simple comprobación: verificar primero la pausa del negocio piloto, el cambio exacto y el plan de migraciones. Si el script cambia en esta tarea, revisar su versión final antes de usarlo.

## Configuración privada del servidor

Mantener los valores únicamente en el archivo de entorno que consume el servicio:

- `META_WHATSAPP_APP_ID`: identificador público de la app correcta.
- `META_WHATSAPP_CONFIG_ID`: configuración vigente de Facebook Login for Business/Embedded Signup verificada en Meta.
- `META_WHATSAPP_APP_SECRET`: secreto vigente de la app, nunca enviado al navegador.
- `META_WHATSAPP_GRAPH_VERSION`: versión soportada y compatible con las llamadas de conexión y envío.
- `META_WHATSAPP_VERIFY_TOKEN`: valor aleatorio privado para comprobar el challenge inicial del webhook; usar exactamente el mismo en el formulario de suscripción de Meta. No es el access token de WhatsApp ni el app secret.
- `PUBLIC_BASE_URL`: origen HTTPS real utilizado por los enlaces firmados y los callbacks públicos.

El callback público de esta entrega es `https://dvsolutionshn.com/integraciones/whatsapp/webhook/` para el origen productivo indicado. En otro entorno, conservar la ruta `/integraciones/whatsapp/webhook/` bajo su propio origen HTTPS. El setting del challenge es `META_WHATSAPP_VERIFY_TOKEN`. Comprobar ambos contra la versión finalmente desplegada antes de configurar Meta.

El configurador existente `deploy/configurar_meta_secret.sh` solo gestiona el app secret. No suponer que añade automáticamente el verify token ni los otros valores.

## Orden de publicación

1. Guardar y comprobar los respaldos privados.
2. Instalar exclusivamente la revisión aprobada y sus dependencias.
3. Ejecutar `manage.py check --deploy` con el entorno correcto; revisar cada aviso relevante.
4. Ejecutar `manage.py migrate --plan` y verificar que la migración nueva pertenece a esta entrega.
5. Aplicar las migraciones aprobadas y recolectar estáticos.
6. Recargar/reiniciar el servicio en la ventana acordada y comprobar su salud y las rutas existentes.
7. Verificar por HTTPS la ruta de webhook nueva: GET challenge con token válido; POST sin firma debe rechazarse. El endpoint no debe pedir inicio de sesión del ERP ni requerir CSRF del navegador; debe exigir la firma de Meta para los eventos.
8. Configurar callback, verify token y campo `messages` en la app Meta correcta. Confirmar la suscripción a la cuenta autorizada mediante el flujo implementado.
9. Conectar únicamente los activos autorizados de demostración. Una conexión nueva debe quedar pendiente de validación y con `whatsapp_activo=False`; los interruptores de recordatorios se conservan, pero no deben enviar mientras WhatsApp esté pausado.

## Prueba de salida y recepción

1. Comprobar que el token permite los activos seleccionados y que el número pertenece a la cuenta autorizada.
2. Comprobar plantilla `APPROVED`, idioma y variables. Un nombre guardado en el CRM no acredita aprobación.
3. Comprobar que el número está utilizable y que Meta no pide verificación, pago, registro o un paso adicional. El registro de un número nuevo y la coexistencia son flujos distintos; no ejecutar migración ni `/register` indiscriminadamente.
4. Ejecutar un envío único al destinatario autorizado. La prueba debe guardar el `wamid` devuelto; no exponer el token.
5. Verificar recepción en WhatsApp y el evento de entrega en el CRM. La aceptación por API y el estado `sent` no equivalen a `delivered`/`read`.
6. Responder con texto de demostración y comprobar que la recepción queda en la empresa correcta. La bandeja de esta entrega se consulta desde la configuración CRM, ruta relativa `configuracion/whatsapp/mensajes/`. Las respuestas ambiguas no deben confirmar una cita automáticamente.
7. Probar que un evento duplicado no duplica mensajes y que eventos de otra cuenta o número no aparecen en Hospital Mía.
8. Verificar que la activación exige el evento `delivered` o `read` correspondiente al `wamid` de prueba. Si los recordatorios de citas están habilitados para el negocio, la plantilla de citas también debe estar aprobada. Revisar la cola pendiente antes de activar. No habilitar el timer ni usar campañas masivas durante estas comprobaciones.

## Criterios de salida

La integración se puede presentar como desplegada y probada cuando se confirme el commit, las migraciones aplicadas, el servicio saludable y el recorrido real de envío/recepción con activos autorizados. La aprobación de App Review, la verificación de acceso y la publicación de la app deben informarse por su estado efectivo en Meta, de forma independiente.

Si algo falla, mantener pausados los envíos del piloto, conservar la evidencia técnica sin secretos y aplicar el plan de recuperación acordado. No revertir migraciones ni sobrescribir datos productivos mediante comandos genéricos.
