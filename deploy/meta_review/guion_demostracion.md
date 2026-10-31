# Grabación real para Meta

Este archivo es un guion. No es evidencia de ejecución y no reemplaza los archivos de video solicitados por Meta.

## Preparación de la demostración

- Usar un espacio de demostración separado de pacientes reales, con un contacto ficticio como `Paciente Demo` y una cita ficticia.
- Usar un número receptor real controlado por el equipo, con consentimiento para la prueba. El nombre ficticio no convierte un número aleatorio en un destinatario autorizado.
- Hospital Mía conservará WhatsApp Business en el teléfono por decisión confirmada del usuario: preparar la demostración con coexistencia solo si Meta declara elegible el activo. El administrador autorizado debe completar el código o QR con el teléfono disponible. No demostrar ni prometer sincronización de historial o eventos de eco del teléfono, que no están implementados en esta entrega.
- Mantener pausados recordatorios y campañas. No usar una cita real ni disparar la cola del hospital para grabar.
- Preparar una plantilla de la cuenta autorizada, aprobada en el idioma seleccionado. Validar el número y orden de variables. `hello_world` no acredita la disponibilidad de las plantillas del hospital.
- Comprobar que la versión desplegada coincide con la que pasó las pruebas y que el usuario de revisión solo ve datos de demostración.
- Ocultar las claves, tokens, cookies, códigos de autenticación y datos personales ajenos. Nunca grabar el secreto de la app, el intercambio OAuth ni el archivo de entorno.
- Hacer una grabación continua legible desde el acceso al CRM hasta la recepción del mensaje; no utilizar un montaje que simule éxito.

## Video de `whatsapp_business_messaging`

| Escena | Acción real | Evidencia que debe verse |
| --- | --- | --- |
| 1. Contexto | Mostrar el nombre de DV Solutions CRM y explicar que sirve a empresas autorizadas | Nombre del producto, espacio de demostración y propósito de recordatorio |
| 2. Acceso | Iniciar sesión con el usuario de demostración | Acceso al CRM y empresa seleccionada; no mostrar la contraseña |
| 3. Conexión | Abrir la configuración del CRM y mostrar el flujo oficial Meta que funciona para los activos de demostración | Empresa y cuenta autorizadas; número seleccionado; permiso concedido por el administrador |
| 4. Revisión | Mostrar el número conectado, el idioma y la plantilla aprobada | El número de origen es de la empresa de demostración; no el test number si se está afirmando usar uno real |
| 5. Preparación | Crear/abrir la cita ficticia y mostrar el mensaje que se enviará | Destinatario autorizado, variables de cita correctas y automatizaciones pausadas |
| 6. Envío único | Ejecutar la acción de prueba disponible en la versión presentada | Resultado real de la API y el identificador del mensaje, sin tokens |
| 7. Recepción | Mostrar WhatsApp del destinatario autorizado | Mensaje recibido con el mismo contenido que se envió |
| 8. Estado | Mostrar en el CRM el estado realmente recibido para el `wamid` de la prueba | Diferenciar aceptación por API de entrega/lectura; la activación requiere `delivered` o `read` |
| 9. Respuesta | Solo si funciona en la versión presentada, responder desde el destinatario y abrir la bandeja desde Configuración CRM → mensajes WhatsApp | Mensaje entrante visible en la empresa correcta |

Narración breve ES: «DV Solutions permite que el personal autorizado de cada empresa envíe recordatorios desde su propia cuenta de WhatsApp. En esta prueba usamos datos ficticios y un único destinatario autorizado. La cuenta de la empresa queda vinculada a su espacio de CRM.»

Brief EN narration: “DV Solutions lets authorized business staff send reminders from their own WhatsApp account. This test uses fictional data and a single authorized recipient. The business's account is linked to its own CRM workspace.”

## Video de `whatsapp_business_management`

1. Mostrar el espacio del negocio de demostración dentro del CRM.
2. Ejecutar el registro oficial de Meta con los activos que el administrador tenga permitido compartir.
3. Mostrar la cuenta/número elegidos y su asociación en el CRM.
4. Mostrar la comprobación real de plantillas aprobadas, idioma y variables que use la versión presentada.
5. Mostrar la recepción de eventos para la cuenta autorizada cuando esté disponible, explicando que la aplicación se suscribe a esos eventos.
6. Si el formulario de Meta exige demostrar otra operación de administración, implementarla y grabarla de verdad o ajustar el alcance solicitado. No grabar clics inexistentes ni presentar WhatsApp Manager como una función desarrollada dentro del CRM.

Narración ES: «Este permiso se usa para identificar los activos compartidos por el administrador de la empresa, comprobar que el número pertenece a la cuenta autorizada y consultar las plantillas necesarias para los recordatorios. Cada empresa autoriza su propia conexión.»

EN narration: “This permission is used to identify the assets shared by the business administrator, verify that the number belongs to the authorized account and retrieve the templates needed for reminders. Each business authorizes its own connection.”

## Registro de resultados que debe acompañar los videos

Completar después de ejecutar, sin secretos:

- Versión/commit desplegado:
- Fecha, zona horaria y entorno HTTPS:
- Empresa de demostración:
- Permisos demostrados:
- Plantilla e idioma:
- Resultado de validación de cuenta/número:
- Aceptación de API:
- Entrega/lectura confirmada por webhook:
- Respuesta entrante recibida:
- Prueba de aislamiento entre empresas:
- Nombre de cada archivo de video adjunto en Meta:
- Persona responsable de revisar la grabación:

La prueba con `Prueba Femenina` queda condicionada a que el titular haya autorizado el mensaje y controle el número seleccionado. No se guardan el número ni su información personal en este paquete.
