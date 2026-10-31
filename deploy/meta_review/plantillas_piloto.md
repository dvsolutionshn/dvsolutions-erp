# Plantillas para la prueba de Hospital Mía

Estos son textos propuestos. No están creados ni aprobados en Meta por este archivo. El administrador debe confirmar la redacción y categoría; la aprobación, categoría e idioma efectivos los determina Meta. Los nombres/idiomas guardados en el CRM deben coincidir exactamente con los aprobados en la cuenta del cliente.

## Prueba única, sin variables ni encabezado de imagen

Nombre propuesto: `prueba_conexion_crm`. Idioma propuesto: `es` (usar el código aprobado real).

> Esta es la prueba de conexión de WhatsApp de Hospital Mía que usted autorizó. No necesita compartir información personal ni realizar ningún pago. Gracias por ayudarnos a comprobar la recepción.

Esta plantilla no incluye variables, archivos, encabezados dinámicos ni botones dinámicos. El botón de prueba del CRM utiliza este tipo de plantilla. `hello_world` puede existir en la cuenta de prueba de Meta y faltar en una cuenta real; no asumir que sirve para el número productivo.

Antes de enviar, guardar en Configuración CRM el nombre e idioma aprobados y el número de Prueba Femenina autorizado. Marcar el consentimiento en el formulario de prueba. Enviar una sola vez y esperar el estado Entregado o Leído en la bandeja del CRM.

## Recordatorio de cita, seis variables

Nombre usado por defecto por el CRM: `recordatorio_cita`. Idioma propuesto: `es`.

```text
Hola {{1}}. Le escribimos de Hospital Mía: {{2}}.

Fecha: {{3}}
Hora: {{4}}
Servicio: {{5}}
Profesional: {{6}}

Por favor confirme su asistencia con nuestro equipo.
```

Orden exacto enviado: paciente, aviso, fecha, hora, servicio y profesional. El contenido debe limitarse a los datos operativos necesarios; no añadir diagnóstico, resultados o detalles clínicos sensibles a la plantilla de demostración.

Si se quiere utilizar el flujo existente de confirmación por enlace firmado, agregar una séptima variable de cuerpo `{{7}}` al texto aprobado y activar en CRM «La plantilla de citas incluye enlace de confirmación». Mantener ese interruptor apagado para una plantilla de seis variables.

## Cumpleaños

Para el piloto de citas se puede mantener apagado el interruptor de cumpleaños. Si se habilita, su remitente requiere una plantilla de cuerpo con cuatro variables y sin imagen dinámica: nombre, mensaje, vigencia y enlace. El nombre predeterminado `promo_general_imagen` no garantiza compatibilidad con este remitente. La activación comprueba la plantilla aprobada cuando ese interruptor está encendido.

Las campañas con imagen utilizan otro flujo que proporciona el encabezado de imagen. No reutilizar una plantilla con imagen en un remitente que solo manda variables de cuerpo.
