from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.core.cache import cache
from django.shortcuts import get_object_or_404, redirect, render

from core.models import Empresa


MAX_INTENTOS = 5
BLOQUEO_SEGUNDOS = 15 * 60


def _ip(request):
    return (request.META.get("HTTP_X_FORWARDED_FOR") or request.META.get("REMOTE_ADDR") or "unknown").split(",")[0].strip()


def _clave_intentos(empresa, request):
    return f"login-tecnicentro:{empresa.id}:{_ip(request)}"


def login_tecnicentro(request, empresa_slug):
    empresa = get_object_or_404(Empresa, slug=empresa_slug, activa=True)
    if not empresa.tiene_modulo_activo("tecnicentro"):
        return render(request, "tecnicentro/login.html", {"empresa": empresa, "modulo_inactivo": True}, status=403)

    if request.user.is_authenticated:
        if request.user.is_superuser or (
            request.user.empresa_id == empresa.id and request.user.tiene_alguna_permision_tecnicentro
        ):
            return redirect("tecnicentro_dashboard", empresa_slug=empresa.slug)
        logout(request)

    clave = _clave_intentos(empresa, request)
    intentos = int(cache.get(clave, 0) or 0)
    if request.method == "POST":
        if intentos >= MAX_INTENTOS:
            messages.error(request, "Acceso temporalmente bloqueado por varios intentos. Espera 15 minutos.")
        else:
            identificador = (request.POST.get("username") or "").strip()
            password = request.POST.get("password") or ""
            usuario = authenticate(request, username=identificador, password=password, empresa=empresa)
            if usuario and usuario.empresa_id == empresa.id and usuario.tiene_alguna_permision_tecnicentro:
                cache.delete(clave)
                login(request, usuario)
                return redirect("tecnicentro_dashboard", empresa_slug=empresa.slug)
            cache.set(clave, intentos + 1, timeout=BLOQUEO_SEGUNDOS)
            if usuario and not usuario.tiene_alguna_permision_tecnicentro:
                messages.error(request, "Tu cuenta no tiene permisos para operar Garage OS.")
            else:
                messages.error(request, "Correo o contrasena incorrectos para este tecnicentro.")

    return render(request, "tecnicentro/login.html", {"empresa": empresa})


def logout_tecnicentro(request, empresa_slug):
    empresa = get_object_or_404(Empresa, slug=empresa_slug)
    logout(request)
    return redirect("tecnicentro_login", empresa_slug=empresa.slug)
