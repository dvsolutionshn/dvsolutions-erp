from functools import wraps
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .assistant import responder_consulta
from .forms import (
    EmpresaControlForm,
    PagoLicenciaEmpresaForm,
    PlanComercialForm,
    RolSistemaForm,
    SuperAdminLoginForm,
    UsuarioControlCreateForm,
    UsuarioControlUpdateForm,
)
from .models import Empresa
from .models import EmpresaModulo
from .models import PagoLicenciaEmpresa, PlanComercial, PlanModulo, RolSistema, Usuario


HOST_LOCAL_PATTERNS = {
    "localhost",
    "127.0.0.1",
    "::1",
}


def _client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _login_throttle_key(scope, request):
    return f"login-throttle:{scope}:{_client_ip(request)}"


def _login_block_seconds(scope, request):
    throttle_data = cache.get(_login_throttle_key(scope, request))
    if not throttle_data:
        return 0

    locked_until = throttle_data.get("locked_until")
    if not locked_until:
        return 0

    remaining = int(locked_until - timezone.now().timestamp())
    if remaining <= 0:
        cache.delete(_login_throttle_key(scope, request))
        return 0
    return remaining


def _register_login_failure(scope, request):
    throttle_key = _login_throttle_key(scope, request)
    window_seconds = settings.LOGIN_THROTTLE_WINDOW_SECONDS
    throttle_limit = settings.LOGIN_THROTTLE_LIMIT
    now_ts = timezone.now().timestamp()
    throttle_data = cache.get(throttle_key) or {
        "count": 0,
        "first_failure": now_ts,
        "locked_until": 0,
    }

    first_failure = throttle_data.get("first_failure", now_ts)
    if first_failure + window_seconds <= now_ts:
        throttle_data = {
            "count": 0,
            "first_failure": now_ts,
            "locked_until": 0,
        }

    throttle_data["count"] += 1
    if throttle_data["count"] >= throttle_limit:
        throttle_data["locked_until"] = now_ts + window_seconds

    cache.set(throttle_key, throttle_data, timeout=window_seconds * 2)
    return _login_block_seconds(scope, request)


def _clear_login_failures(scope, request):
    cache.delete(_login_throttle_key(scope, request))


def _host_sin_puerto(request):
    return (request.get_host() or "").split(":")[0].strip().lower()


def _empresa_desde_host(request):
    host = _host_sin_puerto(request)
    if not host or host in HOST_LOCAL_PATTERNS or re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
        return None

    labels = [label for label in host.split(".") if label]
    if len(labels) < 3:
        return None

    subdominio = labels[0]
    if subdominio in {"www", "erp", "test", "app"}:
        return None

    return Empresa.objects.filter(slug=subdominio, activa=True).first()


def _resolver_empresa_request(request, slug=None):
    if slug:
        return get_object_or_404(Empresa, slug=slug, activa=True)

    empresa = _empresa_desde_host(request)
    if empresa:
        return empresa

    raise Http404("No se encontro una empresa valida para este acceso.")


def _usa_host_empresa(request, empresa):
    empresa_host = _empresa_desde_host(request)
    return bool(empresa_host and empresa_host.id == empresa.id)


def _redirect_login_empresa(request, empresa):
    if _usa_host_empresa(request, empresa):
        return redirect("empresa_login_host")
    return redirect("empresa_login", slug=empresa.slug)


def _redirect_dashboard_empresa(request, empresa):
    if _usa_host_empresa(request, empresa):
        return redirect("dashboard_host")
    return redirect("dashboard", slug=empresa.slug)


def _minutes_remaining(seconds):
    if seconds <= 0:
        return 1
    return max(1, (seconds + 59) // 60)


def empresa_login(request, slug=None):
    empresa = _resolver_empresa_request(request, slug)
    throttle_scope = f"empresa:{empresa.slug}"

    if request.method == 'POST':
        bloqueo_restante = _login_block_seconds(throttle_scope, request)
        if bloqueo_restante > 0:
            messages.error(
                request,
                f"Por seguridad bloqueamos temporalmente este acceso. Intenta nuevamente en {_minutes_remaining(bloqueo_restante)} minuto(s).",
            )
            return render(request, 'core/login.html', {'empresa': empresa})

        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            if user.empresa == empresa:
                _clear_login_failures(throttle_scope, request)
                login(request, user)
                return _redirect_dashboard_empresa(request, empresa)
            else:
                bloqueo_restante = _register_login_failure(throttle_scope, request)
                messages.error(request, "Usuario no pertenece a esta empresa.")
                if bloqueo_restante > 0:
                    messages.error(
                        request,
                        f"Por seguridad bloqueamos temporalmente este acceso. Intenta nuevamente en {_minutes_remaining(bloqueo_restante)} minuto(s).",
                    )
        else:
            bloqueo_restante = _register_login_failure(throttle_scope, request)
            messages.error(request, "Usuario o contraseña incorrectos.")
            if bloqueo_restante > 0:
                messages.error(
                    request,
                    f"Por seguridad bloqueamos temporalmente este acceso. Intenta nuevamente en {_minutes_remaining(bloqueo_restante)} minuto(s).",
                )

    return render(request, 'core/login.html', {'empresa': empresa})


def dashboard(request, slug=None):
    empresa = _resolver_empresa_request(request, slug)

    if not request.user.is_authenticated:
        return _redirect_login_empresa(request, empresa)

    if request.user.empresa != empresa:
        return _redirect_login_empresa(request, empresa)

    if not request.user.is_superuser and not empresa.licencia_operativa:
        messages.error(request, "La licencia comercial de esta empresa no se encuentra operativa. Revisa el plan o su estado en control maestro.")
        logout(request)
        return _redirect_login_empresa(request, empresa)

    modulos_activos = empresa.modulos_habilitados()

    return render(request, 'core/dashboard_premium.html', {
        'empresa': empresa,
        'modulos': modulos_activos
    })


@login_required
@require_POST
def asistente_consulta(request, slug=None):
    empresa = _resolver_empresa_request(request, slug)

    if not request.user.is_superuser and request.user.empresa != empresa:
        return JsonResponse({"error": "No autorizado para consultar esta empresa."}, status=403)

    pregunta = (request.POST.get("pregunta") or "").strip()
    pagina = (request.POST.get("pagina") or "").strip()

    if not pregunta:
        return JsonResponse(
            {
                "error": "Escribe una consulta para que el asistente pueda ayudarte.",
            },
            status=400,
        )

    return JsonResponse(responder_consulta(pregunta, pagina))

from core.models import Modulo


#def modulo_view(request, slug, codigo):
  #  empresa = get_object_or_404(Empresa, slug=slug, activa=True)

  #  if not request.user.is_authenticated:
        #return redirect('empresa_login', slug=slug)

   # if request.user.empresa != empresa:
       # return redirect('empresa_login', slug=slug)

   # modulo = get_object_or_404(Modulo, codigo=codigo)

   # return render(request, 'core/modulo_base.html', {
        #'empresa': empresa,
       # 'modulo': modulo
  #  })
def cerrar_sesion(request, slug=None):
    empresa = _resolver_empresa_request(request, slug)
    logout(request)
    return _redirect_login_empresa(request, empresa)


def _superadmin_base_context():
    return {
        "superadmin_dashboard_url": "/control/",
        "superadmin_empresas_url": "/control/empresas/",
        "superadmin_usuarios_url": "/control/usuarios/",
        "superadmin_planes_url": "/control/planes/",
        "superadmin_roles_url": "/control/roles/",
        "superadmin_modulos_url": "/control/modulos/",
        "superadmin_licencias_url": "/control/licencias/",
        "enable_django_admin": settings.ENABLE_DJANGO_ADMIN,
        "django_admin_url": f"/{settings.DJANGO_ADMIN_PATH.strip('/')}/" if settings.ENABLE_DJANGO_ADMIN else None,
    }


def _enriquecer_empresa(empresa):
    empresa.modulos_habilitados_lista = list(empresa.modulos_habilitados())
    empresa.modulos_habilitados_preview = empresa.modulos_habilitados_lista[:4]
    empresa.modulos_habilitados_total = len(empresa.modulos_habilitados_lista)
    empresa.usuarios_relacionados = list(
        Usuario.objects.filter(empresa=empresa)
        .select_related("rol_sistema")
        .order_by("username")
    )
    empresa.usuarios_preview = empresa.usuarios_relacionados[:4]
    empresa.usuarios_total = len(empresa.usuarios_relacionados)
    empresa.pagos_licencia_recientes = list(empresa.pagos_licencia.select_related("plan_comercial")[:5])
    empresa.pagos_licencia_total = empresa.pagos_licencia.count()
    empresa.estado_licencia_resuelto = empresa.estado_licencia_actual
    empresa.licencia_operativa_flag = empresa.licencia_operativa
    if empresa.fecha_vencimiento_plan:
        empresa.dias_restantes_plan = (empresa.fecha_vencimiento_plan - timezone.localdate()).days
    else:
        empresa.dias_restantes_plan = None
    return empresa


def _matriz_modulos_empresa(empresa):
    modulos_catalogo = list(Modulo.objects.filter(es_comercial=True).order_by("nombre"))
    modulos_plan_ids = set()
    if empresa.plan_comercial_id:
        modulos_plan_ids = set(
            PlanModulo.objects.filter(plan=empresa.plan_comercial, activo=True).values_list("modulo_id", flat=True)
        )
    modulos_manual_ids = set(
        EmpresaModulo.objects.filter(empresa=empresa, activo=True).values_list("modulo_id", flat=True)
    )

    resultado = []
    for modulo in modulos_catalogo:
        incluido_plan = modulo.id in modulos_plan_ids
        activo_manual = modulo.id in modulos_manual_ids
        activo_total = incluido_plan or activo_manual
        if incluido_plan and activo_manual:
            origen = "Incluido en plan y ajuste manual"
        elif incluido_plan:
            origen = "Incluido en el plan"
        elif activo_manual:
            origen = "Activado manualmente"
        else:
            origen = "No contratado"
        resultado.append({
            "modulo": modulo,
            "incluido_plan": incluido_plan,
            "activo_manual": activo_manual,
            "activo_total": activo_total,
            "origen": origen,
        })
    return resultado


def superadmin_required(view_func):
    @wraps(view_func)
    @login_required(login_url="/control/login/")
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_superuser:
            messages.error(request, "Este panel privado es exclusivo para superadministradores.")
            return redirect("superadmin_login")
        return view_func(request, *args, **kwargs)

    return _wrapped_view


def superadmin_login(request):
    if request.user.is_authenticated and request.user.is_superuser:
        return redirect("superadmin_dashboard")

    form = SuperAdminLoginForm(request=request, data=request.POST or None)
    if request.method == "POST":
        bloqueo_restante = _login_block_seconds("superadmin", request)
        if bloqueo_restante > 0:
            messages.error(
                request,
                f"Por seguridad bloqueamos temporalmente el acceso maestro. Intenta nuevamente en {_minutes_remaining(bloqueo_restante)} minuto(s).",
            )
        elif form.is_valid():
            _clear_login_failures("superadmin", request)
            login(request, form.get_user())
            return redirect("superadmin_dashboard")
        else:
            bloqueo_restante = _register_login_failure("superadmin", request)
            if bloqueo_restante > 0:
                messages.error(
                    request,
                    f"Por seguridad bloqueamos temporalmente el acceso maestro. Intenta nuevamente en {_minutes_remaining(bloqueo_restante)} minuto(s).",
                )

    return render(request, "core/superadmin_login.html", {"form": form})


def superadmin_logout(request):
    logout(request)
    return redirect("superadmin_login")


@superadmin_required
def superadmin_dashboard(request):
    empresas = list(
        Empresa.objects.annotate(
            usuarios_count=Count("usuario", distinct=True),
            modulos_activos_count=Count("empresamodulo", filter=Q(empresamodulo__activo=True), distinct=True),
        ).order_by("-fecha_creacion")[:6]
    )
    empresas = [_enriquecer_empresa(empresa) for empresa in empresas]
    context = {
        **_superadmin_base_context(),
        "total_empresas_activas": Empresa.objects.filter(activa=True).count(),
        "total_usuarios": Usuario.objects.count(),
        "total_admin_empresa": Usuario.objects.filter(es_administrador_empresa=True).count(),
        "total_modulos_activos": EmpresaModulo.objects.filter(activo=True).count(),
        "total_planes": PlanComercial.objects.filter(activo=True).count(),
        "total_roles": RolSistema.objects.filter(activo=True).count(),
        "total_licencias_operativas": sum(1 for empresa in empresas if empresa.licencia_operativa_flag),
        "total_licencias_vencidas": Empresa.objects.filter(fecha_vencimiento_plan__lt=timezone.localdate()).count(),
        "total_licencias_prueba": Empresa.objects.filter(estado_licencia="prueba").count(),
        "empresas_recientes": empresas,
    }
    return render(request, "core/superadmin_dashboard.html", context)


@superadmin_required
def superadmin_empresas(request):
    empresas = Empresa.objects.annotate(
        usuarios_count=Count("usuario", distinct=True),
        modulos_activos_count=Count("empresamodulo", filter=Q(empresamodulo__activo=True), distinct=True),
    ).order_by("nombre")
    empresas = [_enriquecer_empresa(empresa) for empresa in empresas]
    context = {
        **_superadmin_base_context(),
        "empresas": empresas,
        "resumen": {
            "total": len(empresas),
            "activas": sum(1 for empresa in empresas if empresa.activa),
            "inactivas": sum(1 for empresa in empresas if not empresa.activa),
            "modulos": EmpresaModulo.objects.filter(activo=True).count(),
            "prueba": sum(1 for empresa in empresas if empresa.estado_licencia_resuelto == "prueba"),
            "vencidas": sum(1 for empresa in empresas if empresa.estado_licencia_resuelto == "vencida"),
        },
    }
    return render(request, "core/superadmin_empresas.html", context)


@superadmin_required
def superadmin_empresa_detail(request, empresa_id):
    empresa = _enriquecer_empresa(get_object_or_404(Empresa, id=empresa_id))
    matriz_modulos = _matriz_modulos_empresa(empresa)
    context = {
        **_superadmin_base_context(),
        "empresa_obj": empresa,
        "usuarios_empresa": empresa.usuarios_relacionados,
        "modulos_empresa": empresa.modulos_habilitados_lista,
        "matriz_modulos": matriz_modulos,
        "pagos_licencia": empresa.pagos_licencia.select_related("plan_comercial"),
    }
    return render(request, "core/superadmin_empresa_detalle.html", context)


@superadmin_required
def superadmin_empresa_create(request):
    form = EmpresaControlForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        empresa = form.save()
        messages.success(request, f"Empresa {empresa.nombre} creada correctamente.")
        return redirect("superadmin_empresas")

    return render(request, "core/superadmin_empresa_form.html", {
        **_superadmin_base_context(),
        "form": form,
        "titulo": "Nueva Empresa",
    })


@superadmin_required
def superadmin_empresa_edit(request, empresa_id):
    empresa = get_object_or_404(Empresa, id=empresa_id)
    form = EmpresaControlForm(request.POST or None, request.FILES or None, instance=empresa)
    if request.method == "POST" and form.is_valid():
        empresa = form.save()
        messages.success(request, f"Empresa {empresa.nombre} actualizada correctamente.")
        return redirect("superadmin_empresas")

    return render(request, "core/superadmin_empresa_form.html", {
        **_superadmin_base_context(),
        "form": form,
        "titulo": f"Editar Empresa: {empresa.nombre}",
    })


@superadmin_required
def superadmin_usuarios(request):
    usuarios = Usuario.objects.select_related("empresa").prefetch_related("groups").order_by("username")
    context = {
        **_superadmin_base_context(),
        "usuarios": usuarios,
        "resumen": {
            "total": usuarios.count(),
            "superadmins": usuarios.filter(is_superuser=True).count(),
            "admins_empresa": usuarios.filter(es_administrador_empresa=True).count(),
            "activos": usuarios.filter(is_active=True).count(),
        },
    }
    return render(request, "core/superadmin_usuarios.html", context)


@superadmin_required
def superadmin_planes(request):
    planes = PlanComercial.objects.annotate(
        modulos_count=Count("planmodulo", filter=Q(planmodulo__activo=True), distinct=True),
        empresas_count=Count("empresas", distinct=True),
    ).order_by("nombre")
    context = {
        **_superadmin_base_context(),
        "planes": planes,
    }
    return render(request, "core/superadmin_planes.html", context)


@superadmin_required
def superadmin_plan_create(request):
    form = PlanComercialForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        plan = form.save()
        messages.success(request, f"Plan {plan.nombre} creado correctamente.")
        return redirect("superadmin_planes")

    return render(request, "core/superadmin_plan_form.html", {
        **_superadmin_base_context(),
        "form": form,
        "titulo": "Nuevo Plan Comercial",
    })


@superadmin_required
def superadmin_plan_edit(request, plan_id):
    plan = get_object_or_404(PlanComercial, id=plan_id)
    form = PlanComercialForm(request.POST or None, instance=plan)
    if request.method == "POST" and form.is_valid():
        plan = form.save()
        messages.success(request, f"Plan {plan.nombre} actualizado correctamente.")
        return redirect("superadmin_planes")

    return render(request, "core/superadmin_plan_form.html", {
        **_superadmin_base_context(),
        "form": form,
        "titulo": f"Editar Plan: {plan.nombre}",
    })


@superadmin_required
def superadmin_roles(request):
    roles = RolSistema.objects.order_by("nombre")
    context = {
        **_superadmin_base_context(),
        "roles": roles,
    }
    return render(request, "core/superadmin_roles.html", context)


@superadmin_required
def superadmin_modulos(request):
    modulos = []
    for modulo in Modulo.objects.filter(es_comercial=True).order_by("nombre"):
        modulos.append({
            "modulo": modulo,
            "planes_count": PlanModulo.objects.filter(modulo=modulo, activo=True).values("plan_id").distinct().count(),
            "empresas_count": EmpresaModulo.objects.filter(modulo=modulo, activo=True).values("empresa_id").distinct().count(),
        })

    return render(request, "core/superadmin_modulos.html", {
        **_superadmin_base_context(),
        "modulos": modulos,
    })


@superadmin_required
def superadmin_licencias(request):
    pagos = PagoLicenciaEmpresa.objects.select_related("empresa", "plan_comercial").order_by("-fecha_pago", "-id")
    empresas = [_enriquecer_empresa(empresa) for empresa in Empresa.objects.order_by("nombre")]
    return render(request, "core/superadmin_licencias.html", {
        **_superadmin_base_context(),
        "pagos": pagos[:30],
        "empresas": empresas,
        "resumen": {
            "operativas": sum(1 for empresa in empresas if empresa.licencia_operativa_flag),
            "prueba": sum(1 for empresa in empresas if empresa.estado_licencia_resuelto == "prueba"),
            "suspendidas": sum(1 for empresa in empresas if empresa.estado_licencia_resuelto == "suspendida"),
            "vencidas": sum(1 for empresa in empresas if empresa.estado_licencia_resuelto == "vencida"),
        },
    })


@superadmin_required
def superadmin_empresa_registrar_pago_licencia(request, empresa_id):
    empresa = _enriquecer_empresa(get_object_or_404(Empresa, id=empresa_id))
    form = PagoLicenciaEmpresaForm(request.POST or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        pago = form.save(commit=False)
        pago.empresa = empresa
        if not pago.plan_comercial_id:
            pago.plan_comercial = empresa.plan_comercial
        pago.save()
        if pago.plan_comercial_id and empresa.plan_comercial_id != pago.plan_comercial_id:
            empresa.plan_comercial = pago.plan_comercial
            empresa.save(update_fields=["plan_comercial"])
        empresa.aplicar_pago_licencia(pago)
        messages.success(request, f"Pago de licencia registrado para {empresa.nombre}. La empresa quedo activa hasta {empresa.fecha_vencimiento_plan}.")
        return redirect("superadmin_empresa_detail", empresa_id=empresa.id)

    return render(request, "core/superadmin_licencia_pago_form.html", {
        **_superadmin_base_context(),
        "form": form,
        "empresa_obj": empresa,
        "titulo": f"Registrar pago de licencia: {empresa.nombre}",
    })


@superadmin_required
@require_POST
def superadmin_empresa_suspender_licencia(request, empresa_id):
    empresa = get_object_or_404(Empresa, id=empresa_id)
    empresa.suspender_licencia()
    messages.warning(request, f"La empresa {empresa.nombre} fue suspendida. Su informacion se conserva, pero queda bloqueada para operar.")
    return redirect("superadmin_empresa_detail", empresa_id=empresa.id)


@superadmin_required
@require_POST
def superadmin_empresa_activar_licencia(request, empresa_id):
    empresa = get_object_or_404(Empresa, id=empresa_id)
    if empresa.activar_licencia_manual():
        messages.success(request, f"La empresa {empresa.nombre} fue activada nuevamente con su vigencia actual.")
    else:
        messages.error(request, "No se pudo activar manualmente porque la licencia ya esta vencida. Usa Renovar para registrar un pago y extender la vigencia.")
    return redirect("superadmin_empresa_detail", empresa_id=empresa.id)


@superadmin_required
def superadmin_rol_create(request):
    form = RolSistemaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        rol = form.save()
        messages.success(request, f"Rol {rol.nombre} creado correctamente.")
        return redirect("superadmin_roles")

    return render(request, "core/superadmin_rol_form.html", {
        **_superadmin_base_context(),
        "form": form,
        "titulo": "Nuevo Rol",
    })


@superadmin_required
def superadmin_rol_edit(request, rol_id):
    rol = get_object_or_404(RolSistema, id=rol_id)
    form = RolSistemaForm(request.POST or None, instance=rol)
    if request.method == "POST" and form.is_valid():
        rol = form.save()
        messages.success(request, f"Rol {rol.nombre} actualizado correctamente.")
        return redirect("superadmin_roles")

    return render(request, "core/superadmin_rol_form.html", {
        **_superadmin_base_context(),
        "form": form,
        "titulo": f"Editar Rol: {rol.nombre}",
    })


@superadmin_required
def superadmin_usuario_create(request):
    form = UsuarioControlCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        usuario = form.save()
        messages.success(request, f"Usuario {usuario.username} creado correctamente.")
        return redirect("superadmin_usuarios")

    return render(request, "core/superadmin_usuario_form.html", {
        **_superadmin_base_context(),
        "form": form,
        "titulo": "Nuevo Usuario",
    })


@superadmin_required
def superadmin_usuario_edit(request, usuario_id):
    usuario = get_object_or_404(Usuario, id=usuario_id)
    form = UsuarioControlUpdateForm(request.POST or None, instance=usuario)
    if request.method == "POST" and form.is_valid():
        usuario = form.save()
        messages.success(request, f"Usuario {usuario.username} actualizado correctamente.")
        return redirect("superadmin_usuarios")

    return render(request, "core/superadmin_usuario_form.html", {
        **_superadmin_base_context(),
        "form": form,
        "titulo": f"Editar Usuario: {usuario.username}",
    })
