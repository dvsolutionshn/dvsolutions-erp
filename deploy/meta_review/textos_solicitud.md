# Textos para App Review

Estos son borradores para copiar tras completar y grabar las funciones que describen. No enviar texto condicional ni marcadores entre corchetes como si fueran hechos. Si una función no aparece en la demostración real, eliminarla del texto o terminarla primero.

## Descripción general — español

DV Solutions CRM es una plataforma empresarial que permite a cada empresa cliente administrar sus contactos, agenda y comunicaciones desde su propio espacio de trabajo. Para el caso de uso de Hospital Mía, el personal autorizado utiliza el CRM para enviar confirmaciones y recordatorios de citas mediante WhatsApp Business Platform. El administrador de cada empresa conecta sus propios activos de WhatsApp mediante el registro oficial de Meta y autoriza a la aplicación DV Solutions CRM. El número continúa perteneciendo a la empresa cliente. DV Solutions proporciona la integración, el software y soporte.

Los envíos iniciados por el negocio utilizan las plantillas aprobadas y los idiomas configurados para la cuenta de ese negocio. Las campañas promocionales son opcionales y se deben dirigir únicamente a destinatarios con autorización documentada. En esta demostración se utilizan datos ficticios y un destinatario de prueba autorizado.

## General description — English

DV Solutions CRM is a business platform that lets each customer business manage its contacts, appointments and communications in its own workspace. For the Hospital Mía use case, authorized staff use the CRM to send appointment confirmations and reminders through the WhatsApp Business Platform. Each business administrator connects their own WhatsApp assets through Meta's official signup flow and authorizes the DV Solutions CRM app. The customer business retains ownership of its phone number. DV Solutions provides the integration, software and support.

Business-initiated messages use the approved templates and configured languages for that business's account. Promotional campaigns are optional and must be limited to recipients with documented permission. This demonstration uses fictional data and an authorized test recipient.

## `whatsapp_business_messaging` — español

Solicitamos este permiso para enviar mensajes de WhatsApp en nombre de la empresa que haya autorizado nuestra aplicación. El caso principal es enviar confirmaciones y recordatorios de citas creadas por personal autorizado en el CRM. La empresa selecciona una plantilla aprobada, su idioma y los datos operativos de la cita. El servidor envía la solicitud a WhatsApp Cloud API utilizando las credenciales e identificadores de esa empresa. El video muestra el inicio de sesión en el CRM, la empresa de demostración seleccionada, el envío a un destinatario autorizado y la recepción del mensaje en WhatsApp.

Añadir el siguiente párrafo únicamente después de verificar y grabar la recepción real:

> La aplicación también recibe mensajes entrantes y estados de entrega mediante un webhook cuya firma se verifica. Los eventos se asocian a la cuenta y número autorizados de la empresa correspondiente, y se presentan a sus usuarios autorizados. Una respuesta de texto ambigua no modifica automáticamente una cita.

## `whatsapp_business_messaging` — English

We request this permission to send WhatsApp messages on behalf of a business that has authorized our application. The primary use case is sending confirmations and reminders for appointments created by authorized staff in the CRM. The business selects an approved template, its language and the operational appointment details. The server sends the request to WhatsApp Cloud API using that business's credentials and identifiers. The recording shows CRM sign-in, the selected demonstration business, a send to an authorized test recipient and receipt of the message in WhatsApp.

Add the following paragraph only after real inbound delivery has been verified and recorded:

> The application also receives incoming messages and delivery statuses through a signature-verified webhook. Events are associated with the corresponding business's authorized account and phone number and presented to its authorized users. An ambiguous text reply does not automatically change an appointment.

## `whatsapp_business_management` — español

Solicitamos este permiso para identificar y administrar la conexión de los activos de WhatsApp que la empresa cliente autoriza a nuestra aplicación. Durante la incorporación, consultamos los números asociados a la cuenta autorizada y comprobamos que el número seleccionado corresponde a esa cuenta antes de guardar la configuración de la empresa en el CRM. El permiso también se utiliza para comprobar o establecer la suscripción de la aplicación a los eventos de esa cuenta y para consultar las plantillas, sus idiomas y su estado de aprobación necesarios para los envíos configurados.

El video debe mostrar las operaciones efectivamente disponibles en la versión presentada. No afirmamos que el CRM cree, edite o elimine plantillas automáticamente si esas funciones no forman parte de la demostración. El permiso no se usa para acceder a activos de empresas que no hayan autorizado la aplicación.

## `whatsapp_business_management` — English

We request this permission to identify and manage the connection of the WhatsApp assets that a customer business authorizes to our app. During onboarding, we retrieve the phone numbers associated with the authorized account and verify that the selected number belongs to that account before saving the business's CRM configuration. The permission is also used to check or establish the app's subscription to that account's events and to retrieve the templates, languages and approval statuses required for the configured sends.

The recording must show the operations actually available in the submitted version. We do not claim automatic template creation, editing or deletion in the CRM unless those functions are part of the demonstration. The permission is not used to access assets belonging to businesses that have not authorized the app.

## `public_profile` — alcance por confirmar

Este permiso aparece en el borrador de Meta. El acceso del usuario al ERP utiliza la autenticación propia de DV Solutions; no describir ese acceso como Facebook Login. Antes de enviar, confirmar si la configuración de Facebook Login for Business exige `public_profile` y qué datos se reciben y guardan realmente.

Si Meta mantiene el permiso como parte del flujo y no se realiza ningún uso adicional, esta explicación solo se puede utilizar después de comprobarla:

**ES:** `public_profile` forma parte del flujo de autorización de Meta utilizado por el administrador de la empresa para conectar sus activos de WhatsApp. El inicio de sesión al CRM se gestiona por separado. No utilizamos este permiso para obtener la lista de amigos ni para publicar en el perfil personal. [Completar únicamente los campos de identidad que efectivamente se reciben/guardan, o indicar que no se guardan si se verificó.]

**EN:** `public_profile` is part of Meta's authorization flow used by the business administrator to connect their WhatsApp assets. CRM authentication is handled separately. We do not use this permission to obtain a friends list or publish to a personal profile. [List only identity fields actually received/stored, or state that none are stored if verified.]

## Instrucciones para revisores — completar en Meta

No crear credenciales dentro de este archivo. Mantener los datos de acceso en el formulario privado de Meta o en el canal autorizado para revisores.

### Español

1. Abra `[URL HTTPS de revisión validada]`.
2. Inicie sesión con el usuario de revisión facilitado en los campos privados de esta solicitud.
3. Seleccione `[empresa ficticia de revisión]` y entre en CRM → Configuración.
4. Siga el recorrido mostrado en el video para conectar los activos de demostración autorizados. Si la app aún tiene restricciones de desarrollo, explique con precisión qué identidad/activo de demostración puede utilizar el revisor; no solicitar acceso a su cuenta personal ni prometer que cualquier cuenta funcionará.
5. Compruebe el número de demostración mostrado y la configuración de plantilla/idioma. Las automatizaciones deben permanecer pausadas.
6. Realice la única prueba permitida al destinatario de demostración indicado en la solicitud privada; no use la lista de pacientes ni envíe campañas masivas.
7. Compare el identificador de envío y los estados de entrega con el video. Si la versión presentada incluye bandeja de entrada, envíe una respuesta de demostración y compruebe que aparece solo en la empresa autorizada.
8. Para el permiso de administración, compruebe los activos y plantillas consultados por el flujo demostrado. Las operaciones grabadas deben coincidir con la versión accesible al revisor.
9. Contacto de revisión: `[correo atendido por el responsable]`. Complete aquí cualquier restricción de horario, autenticación o entorno que realmente exista.

### English

1. Open `[validated HTTPS review URL]`.
2. Sign in with the reviewer credentials provided in the private fields of this submission.
3. Select `[fictional review business]` and open CRM → Configuration.
4. Follow the recording to connect the authorized demonstration assets. If development restrictions still apply, accurately explain which demonstration identity/assets the reviewer may use; do not request access to the reviewer's personal account or imply that every account will work.
5. Verify the displayed demonstration number and template/language configuration. Keep automations paused.
6. Perform the single permitted test to the demonstration recipient identified in the private submission; do not use patient lists or send bulk campaigns.
7. Compare the message identifier and delivery statuses with the recording. If the submitted version includes an inbox, send a demonstration reply and confirm that it appears only in the authorized business workspace.
8. For the management permission, inspect the authorized assets and templates retrieved by the demonstrated flow. Recorded actions must match the version available to the reviewer.
9. Review contact: `[monitored contact email]`. Specify any real schedule, authentication or environment restrictions.
