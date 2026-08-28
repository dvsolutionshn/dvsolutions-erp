import hashlib
import json
import logging
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.auth import authenticate
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from core.assistant import responder_consulta
from core.models import Empresa
from core.onix_access import onix_disponible_para_empresa
from core.onix_actions import cancelar_accion, ejecutar_accion

from .authentication import autenticar_token_movil
from .integrations import construir_autorizacion_google, intercambiar_codigo_google
from .models import (
    ConexionOnixExterna,
    PerfilOnixPersonal,
    SesionOnixMovil,
    SolicitudOAuthOnix,
)
from .serializers import (
    construir_bootstrap,
    serializar_accion,
    serializar_conexiones,
    serializar_mensajes,
)
from .security import validar_cifrado_conexiones


logger = logging.getLogger(__name__)


def _json_error(mensaje, *, status=400, codigo="invalid_request"):
    return JsonResponse({"ok": False, "error": mensaje, "code": codigo}, status=status)


def _leer_json(request):
    maximo = int(getattr(settings, "ONIX_MOBILE_MAX_BODY_BYTES", 65536))
    if len(request.body) > maximo:
        raise ValidationError("La solicitud es demasiado grande.")
    try:
        datos = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("Envia los datos en formato JSON valido.") from exc
    if not isinstance(datos, dict):
        raise ValidationError("El contenido JSON debe ser un objeto.")
    return datos


def _direccion_ip(request):
    reenviada = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (reenviada.split(",", 1)[0] if reenviada else request.META.get("REMOTE_ADDR", "")).strip() or None


def _clave_intentos(request, empresa_slug, identificador):
    origen = f"{_direccion_ip(request)}|{empresa_slug.lower()}|{identificador.lower()}"
    return "onix-mobile-login:" + hashlib.sha256(origen.encode("utf-8")).hexdigest()


def _registrar_fallo(clave):
    ventana = max(60, int(getattr(settings, "ONIX_MOBILE_LOGIN_WINDOW_SECONDS", 900)))
    actual = cache.get(clave)
    if actual is None:
        cache.set(clave, 1, ventana)
        return 1
    try:
        return cache.incr(clave)
    except ValueError:
        cache.set(clave, 1, ventana)
        return 1


def _solo_metodo(request, metodo):
    if request.method != metodo:
        respuesta = _json_error("Metodo no permitido.", status=405, codigo="method_not_allowed")
        respuesta["Allow"] = metodo
        return respuesta
    return None


def _normalizar_whatsapp(valor):
    texto = str(valor or "").strip()
    if not texto:
        return ""
    digitos = re.sub(r"\D", "", texto)
    if digitos.startswith("00"):
        digitos = digitos[2:]
    if not texto.startswith("+") and len(digitos) == 8:
        digitos = "504" + digitos
    if not 8 <= len(digitos) <= 15:
        raise ValidationError("Escribe el WhatsApp con codigo de pais, por ejemplo +504 9999-9999.")
    return "+" + digitos


def _oauth_html(titulo, mensaje, *, status=200):
    response = HttpResponse(
        "<!doctype html><html lang='es'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>ONIX</title><body style='margin:0;background:#071525;color:#f7fbff;"
        "font-family:system-ui;display:grid;place-items:center;min-height:100vh'>"
        "<main style='max-width:520px;padding:40px;text-align:center'>"
        "<div style='font-size:14px;letter-spacing:5px;color:#41dbde'>ONIX</div>"
        f"<h1>{titulo}</h1><p style='color:#b8cfe1;line-height:1.6'>{mensaje}</p>"
        "<p style='margin-top:28px'>Ya puedes cerrar esta ventana y volver a la aplicacion.</p>"
        "</main></body></html>",
        status=status,
        content_type="text/html; charset=utf-8",
    )
    response["Cache-Control"] = "no-store"
    response["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    return response


@csrf_exempt
def login(request):
    error_metodo = _solo_metodo(request, "POST")
    if error_metodo:
        return error_metodo
    try:
        datos = _leer_json(request)
    except ValidationError as exc:
        return _json_error(" ".join(exc.messages))

    empresa_slug = str(datos.get("empresa") or "").strip()
    identificador = str(datos.get("usuario") or "").strip()
    contrasena = str(datos.get("password") or "")
    dispositivo = str(datos.get("dispositivo") or "").strip()
    if not empresa_slug or not identificador or not contrasena:
        return _json_error("Indica empresa, usuario y contrasena.", codigo="missing_credentials")

    clave_intentos = _clave_intentos(request, empresa_slug, identificador)
    limite = max(1, int(getattr(settings, "ONIX_MOBILE_LOGIN_MAX_ATTEMPTS", 5)))
    if int(cache.get(clave_intentos, 0)) >= limite:
        respuesta = _json_error(
            "Demasiados intentos. Espera unos minutos antes de volver a probar.",
            status=429,
            codigo="too_many_attempts",
        )
        respuesta["Retry-After"] = str(
            max(60, int(getattr(settings, "ONIX_MOBILE_LOGIN_WINDOW_SECONDS", 900)))
        )
        return respuesta

    empresa = Empresa.objects.filter(slug__iexact=empresa_slug, activa=True).first()
    usuario = None
    if empresa and empresa.licencia_operativa and onix_disponible_para_empresa(empresa):
        usuario = authenticate(
            request,
            username=identificador,
            password=contrasena,
            empresa=empresa,
        )

    if (
        usuario is None
        or not usuario.is_active
        or (not usuario.is_superuser and not usuario.puede_acceder_empresa(empresa))
    ):
        _registrar_fallo(clave_intentos)
        return _json_error(
            "No pudimos validar la empresa o las credenciales.",
            status=401,
            codigo="invalid_credentials",
        )

    cache.delete(clave_intentos)
    token, sesion = SesionOnixMovil.emitir(
        usuario=usuario,
        empresa=empresa,
        dispositivo=dispositivo,
        direccion_ip=_direccion_ip(request),
        user_agent=request.headers.get("User-Agent", ""),
    )
    return JsonResponse(
        {
            "ok": True,
            "token": token,
            "token_type": "Bearer",
            "expires_at": sesion.expira_en.isoformat(),
            "bootstrap": construir_bootstrap(usuario=usuario, empresa=empresa),
        }
    )


@csrf_exempt
@autenticar_token_movil
def bootstrap(request):
    error_metodo = _solo_metodo(request, "GET")
    if error_metodo:
        return error_metodo
    return JsonResponse(
        {
            "ok": True,
            "bootstrap": construir_bootstrap(usuario=request.user, empresa=request.empresa),
        }
    )


@csrf_exempt
@autenticar_token_movil
def connections(request):
    error_metodo = _solo_metodo(request, "GET")
    if error_metodo:
        return error_metodo
    return JsonResponse(
        {
            "ok": True,
            "connections": serializar_conexiones(empresa=request.empresa, usuario=request.user),
        }
    )


@csrf_exempt
@autenticar_token_movil
def personal_profile(request):
    error_metodo = _solo_metodo(request, "POST")
    if error_metodo:
        return error_metodo
    try:
        datos = _leer_json(request)
        whatsapp = _normalizar_whatsapp(datos.get("whatsapp"))
        zona_horaria = str(datos.get("timezone") or "America/Tegucigalpa").strip()
        ZoneInfo(zona_horaria)
        canal = str(datos.get("reminder_channel") or PerfilOnixPersonal.CANAL_APP).strip()
        canales = {valor for valor, _ in PerfilOnixPersonal.CANALES}
        if canal not in canales:
            raise ValidationError("Selecciona un canal de recordatorio valido.")
        opt_in = datos.get("whatsapp_opt_in") is True
        if opt_in and not whatsapp:
            raise ValidationError("Registra tu numero antes de activar avisos por WhatsApp.")
        if canal == PerfilOnixPersonal.CANAL_WHATSAPP and not opt_in:
            raise ValidationError("Activa los avisos por WhatsApp antes de usarlo como canal principal.")
    except ZoneInfoNotFoundError:
        return _json_error("La zona horaria indicada no es valida.")
    except ValidationError as exc:
        return _json_error(" ".join(exc.messages))

    perfil, _ = PerfilOnixPersonal.objects.get_or_create(usuario=request.user)
    if perfil.telefono_whatsapp != whatsapp:
        perfil.whatsapp_verificado_en = None
    perfil.telefono_whatsapp = whatsapp
    perfil.acepta_notificaciones_whatsapp = opt_in
    perfil.zona_horaria = zona_horaria
    perfil.canal_recordatorio = canal
    perfil.save()
    return JsonResponse(
        {
            "ok": True,
            "connections": serializar_conexiones(empresa=request.empresa, usuario=request.user),
        }
    )


@csrf_exempt
@autenticar_token_movil
def google_connection_start(request):
    error_metodo = _solo_metodo(request, "POST")
    if error_metodo:
        return error_metodo
    try:
        validar_cifrado_conexiones()
        estado, _ = SolicitudOAuthOnix.emitir(
            usuario=request.user,
            empresa=request.empresa,
            proveedor=ConexionOnixExterna.GOOGLE_CALENDAR,
        )
        url = construir_autorizacion_google(estado)
    except ImproperlyConfigured:
        return _json_error(
            "El servidor todavia no puede proteger conexiones externas.",
            status=503,
            codigo="connection_security_not_configured",
        )
    except ValidationError as exc:
        SolicitudOAuthOnix.objects.filter(estado_hash=SolicitudOAuthOnix.calcular_hash(estado)).delete()
        return _json_error(" ".join(exc.messages), status=503, codigo="google_not_configured")
    ConexionOnixExterna.objects.update_or_create(
        usuario=request.user,
        empresa=request.empresa,
        proveedor=ConexionOnixExterna.GOOGLE_CALENDAR,
        defaults={"estado": ConexionOnixExterna.PENDIENTE, "ultimo_error": ""},
    )
    return JsonResponse({"ok": True, "authorization_url": url, "expires_in": 600})


def google_connection_callback(request):
    if request.method != "GET":
        return _oauth_html("Solicitud no valida", "Este enlace solo puede abrirse desde Google.", status=405)
    estado = str(request.GET.get("state") or "")
    codigo = str(request.GET.get("code") or "")
    error = str(request.GET.get("error") or "")
    solicitud = SolicitudOAuthOnix.consumir(estado, ConexionOnixExterna.GOOGLE_CALENDAR) if estado else None
    if not solicitud:
        return _oauth_html("Enlace vencido", "Vuelve a ONIX e inicia nuevamente la conexion.", status=400)
    if error or not codigo:
        return _oauth_html("Conexion cancelada", "Google no autorizo el acceso al calendario.", status=400)
    try:
        datos = intercambiar_codigo_google(codigo)
        with transaction.atomic():
            conexion, _ = ConexionOnixExterna.objects.select_for_update().get_or_create(
                usuario=solicitud.usuario,
                empresa=solicitud.empresa,
                proveedor=ConexionOnixExterna.GOOGLE_CALENDAR,
            )
            conexion.guardar_tokens(
                acceso=datos["access_token"],
                refresco=datos["refresh_token"],
            )
            conexion.estado = ConexionOnixExterna.CONECTADA
            conexion.cuenta_externa = datos["email"]
            conexion.nombre_cuenta = datos["name"]
            conexion.permisos = datos["scope"]
            conexion.token_expira_en = datos["expires_at"]
            conexion.sincronizacion_activa = True
            conexion.ultimo_error = ""
            conexion.metadatos = {"subject": datos["subject"]}
            conexion.save()
    except (ImproperlyConfigured, ValidationError):
        logger.exception("Google OAuth fallo para la solicitud %s", solicitud.id)
        ConexionOnixExterna.objects.filter(
            usuario=solicitud.usuario,
            empresa=solicitud.empresa,
            proveedor=ConexionOnixExterna.GOOGLE_CALENDAR,
        ).update(estado=ConexionOnixExterna.ERROR, ultimo_error="Google no completo la autorizacion.")
        return _oauth_html("No pudimos conectar Google", "Intenta nuevamente desde ONIX.", status=502)
    return _oauth_html("Google Calendar conectado", "ONIX ya puede trabajar con el calendario que autorizaste.")


@csrf_exempt
@autenticar_token_movil
def disconnect_connection(request, proveedor):
    error_metodo = _solo_metodo(request, "POST")
    if error_metodo:
        return error_metodo
    permitidos = {valor for valor, _ in ConexionOnixExterna.PROVEEDORES}
    if proveedor not in permitidos:
        return _json_error("La conexion indicada no existe.", status=404, codigo="connection_not_found")
    conexion = ConexionOnixExterna.objects.filter(
        usuario=request.user,
        empresa=request.empresa,
        proveedor=proveedor,
    ).first()
    if conexion:
        conexion.revocar()
    return JsonResponse(
        {
            "ok": True,
            "connections": serializar_conexiones(empresa=request.empresa, usuario=request.user),
        }
    )


@csrf_exempt
@autenticar_token_movil
def history(request):
    error_metodo = _solo_metodo(request, "GET")
    if error_metodo:
        return error_metodo
    try:
        limite = min(100, max(1, int(request.GET.get("limit", "50"))))
    except ValueError:
        return _json_error("El limite del historial no es valido.")
    return JsonResponse(
        {
            "ok": True,
            "messages": serializar_mensajes(
                empresa=request.empresa,
                usuario=request.user,
                limite=limite,
            ),
        }
    )


@csrf_exempt
@autenticar_token_movil
def chat(request):
    error_metodo = _solo_metodo(request, "POST")
    if error_metodo:
        return error_metodo
    try:
        datos = _leer_json(request)
    except ValidationError as exc:
        return _json_error(" ".join(exc.messages))

    pregunta = str(datos.get("pregunta") or "").strip()
    if not pregunta:
        return _json_error("Escribe un mensaje para Onix.", codigo="empty_message")
    if len(pregunta) > 4000:
        return _json_error("El mensaje no puede superar 4,000 caracteres.", codigo="message_too_long")

    try:
        respuesta = responder_consulta(
            pregunta,
            "onix-mobile://chat",
            empresa=request.empresa,
            usuario=request.user,
        )
    except Exception:
        logger.exception("Onix Mobile no pudo responder para la empresa %s", request.empresa.id)
        return _json_error(
            "Onix no pudo completar la consulta en este momento.",
            status=503,
            codigo="assistant_unavailable",
        )

    acciones = [serializar_accion(accion) for accion in respuesta.get("actions", [])]
    respuesta = {**respuesta, "actions": acciones}
    return JsonResponse(
        {
            "ok": True,
            "message": {
                "role": "asistente",
                "content": respuesta.get("answer", ""),
                "created_at": timezone.now().isoformat(),
                "actions": acciones,
            },
            "response": respuesta,
        }
    )


@csrf_exempt
@autenticar_token_movil
def action(request, accion_id):
    error_metodo = _solo_metodo(request, "POST")
    if error_metodo:
        return error_metodo
    try:
        datos = _leer_json(request)
    except ValidationError as exc:
        return _json_error(" ".join(exc.messages))
    decision = str(datos.get("decision") or "").strip().lower()
    if decision not in {"confirmar", "cancelar"}:
        return _json_error("Selecciona confirmar o cancelar la accion.", codigo="invalid_decision")

    try:
        if decision == "confirmar":
            accion = ejecutar_accion(
                accion_id=accion_id,
                empresa=request.empresa,
                usuario=request.user,
            )
            if accion.get("status") == "expirada":
                return _json_error(
                    accion.get("error") or "La vista previa vencio.",
                    codigo="expired_action",
                )
            mensaje = "Accion ejecutada correctamente."
        else:
            accion = cancelar_accion(
                accion_id=accion_id,
                empresa=request.empresa,
                usuario=request.user,
            )
            mensaje = "La accion fue descartada. No se realizaron cambios."
    except PermissionDenied as exc:
        return _json_error(str(exc), status=403, codigo="permission_denied")
    except ValidationError as exc:
        return _json_error(" ".join(exc.messages), codigo="invalid_action")

    return JsonResponse(
        {
            "ok": True,
            "message": mensaje,
            "action": serializar_accion(accion),
        }
    )


@csrf_exempt
@autenticar_token_movil
def invoice_pdf(request, factura_id):
    error_metodo = _solo_metodo(request, "GET")
    if error_metodo:
        return error_metodo
    if not any(
        request.user.tiene_permiso_erp(permiso, request.empresa)
        for permiso in ("puede_ver_facturas", "puede_crear_facturas")
    ):
        return _json_error(
            "El usuario no tiene permiso para descargar facturas.",
            status=403,
            codigo="permission_denied",
        )

    from facturacion.models import ConfiguracionFacturacionEmpresa, Factura
    from facturacion.views import (
        _generar_factura_pdf_bytes,
        _nombre_factura_pdf,
        _resolver_plantilla_factura,
    )

    factura = (
        Factura.objects.filter(empresa=request.empresa, pk=factura_id)
        .select_related("cliente", "empresa", "cai")
        .first()
    )
    if not factura:
        return _json_error(
            "La factura no existe o no pertenece a la empresa activa.",
            status=404,
            codigo="invoice_not_found",
        )
    try:
        configuracion, _ = ConfiguracionFacturacionEmpresa.objects.get_or_create(
            empresa=request.empresa
        )
        plantilla = _resolver_plantilla_factura(configuracion, request.empresa)
        pdf_bytes = _generar_factura_pdf_bytes(request.empresa, factura, plantilla)
        nombre = _nombre_factura_pdf(factura)
    except Exception:
        logger.exception(
            "Onix Mobile no pudo generar el PDF de la factura %s de la empresa %s",
            factura.id,
            request.empresa.id,
        )
        return _json_error(
            "No fue posible generar el PDF de la factura en este momento.",
            status=500,
            codigo="pdf_generation_failed",
        )

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{nombre}"'
    response["Cache-Control"] = "private, no-store"
    return response


@csrf_exempt
@autenticar_token_movil
def logout(request):
    error_metodo = _solo_metodo(request, "POST")
    if error_metodo:
        return error_metodo
    request.onix_mobile_session.revocar()
    return JsonResponse({"ok": True, "message": "Sesion cerrada correctamente."})
