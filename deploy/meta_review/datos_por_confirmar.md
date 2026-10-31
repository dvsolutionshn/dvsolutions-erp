# Datos que debe confirmar DV Solutions antes de enviar

Completar estas respuestas con el responsable del servicio. No deducirlas de una plantilla, de una configuración local ni de lo que diga un asistente de soporte. Este archivo no contiene respuestas legales aprobadas.

## Acceso y activos de demostración

1. ¿Qué URL HTTPS puede abrir el revisor y qué versión exacta tiene desplegada?
2. ¿Existe un usuario de revisión con acceso solo a datos ficticios? ¿Necesita segundo factor, rol Meta o intervención de alguien del equipo?
3. ¿Qué empresa, número de origen y receptor están autorizados para la prueba? ¿Quién puede realizar el código/QR de verificación del número?
4. El usuario confirmó conservar WhatsApp Business en el teléfono de Hospital Mía; el flujo elegido es coexistencia. Falta comprobar la elegibilidad del número en Meta y disponer del administrador autorizado y del teléfono para completar el código o QR. Confirmar si existe alguna integración previa que afecte ese flujo. La sincronización de historial y los eventos de eco del teléfono no están implementados en esta entrega.
5. ¿Quién es el propietario y administrador del portafolio y del número de Hospital Mía? ¿Quién autoriza la conexión de DV Solutions?
6. ¿Quién paga las tarifas de Meta y quién recibe la factura del servicio CRM según el acuerdo real?

## Datos recibidos y utilizados

1. ¿Qué datos de Meta procesa efectivamente la versión presentada: identificadores de cuenta/número/mensaje, teléfono del contacto, nombre de perfil, contenido del mensaje, medio, estados y marcas de tiempo?
2. ¿Recibe o conserva la app algún campo de `public_profile`? Distinguir Facebook Login for Business del usuario y contraseña propios del ERP.
3. ¿Cuáles de esos datos se guardan y cuáles solo se usan durante la autorización? ¿Se registra alguno accidentalmente en logs, errores o monitoreo?
4. ¿Los mensajes de citas incluyen datos clínicos que no sean necesarios? Usar el mínimo contenido operativo aprobado por el hospital y datos ficticios en videos.
5. ¿Hay análisis con IA, publicidad, perfilado, venta de datos o transferencias adicionales en este flujo específico? Contestar según la operación efectiva, no según capacidades de otros módulos.

## Proveedores, almacenamiento y acceso

1. Nombre legal de los proveedores reales de servidor, base de datos, almacenamiento, respaldo, correo, monitoreo y soporte que acceden a datos de la plataforma.
2. Países/regiones donde almacenan o procesan datos y acuerdos efectivos con ellos.
3. Medidas realmente implementadas en servidor y respaldos: control de acceso, aislamiento, transporte HTTPS, cifrado de disco/base de datos y gestión de claves. No afirmar cifrado en reposo de tokens solo porque la pantalla usa un campo tipo contraseña.
4. Roles que pueden ver/exportar mensajes, gestionar credenciales o acceder a respaldos; proceso para retirar acceso.
5. Política y proceso reales de respuesta a incidentes, responsable y canal de contacto.

## Retención, eliminación y consentimiento

1. Plazo concreto para mensajes, eventos, tokens, logs y respaldos. ¿Cómo se ejecuta y demuestra su eliminación?
2. ¿Qué ocurre al desconectar una empresa y al revocar acceso desde Meta? ¿Se retiran credenciales y se detienen los envíos?
3. Canal operativo para solicitudes de acceso/eliminación, verificación de identidad, responsable y tiempos internos.
4. Método real para registrar la autorización de mensajes transaccionales y la autorización separada de promociones cuando corresponda.
5. Cómo se respeta una solicitud de no recibir mensajes y cómo se aplica a todos los tipos de campañas. La audiencia `promociones` del código revisado filtra `acepta_promociones`; las audiencias `todos` y `cumpleanos` requieren revisar su alcance antes de afirmar que hay un bloqueo global.

## Política pública y revisión

Existe una ruta de política en el repositorio: `/politica-de-privacidad/`. Confirmar que la URL pública carga, que su contacto está atendido y que describe fielmente la operación desplegada. La política anterior describe retención y proveedores de manera general; eso no permite inventar respuestas específicas para el cuestionario de Meta.

Antes del envío, el responsable debe aprobar las respuestas de uso permitido y tratamiento de datos, confirmar la exactitud de los videos y probar el acceso entregado al revisor.
