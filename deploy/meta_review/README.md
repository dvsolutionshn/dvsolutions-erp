# Revisión de Meta — DV Solutions CRM

Paquete de trabajo preparado el 31 de octubre de 2026. No acredita una aprobación ni sustituye una prueba real con Meta.

- Negocio proveedor: DV Solutions, Business ID `1894111938665527`.
- Aplicación: DV Solutions CRM, App ID `1247884751184656`.
- Solicitud de App Review observada: `1247886881184443`, **No enviada**.
- Permisos solicitados: `whatsapp_business_messaging`, `whatsapp_business_management` y `public_profile`.
- Verificación del negocio: aprobada según el panel observado.
- Verificación de acceso como proveedor tecnológico: trámite separado, pendiente según el panel observado.
- Publicación de la app: **Sin publicar** según el panel observado.
- Inspección de configuración del 31/10/2026: el identificador usado por el CRM (`1785228876001463`) incluye `whatsapp_business_management` y `whatsapp_business_messaging`. Su nombre en Meta menciona un token de 60 días; debe verificarse la expiración efectiva del token al conectar y planificarse su renovación. La inspección se cerró con Cancelar, sin guardar cambios de permisos.

## Modelo del servicio

DV Solutions opera y cobra por el CRM. Hospital Mía conserva la propiedad de su número y de los activos de WhatsApp que autorice. La incorporación se realiza desde el CRM mediante el registro oficial de Meta: el administrador del hospital selecciona o crea su portafolio, selecciona su cuenta/número y autoriza a la app de DV Solutions. Los clientes posteriores siguen ese mismo flujo con sus propios activos.

El usuario confirmó que Hospital Mía debe conservar WhatsApp Business en el teléfono: el flujo elegido es **coexistencia**. Falta comprobar la elegibilidad real que muestre Meta y completar el código o QR con el administrador autorizado y el teléfono del hospital. Esta elección no acredita aprobación ni despliegue; la sincronización de historial y los eventos de eco del teléfono no están implementados en esta entrega.

La pantalla vacía de cuentas de mensajería de DV Solutions no demuestra por sí sola la causa de la revisión pendiente. No se debe crear una cuenta del hospital bajo el portafolio del proveedor para intentar eliminar una alerta.

Meta distingue actualmente la cuenta de WhatsApp que gestiona el número y la cuenta de mensajería que gestiona plantillas y pagos. Confirmar los identificadores devueltos por el flujo habilitado en la app y su asociación antes de guardar una conexión. La facturación de Meta y la licencia del CRM se deben explicar como conceptos separados, según el acuerdo efectivo.

## Archivos

1. `textos_solicitud.md`: borradores en español e inglés; revisar los límites indicados antes de copiar.
2. `guion_demostracion.md`: grabación real con una empresa de demostración y un destinatario autorizado.
3. `datos_por_confirmar.md`: hechos que DV Solutions debe confirmar antes de contestar tratamiento de datos e instrucciones de acceso.
4. `despliegue_y_validacion.md`: orden de despliegue y puesta en marcha controlada.
5. `plantillas_piloto.md`: propuestas de prueba y recordatorio con el orden de variables que envía el CRM; requieren aprobación real en Meta.

## Secuencia de preparación

1. Terminar y verificar los cambios locales de la integración; conservar el resultado exacto de las pruebas.
2. Desplegar una versión revisada en un entorno HTTPS de pruebas o en la ventana de producción autorizada. Mantener pausados los envíos del negocio piloto durante la conexión y validación.
3. Confirmar la configuración actual de Facebook Login for Business y del registro integrado. El campo `sessionInfoVersion: "3"` del código anterior describe el formato de la información de sesión; no prueba por sí solo qué versión de Embedded Signup está usando la configuración de Meta.
4. Preparar una empresa de demostración con datos ficticios, usuario revisor limitado y número autorizado. No reutilizar pacientes reales.
5. Verificar un recorrido real de conexión, envío, recepción y estado de entrega; grabar los permisos demostrables.
6. Completar uso permitido, tratamiento de datos e instrucciones de revisión con hechos confirmados por el responsable.
7. Revisar los videos y las respuestas completas. Enviar la solicitud cuando todos sus apartados estén completos y los activos de demostración funcionen.
8. Resolver las aprobaciones/restricciones que muestre Meta; publicar la app cuando se cumplan sus requisitos.
9. Incorporar el número real del hospital y activar sus automatizaciones solo después de la prueba autorizada.

## Matriz de evidencia y pendientes

| Elemento | Evidencia disponible al preparar el paquete | Antes de enviar |
| --- | --- | --- |
| Negocio verificado | Estado visible del portafolio en Meta | Confirmar que sigue aprobado |
| App Review | Borrador observado, no enviado | Completar cada sección y enviar |
| Acceso tecnológico | Trámite independiente en revisión | Resolver la revisión o cualquier acción que Meta solicite |
| Conexión por empresa | Configuración CRM por empresa y botón Meta existentes en código | Prueba real de autorización y asociación de activos |
| Envío por Cloud API | Servicios de envío y recordatorios existentes | Mensaje real aceptado, recibido y asociado al negocio correcto |
| Recepción/estados | Webhook firmado y bandeja implementados en el código local | Entrega real del webhook `/integraciones/whatsapp/webhook/` después del despliegue |
| Plantillas | CRM usa nombres/idiomas configurados | Confirmar aprobación y variables en la cuenta real |
| Administración mediante permiso management | Uso previsto para descubrir activos autorizados, suscribirse y consultar plantillas | Grabar las operaciones que efectivamente estén disponibles; no afirmar creación/edición automática de plantillas si no existe |
| `public_profile` | Presente en el borrador de Meta | Confirmar si el flujo lo requiere y limitar su descripción a la identidad de Meta; no confundir con el acceso al ERP |
| Videos | Guion preparado | Grabar y adjuntar archivos reales; no hay video de aprobación en este paquete |
| Acceso del revisor | Plantilla de instrucciones | Crear/probar acceso limitado y completar los datos privados en Meta |
| Tratamiento de datos | Política existente y preguntas de verificación | Confirmar proveedores, ubicaciones, retención y procesos reales |
| Automatizaciones | Existe procesador programado cada cinco minutos en el script de despliegue anterior | Conexión nueva pausada, `wamid` de prueba con `delivered`/`read`, plantilla aprobada cuando aplique y revisión de cola antes de activar |

## Fuentes oficiales para cotejar al ejecutar

- [Solicitud real de esta app](https://developers.facebook.com/apps/1247884751184656/app-review/submissions/?submission_id=1247886881184443&business_id=1894111938665527).
- [Programa de proveedores de WhatsApp](https://whatsappbusiness.com/partners/become-a-partner/).
- [Evolución del modelo de cuentas de WhatsApp](https://developers.meta.com/resources/videos/whatsapp-account-model-evolution/).
- [App Review para proveedores](https://developers.facebook.com/documentation/business-messaging/whatsapp/solution-providers/app-review/).
- [Registro integrado](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/overview/).
- [Coexistencia con WhatsApp Business](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-business-app-users/).

Las rutas de documentación pueden exigir sesión o limitar consultas. Los textos del asistente de soporte no equivalen a una aprobación ni a un diagnóstico técnico confirmado.

## Alcance de esta versión

La bandeja guarda mensajes entrantes nuevos y estados de entrega firmados. No importa el historial del teléfono ni sincroniza los ecos de mensajes enviados desde la app de WhatsApp Business. La recepción de una respuesta no confirma una cita por inferencia; el flujo de confirmación existente usa un enlace firmado. La bandeja de esta entrega es de consulta; no se presenta como un chat bidireccional con editor de respuestas.

Las conexiones anteriores no se desactivan por la migración. Las conexiones nuevas o con credenciales modificadas requieren validación; una conexión validada también vuelve a pausa cuando cambia el esquema de sus plantillas. El token guardado no vuelve a aparecer en el HTML del formulario. El almacenamiento existente del token en la base no se ha migrado a cifrado de aplicación en esta entrega: no afirmar ese cifrado en la solicitud de Meta.
