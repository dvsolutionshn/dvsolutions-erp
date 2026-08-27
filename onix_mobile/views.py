import hashlib
import json
import logging

from django.conf import settings
from django.contrib.auth import authenticate
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from core.assistant import responder_consulta
from core.models import Empresa
from core.onix_access import onix_disponible_para_empresa
from core.onix_actions import cancelar_accion, ejecutar_accion

from .authentication import autenticar_token_movil
from .models import SesionOnixMovil
from .serializers import construir_bootstrap, serializar_accion, serializar_mensajes


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
def logout(request):
    error_metodo = _solo_metodo(request, "POST")
    if error_metodo:
        return error_metodo
    request.onix_mobile_session.revocar()
    return JsonResponse({"ok": True, "message": "Sesion cerrada correctamente."})

