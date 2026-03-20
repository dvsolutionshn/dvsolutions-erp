from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login
from django.contrib import messages
from .models import Empresa
from .models import EmpresaModulo


def empresa_login(request, slug):
    empresa = get_object_or_404(Empresa, slug=slug, activa=True)

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            if user.empresa == empresa:
                login(request, user)
                return redirect('dashboard', slug=empresa.slug)
            else:
                messages.error(request, "Usuario no pertenece a esta empresa.")
        else:
            messages.error(request, "Usuario o contraseña incorrectos.")

    return render(request, 'core/login.html', {'empresa': empresa})


def dashboard(request, slug):
    empresa = get_object_or_404(Empresa, slug=slug, activa=True)

    if not request.user.is_authenticated:
        return redirect('empresa_login', slug=slug)

    if request.user.empresa != empresa:
        return redirect('empresa_login', slug=slug)

    modulos_activos = EmpresaModulo.objects.filter(
        empresa=empresa,
        activo=True
    ).select_related('modulo')

    return render(request, 'core/dashboard.html', {
        'empresa': empresa,
        'modulos': modulos_activos
    })

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
from django.contrib.auth import logout
from django.shortcuts import redirect

def cerrar_sesion(request, slug):
    logout(request)
    return redirect('empresa_login', slug=slug)
