"""Edición de permisos mediante una nueva asignación local, nunca del rol compartido."""

import hashlib
import json
import uuid
from types import SimpleNamespace

from django.contrib import messages
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .audit_context import audit_scope
from .clinical_permissions import PERMISOS_CLINICOS, TODOS_LOS_PERMISOS_CLINICOS
from .mobile_users import (
    _contexto_usuarios_app, _numero_canonico, respuesta_usuarios_app,
    url_usuarios_app, usuarios_disponibles_app, validar_usuarios_app,
)
from .models import Empresa, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso


VERSION_SALT = "core.mobile_user_permissions.edit.v1"
VERSION_MAX_AGE = 3600


def url_editar_permisos_usuario_app(empresa, usuario):
    usuario_id = getattr(usuario, "pk", usuario)
    return f"{reverse('usuario_clinico_permisos_editar_app', args=[empresa.slug, usuario_id])}?app=1"


def permisos_usuario_editables_app(request, empresa, rol):
    """Capacidad de presentación; el destino vuelve a comprobar usuario y asignación."""
    return bool(
        usuarios_disponibles_app(request, empresa)
        and rol and rol.activo and rol.es_rol_clinico and rol.usa_permisos_clinicos_granulares
    )


def _autorizar(request, empresa, usuario_id):
    if not usuarios_disponibles_app(request, empresa):
        raise PermissionDenied("No tiene permiso para administrar usuarios de esta empresa.")
    if request.method not in {"GET", "HEAD", "POST"}:
        raise PermissionDenied("Método no disponible para editar permisos.")
    if set(request.GET) != {"app"} or request.GET.getlist("app") != ["1"]:
        raise PermissionDenied("La edición local requiere únicamente app=1 en la URL.")
    identificador = _numero_canonico(usuario_id)
    partes = request.path.strip("/").split("/")
    if "usuarios-clinicos" in partes:
        posicion = partes.index("usuarios-clinicos") + 1
        if posicion >= len(partes) or _numero_canonico(partes[posicion]) != identificador:
            raise PermissionDenied("Identificador de usuario no válido.")
    if request.method in {"GET", "HEAD"}:
        if request.body or request.POST or request.FILES:
            raise PermissionDenied("La consulta no admite un cuerpo ni archivos.")
    else:
        permitidos = set(TODOS_LOS_PERMISOS_CLINICOS) | {
            "app", "csrfmiddlewaretoken", "version_permisos", "confirmar_permisos",
        }
        if (
            request.FILES or set(request.POST) - permitidos
            or any(len(request.POST.getlist(clave)) != 1 for clave in request.POST)
            or request.POST.getlist("app") != ["1"]
            or request.POST.getlist("confirmar_permisos") != ["1"]
            or list(request.POST)[-1:] != ["confirmar_permisos"]
            or not request.POST.get("version_permisos")
            or any(request.POST[campo] != "1" for campo in TODOS_LOS_PERMISOS_CLINICOS if campo in request.POST)
        ):
            raise PermissionDenied("El formulario de permisos está incompleto o contiene campos no admitidos.")
    return identificador


def _estado(usuario, empresa, *, bloquear=False):
    asignaciones = UsuarioEmpresaPermiso.objects.filter(usuario=usuario, empresa=empresa)
    if bloquear:
        asignaciones = asignaciones.select_for_update()
    asignacion = asignaciones.first()
    rol_id = asignacion.rol_sistema_id if asignacion and asignacion.activo else usuario.rol_sistema_id
    roles = RolSistema.objects.select_for_update() if bloquear else RolSistema.objects.all()
    rol = get_object_or_404(roles, pk=rol_id) if rol_id else None
    return asignacion, rol


def _digest(actor, empresa, usuario, asignacion, rol):
    datos = {
        "actor": {
            "id": actor.pk, "super": actor.is_superuser,
            "administrar": actor.puede_administrar_usuarios_clinicos,
        },
        "empresa": {"id": empresa.pk, "slug": empresa.slug, "activa": empresa.activa, "tipo": empresa.tipo_solucion},
        "usuario": {
            "id": usuario.pk, "empresa": usuario.empresa_id, "rol": usuario.rol_sistema_id,
            "administrador": usuario.es_administrador_empresa, "staff": usuario.is_staff,
            "super": usuario.is_superuser, "activo": usuario.is_active, "retirado": usuario.retirado_control,
        },
        "asignacion": None if asignacion is None else {
            "id": asignacion.pk, "rol": asignacion.rol_sistema_id, "activo": asignacion.activo,
            "fecha": asignacion.fecha_actualizacion.isoformat(),
        },
        "rol": None if rol is None else {
            campo.attname: campo.value_from_object(rol) for campo in rol._meta.concrete_fields
        },
    }
    serializado = json.dumps(datos, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


def _version(request, empresa, usuario, asignacion, rol):
    return signing.dumps({
        "actor": request.user.pk, "empresa": empresa.pk, "usuario": usuario.pk,
        "estado": _digest(request.user, empresa, usuario, asignacion, rol),
    }, salt=VERSION_SALT)


def _leer_version(request, empresa, usuario):
    try:
        datos = signing.loads(request.POST["version_permisos"], salt=VERSION_SALT, max_age=VERSION_MAX_AGE)
    except signing.BadSignature:
        return None
    if not isinstance(datos, dict) or not isinstance(datos.get("estado"), str) or len(datos["estado"]) != 64 or any(
        datos.get(clave) != valor
        for clave, valor in {"actor": request.user.pk, "empresa": empresa.pk, "usuario": usuario.pk}.items()
    ):
        return None
    return datos


def _render(request, empresa, usuario, asignacion, rol, *, errores=(), estado=200, obsoleto=False):
    editable = permisos_usuario_editables_app(request, empresa, rol)
    bound = request.method == "POST"
    grupos = [
        {"nombre": nombre, "permisos": [
            {"campo": campo, "etiqueta": etiqueta,
             "activo": request.POST.get(campo) == "1" if bound else bool(getattr(rol, campo))}
            for campo, etiqueta in permisos
        ]}
        for nombre, permisos in PERMISOS_CLINICOS
    ] if editable or bound else []
    url = url_editar_permisos_usuario_app(empresa, usuario)
    identidad_empresa = {"id": empresa.pk, "nombre": empresa.nombre, "slug": empresa.slug}
    contexto = {
        **_contexto_usuarios_app(request, empresa),
        "empresa": identidad_empresa, "empresa_original": identidad_empresa,
        "usuario_gestionado": {
            "id": usuario.pk, "nombre": usuario.get_full_name().strip() or usuario.username,
            "username": usuario.username, "is_active": usuario.is_active,
        },
        "rol_actual": {"id": rol.pk, "nombre": rol.nombre} if rol else None,
        "mobile_user_permission_groups": grupos,
        "mobile_user_permissions_editable": editable,
        "mobile_user_permissions_bound": bound,
        "mobile_user_permissions_stale": obsoleto,
        "mobile_user_permissions_errors": list(errores),
        "mobile_user_permissions_version": request.POST.get("version_permisos", "") if bound else (
            _version(request, empresa, usuario, asignacion, rol) if editable else ""
        ),
        "mobile_user_permissions_independent_admin": bool(usuario.es_administrador_empresa),
        "app_user_permissions_edit_form_url": url,
        "app_user_permissions_edit_reload_url": url,
        "app_user_permissions_edit_back_url": url_usuarios_app("usuarios_clinicos", empresa),
        "app_user_permissions_assign_role_url": url_usuarios_app("usuario_clinico_permisos", empresa, usuario),
        "app_home_disponible": False, "app_hide_agenda_navigation": True,
        "app_hide_patient_navigation": True,
        "app_modules_url": reverse("agenda_mobile_modules_screen", args=[empresa.slug]),
    }
    return respuesta_usuarios_app(render(request, "core/mobile_user_permissions_edit.html", contexto, status=estado))


def _registrar_cambio_permisos(request, empresa, usuario, asignacion, rol_anterior, rol_nuevo, anteriores, nuevos):
    """Registro obligatorio dentro de la misma transacción; no depende del signal tolerante."""
    from .audit_signals import _request_data

    metadata = _request_data("modificar")
    RegistroAuditoria.objects.create(
        empresa=empresa, usuario=request.user, accion="modificar", modulo="Configuracion",
        app_label="core", modelo="usuarioempresapermiso", objeto_id=str(asignacion.pk),
        objeto_representacion=f"Permisos clínicos locales del usuario #{usuario.pk}",
        cambios={
            **{campo: {"anterior": anteriores[campo], "nuevo": nuevos[campo]}
               for campo in TODOS_LOS_PERMISOS_CLINICOS if anteriores[campo] != nuevos[campo]},
            "rol_sistema": {"anterior": rol_anterior.pk, "nuevo": rol_nuevo.pk},
        },
        motivo="Edición móvil de permisos clínicos únicamente en esta empresa.",
        ruta=metadata["path"] or request.path, metodo_http=metadata["method"] or request.method,
        direccion_ip=metadata["ip"], agente_usuario=metadata["user_agent"],
        identificador_solicitud=metadata["request_id"],
    )


class _VersionObsoleta(Exception):
    pass


def gestionar_permisos_usuario_app(request, empresa, usuario_id):
    """Usa la resolución actual: snapshot global nuevo y asignación exclusiva de la empresa."""
    from .views import _usuarios_operativos_de_empresa

    identificador = _autorizar(request, empresa, usuario_id)
    usuario = get_object_or_404(_usuarios_operativos_de_empresa(empresa), pk=identificador)
    validar_usuarios_app(empresa, usuario.pk)
    asignacion, rol = _estado(usuario, empresa)
    if request.method != "POST":
        return _render(request, empresa, usuario, asignacion, rol)
    version = _leer_version(request, empresa, usuario)
    if version is None:
        return _render(request, empresa, usuario, asignacion, rol, estado=400, obsoleto=True,
                       errores=["La versión del formulario no es válida o ha vencido. Recargue para volver a revisar."])
    try:
        with transaction.atomic(), audit_scope(user=request.user):
            # La empresa sólo se consulta. Un bloqueo exclusivo aquí puede
            # interferir con las referencias de auditoría de usuarios compartidos.
            empresa_actual = Empresa.objects.get(pk=empresa.pk)
            # Orden común incluso cuando dos administradores se editan entre sí.
            # No FOR UPDATE sobre el DISTINCT/M2M del catálogo original.
            usuarios_bloqueados = {
                fila.pk: fila for fila in Usuario.objects.select_for_update()
                .filter(pk__in={request.user.pk, identificador}).order_by("pk")
            }
            actor = usuarios_bloqueados.get(request.user.pk)
            if actor is None or not usuarios_disponibles_app(SimpleNamespace(user=actor), empresa_actual):
                raise PermissionDenied("Ya no tiene permiso para administrar usuarios de esta empresa.")
            usuario = usuarios_bloqueados.get(identificador)
            if usuario is None:
                raise Http404("No se encontró el usuario administrable.")
            if not _usuarios_operativos_de_empresa(empresa_actual).filter(pk=usuario.pk).exists():
                raise PermissionDenied("El usuario ya no pertenece al conjunto administrable de esta empresa.")
            validar_usuarios_app(empresa_actual, usuario.pk)
            asignacion, rol = _estado(usuario, empresa_actual, bloquear=True)
            if version["estado"] != _digest(actor, empresa_actual, usuario, asignacion, rol):
                raise _VersionObsoleta
            if not permisos_usuario_editables_app(SimpleNamespace(user=actor), empresa_actual, rol):
                raise PermissionDenied("Sólo se editan permisos de un rol clínico granular activo.")
            anteriores = {campo: bool(getattr(rol, campo)) for campo in TODOS_LOS_PERMISOS_CLINICOS}
            nuevos = {campo: request.POST.get(campo) == "1" for campo in TODOS_LOS_PERMISOS_CLINICOS}
            if anteriores != nuevos:
                valores = {campo.attname: campo.value_from_object(rol)
                           for campo in rol._meta.concrete_fields if not campo.primary_key}
                valores.update(nombre="Permisos clínicos de asignación local", codigo=f"local-{uuid.uuid4().hex}")
                valores.update(nuevos)
                copia = RolSistema.objects.create(**valores)
                if asignacion is None:
                    try:
                        asignacion = UsuarioEmpresaPermiso.objects.create(
                            usuario=usuario, empresa=empresa_actual, rol_sistema=copia, activo=True,
                        )
                    except IntegrityError as error:
                        raise _VersionObsoleta from error
                else:
                    asignacion.rol_sistema = copia
                    asignacion.activo = True
                    asignacion.save(update_fields=["rol_sistema", "activo", "fecha_actualizacion"])
                _registrar_cambio_permisos(request, empresa_actual, usuario, asignacion, rol, copia, anteriores, nuevos)
            else:
                messages.info(request, "Los permisos ya coincidían; no se realizaron cambios.")
    except _VersionObsoleta:
        return _render(request, empresa, usuario, asignacion, rol, estado=409, obsoleto=True,
                       errores=["Los permisos o la asignación cambiaron desde que abrió el formulario. El borrador no se guardó. Recargue y vuelva a revisar."])
    if anteriores != nuevos:
        messages.success(request, "Se actualizaron los permisos de este usuario únicamente en esta empresa.")
    return respuesta_usuarios_app(redirect(url_editar_permisos_usuario_app(empresa, usuario)))
