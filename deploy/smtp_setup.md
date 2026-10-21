# Invitaciones y configuracion SMTP

La cuenta remitente pertenece a DV Solutions o al proveedor contratado para enviar correo. El destinatario puede ser `jorge.palma@rumi.hn` o cualquier otra direccion valida; no se necesitan credenciales de Rumi para enviarle una invitacion.

## Datos necesarios

Solicitar al proveedor del remitente el host SMTP, puerto, modalidad TLS/SSL, usuario SMTP y credencial autorizada. Algunas cuentas usan una contrasena de aplicacion o un servicio de retransmision. Si el proveedor requiere OAuth, no se debe sustituir por la contrasena normal: este backend usa autenticacion SMTP por usuario y credencial.

El remitente debe estar autorizado por el proveedor. Para un dominio propio, configurar los registros de autenticacion que indique ese proveedor (SPF, DKIM y DMARC).

## Variables

La plantilla `deploy/gunicorn.service` usa `/opt/dvsolutions/repo/.env.production` mediante `EnvironmentFile`; la instalacion actual se detalla al final de esta guia. Django **no carga archivos `.env` automaticamente**: crear el archivo sin cargar sus variables no cambia el envio. No incluir credenciales en Git ni en mensajes de chat.

Completar en el archivo de entorno real, conservando las demas variables existentes:

```dotenv
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=HOST_ENTREGADO_POR_EL_PROVEEDOR
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_USE_SSL=false
EMAIL_TIMEOUT=20
EMAIL_HOST_USER=CUENTA_O_USUARIO_SMTP_REAL
EMAIL_HOST_PASSWORD=CREDENCIAL_SMTP_REAL
DEFAULT_FROM_EMAIL=REMITENTE_AUTORIZADO_REAL
ACCESS_PUBLIC_BASE_URL=https://DOMINIO_PUBLICO_REAL_DEL_ERP
```

Para SSL implicito, usar el puerto que indique el proveedor (habitualmente `465`), `EMAIL_USE_SSL=true` y `EMAIL_USE_TLS=false`. Para STARTTLS, usar el puerto indicado (habitualmente `587`) con `EMAIL_USE_TLS=true` y `EMAIL_USE_SSL=false`. Las dos opciones no pueden estar activadas simultaneamente. [Configuracion de correo de Django](https://docs.djangoproject.com/en/5.2/ref/settings/#email-use-ssl).

`ACCESS_PUBLIC_BASE_URL` es el origen HTTPS desde el que se sirve **este ERP**, sin ruta, parametros ni credenciales. Evita que una invitacion creada desde localhost o desde un acceso interno contenga una direccion que el destinatario no puede abrir. Ese dominio tambien debe estar en `ALLOWED_HOSTS` y configurado en el proxy. Si se deja vacio, el enlace usa el dominio de la solicitud.

## Comprobar sin enviar mensajes

Antes de usar este cambio, aplicar la migracion `core.0056_seguridad_envio_invitaciones` con el respaldo habitual de la base. Los enlaces anteriores no tienen la firma del estado de la cuenta y se revocan una sola vez al migrar: reenviar las invitaciones pendientes o solicitar una recuperacion nueva. Las cuentas y sus contrasenas se conservan.

Las invitaciones nuevas duran 48 horas y la recuperacion dura 2 horas. Cambiar el correo o la contrasena, desactivar o retirar una cuenta invalida sus enlaces. Un reenvio solo revoca el enlace anterior cuando SMTP acepta el nuevo correo. Si falla, el listado muestra `Error de envio` y conserva los enlaces anteriores que aun sean validos. El backend de consola no se presenta como envio real.

En la instalacion actual, ejecutar el diagnostico con el usuario, directorio y archivo de entorno del servicio real:

```bash
sudo systemd-run --quiet --wait --pipe --collect \
  --property=User=dvsolutions \
  --property=WorkingDirectory=/var/www/dvsolutions-erp \
  --property=EnvironmentFile=/etc/dvsolutions-erp.env \
  /var/www/dvsolutions-erp/.venv/bin/python manage.py comprobar_smtp
```

systemd lee `EnvironmentFile` con la misma semantica que el servicio y entrega sus variables al proceso de diagnostico. No usar `source` ni un parser que divida valores por espacios: el formato de systemd no es un script de shell y los remitentes pueden incluir espacios. No imprimir el archivo ni el entorno del proceso. [systemd-run](https://www.freedesktop.org/software/systemd/man/255/systemd-run.html), [EnvironmentFile](https://www.freedesktop.org/software/systemd/man/255/systemd.exec.html#EnvironmentFile=).

Este comando conecta y autentica cuando hay credenciales; no envia mensajes ni crea usuarios. Los errores se muestran sin revelar la credencial ni la respuesta completa del servidor SMTP. Una comprobacion de conexion no confirma que un destinatario vaya a recibir el correo.

La prueba autorizada para esta configuracion se dirige exclusivamente a Daniel:

```bash
sudo systemd-run --quiet --wait --pipe --collect \
  --property=User=dvsolutions \
  --property=WorkingDirectory=/var/www/dvsolutions-erp \
  --property=EnvironmentFile=/etc/dvsolutions-erp.env \
  /var/www/dvsolutions-erp/.venv/bin/python manage.py comprobar_smtp \
  --to daniel.varela@dvsolutionshn.com
```

Este segundo comando si envia un mensaje sencillo. Que SMTP lo acepte no confirma entrega en la bandeja de entrada; comprobar tambien spam y, si corresponde, los registros de entrega del proveedor. [Backend SMTP de Django](https://docs.djangoproject.com/en/5.2/topics/email/#smtp-backend).

Despues de aplicar las variables al servicio, reiniciar la unidad real de Gunicorn, por ejemplo `sudo systemctl restart dvsolutions`, y crear la invitacion desde Control > Usuarios > Nuevo usuario > Enviar invitacion por correo. No hace falta usar el mismo dominio en el remitente y el destinatario.

En Windows local, cargar las variables en el proceso de PowerShell antes de iniciar `manage.py`; escribirlas en `.env` por si solo no las carga. Usar el backend de consola solo para desarrollo: imprime el correo y no lo entrega a personas.

## Prueba local con Google y remitente solicitado: oasis@dvsolutionshn.com

El 21/10/2026 se verifico que el MX del dominio es `smtp.google.com`, correspondiente a Google Workspace. El usuario confirmo que `oasis@dvsolutionshn.com` es un alias de la cuenta principal `daniel.varela@dvsolutionshn.com`. La prueba local autentica con Daniel y solicita `oasis@dvsolutionshn.com` como remitente. La cuenta debe permitir ese alias como "Enviar como"; esta configuracion local no determina el remitente activo en produccion.

Configuracion local para el backend SMTP de Google:

```dotenv
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_USE_SSL=false
EMAIL_TIMEOUT=20
EMAIL_HOST_USER=daniel.varela@dvsolutionshn.com
DEFAULT_FROM_EMAIL='DV Solutions <oasis@dvsolutionshn.com>'
ACCESS_PUBLIC_BASE_URL=https://dvsolutionshn.com
```

Agregar `EMAIL_HOST_PASSWORD` directamente en el entorno privado con una contrasena de aplicacion generada en `daniel.varela@dvsolutionshn.com`, sin ponerla en Git, comandos con historial o chat. Google requiere verificacion en dos pasos para crearla y las politicas de Workspace pueden limitar su disponibilidad. [Crear contrasena de aplicacion](https://myaccount.google.com/apppasswords), [instrucciones oficiales de Google](https://support.google.com/accounts/answer/185833?hl=es), [SMTP para aplicaciones](https://support.google.com/a/answer/176600?hl=en), [enviar desde un alias](https://support.google.com/mail/answer/22370?hl=es).

En un proceso local que haya cargado esas variables, comprobar primero autenticacion con `python manage.py comprobar_smtp`. El destinatario autorizado por el usuario para la prueba es **daniel.varela@dvsolutionshn.com**:

```bash
python manage.py comprobar_smtp --to daniel.varela@dvsolutionshn.com
```

No enviar a `jorge.palma@rumi.hn`: se uso solo como ejemplo de destinatario. Se verifico la conexion STARTTLS a `smtp.gmail.com:587` y que `https://dvsolutionshn.com/control/login/` responde HTTP 200; estas comprobaciones no autentican la cuenta ni envian mensajes.

### Entrada privada en Windows

`deploy/smtp_windows.ps1 -Accion Guardar` abre un cuadro con campo de clave oculto y guarda la credencial mediante DPAPI en `.env.smtp.credentials.xml`, excluido de Git por `.env.*`. Solo el mismo usuario de Windows en la misma maquina puede descifrarla. No se imprime ni se pasa la clave por argumentos de procesos.

Con la credencial guardada, ejecutar desde PowerShell:

```powershell
.\deploy\smtp_windows.ps1 -Accion Comprobar
.\deploy\smtp_windows.ps1 -Accion EnviarPrueba
```

La segunda accion envia exclusivamente a Daniel. La configuracion se aplica al proceso de diagnostico y despues restaura el entorno de la consola. Esta credencial cifrada local no configura automaticamente el servidor de produccion ni crea enlaces de invitacion en otra base de datos.

En servidores con acceso permitido a Google SMTP, `bash deploy/configurar_smtp_google.sh /RUTA/AL/ARCHIVO/DE/ENTORNO` solicita la misma clave de forma oculta, respalda el archivo y cambia solo las variables de correo y el origen publico. El archivo debe ser el que carga el servicio real; el script no envia mensajes, aplica migraciones ni reinicia servicios. No usar ese configurador de Google en el Droplet actual, donde sus puertos estan bloqueados.

## Instalacion actual en DigitalOcean

La aplicacion esta en `/var/www/dvsolutions-erp`, el servicio es `dvsolutions.service` y carga `/etc/dvsolutions-erp.env`. Su entorno virtual es `.venv`. Se verifico que Google SMTP no es accesible desde este servidor en los puertos 587 y 465; el firewall local permite salida y HTTPS funciona. DigitalOcean documenta el bloqueo predeterminado de los puertos SMTP 25, 465 y 587. [Politica de DigitalOcean](https://docs.digitalocean.com/support/why-is-smtp-blocked/).

Las correcciones de invitaciones y la migracion `0056` estan aplicadas. El ERP dispone de una cuenta SMTP de Brevo que autentica correctamente por `smtp-relay.brevo.com:2525`, con `EMAIL_USE_TLS=true` y `EMAIL_USE_SSL=false`. Se conservaron el usuario y la clave de Brevo en el archivo privado del servicio; la clave de aplicacion de Google se usa solo en la prueba local.

El remitente vigente y conservado en produccion es **`dv.solutionshn@gmail.com`**. El cambio al remitente solicitado **`oasis@dvsolutionshn.com`** sigue pendiente de verificacion en Brevo y de confirmar la entrega a Daniel. La autenticacion SMTP correcta no verifica por si sola el permiso para enviar desde Oasis ni su entrega. No sustituir el remitente existente hasta completar esa comprobacion. El origen de los enlaces ya es `https://dvsolutionshn.com`.

Brevo admite STARTTLS por el puerto 2525 cuando el proveedor bloquea 587. El remitente debe estar verificado o pertenecer a un dominio autenticado en la cuenta de Brevo; que SMTP acepte un mensaje no demuestra que haya llegado a la bandeja. [Puertos de Brevo](https://help.brevo.com/hc/en-us/articles/10905415650322-Which-SMTP-port-should-I-use-Port-587-465-or-2525), [validacion del remitente](https://help.brevo.com/hc/en-us/articles/115000188150-Troubleshooting-Issues-with-Brevo-SMTP).
