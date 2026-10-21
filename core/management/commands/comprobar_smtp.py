import smtplib
import socket
import ssl
from email.utils import parseaddr

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage, get_connection
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email


class Command(BaseCommand):
    help = "Comprueba conexion y autenticacion SMTP; solo envia un correo con --to."

    def add_arguments(self, parser):
        parser.add_argument("--to", dest="recipient", help="Destinatario de un correo de prueba opcional.")

    def handle(self, *args, **options):
        if settings.EMAIL_BACKEND != "django.core.mail.backends.smtp.EmailBackend":
            raise CommandError("Configura EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend para comprobar el envio real.")
        if not settings.EMAIL_HOST:
            raise CommandError("Falta configurar EMAIL_HOST.")
        if settings.EMAIL_USE_TLS and settings.EMAIL_USE_SSL:
            raise CommandError("Activa solo EMAIL_USE_TLS o EMAIL_USE_SSL.")
        if not settings.EMAIL_USE_TLS and not settings.EMAIL_USE_SSL:
            raise CommandError("Configura TLS o SSL para comprobar una conexion SMTP cifrada.")
        if bool(settings.EMAIL_HOST_USER) != bool(settings.EMAIL_HOST_PASSWORD):
            raise CommandError("Configura juntos EMAIL_HOST_USER y EMAIL_HOST_PASSWORD.")

        try:
            sender_address = parseaddr(settings.DEFAULT_FROM_EMAIL)[1]
            validate_email(sender_address)
            if sender_address.lower().endswith(".local"):
                raise ValidationError("local")
        except (ValidationError, ValueError):
            raise CommandError("Configura DEFAULT_FROM_EMAIL con un remitente real autorizado por tu proveedor.") from None

        recipient = options.get("recipient")
        if recipient:
            try:
                validate_email(recipient)
            except ValidationError:
                raise CommandError("--to debe contener una direccion de correo valida.") from None

        connection = None
        try:
            connection = get_connection(fail_silently=False)
            connection.open()
            if recipient:
                sent = EmailMessage(
                    subject="Prueba de correo - DV Solutions ERP",
                    body="La configuracion de correo de DV Solutions ERP ha enviado este mensaje de prueba. No se ha creado ninguna cuenta ni invitacion.",
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=[recipient],
                    connection=connection,
                ).send(fail_silently=False)
                if sent != 1:
                    raise CommandError("El proveedor no confirmo la aceptacion del mensaje de prueba.")
        except smtplib.SMTPAuthenticationError:
            raise CommandError("El proveedor rechazo la autenticacion. Revisa la cuenta y la credencial SMTP o contrasena de aplicacion.") from None
        except ssl.SSLError:
            raise CommandError("Fallo la conexion cifrada. Revisa el puerto, TLS/SSL y el certificado del proveedor.") from None
        except (socket.timeout, TimeoutError):
            raise CommandError("La conexion SMTP agoto el tiempo de espera. Revisa el host, puerto y acceso de red.") from None
        except (OSError, smtplib.SMTPException):
            raise CommandError("No se pudo completar la comprobacion SMTP. Revisa host, puerto, permisos del remitente y acceso de red.") from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except (OSError, smtplib.SMTPException):
                    pass

        if recipient:
            self.stdout.write(self.style.SUCCESS("El servidor SMTP acepto el correo de prueba. Comprueba su recepcion y la carpeta de spam."))
        else:
            self.stdout.write(self.style.SUCCESS("Conexion SMTP correcta. Autenticacion completada cuando hay credenciales. No se envio ningun correo."))
