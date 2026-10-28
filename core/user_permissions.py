"""Permisos individuales por empresa usando el mismo motor de los roles."""

from .clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, PERMISOS_CLINICOS, PERMISOS_ERP


def catalogo_permisos_empresa(empresa):
    return PERMISOS_CLINICOS if empresa.slug in EMPRESAS_CLINICAS_CON_ROLES else PERMISOS_ERP


def seleccion_permisos_empresa(datos, empresa_id):
    nombre = f"empresa_permisos_{empresa_id}"
    if hasattr(datos, "getlist"):
        return datos.getlist(nombre)
    valores = datos.get(nombre, [])
    return valores if isinstance(valores, (list, tuple)) else [valores]


def rol_desde_permisos_personalizados(empresa, permisos):
    # Es una vista de los permisos, no un nuevo rol compartido en el catálogo.
    from .models import RolSistema

    es_clinica = empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
    valores = permisos if isinstance(permisos, dict) else {}
    return RolSistema(
        nombre="Permisos personalizados",
        activo=True,
        es_rol_clinico=es_clinica,
        usa_permisos_clinicos_granulares=es_clinica,
        usa_permisos_clinicos_v2=es_clinica,
        usa_permisos_acciones_v3=True,
        **{
            campo: valores.get(campo) is True
            for _, grupo in catalogo_permisos_empresa(empresa)
            for campo, _ in grupo
        },
    )


def validar_permisos_usuario(form, cleaned_data):
    """Validar sólo las empresas autorizadas del formulario de cuenta."""
    from .models import RolSistema

    empresas = set(cleaned_data.get("empresas_acceso") or [])
    if cleaned_data.get("empresa"):
        empresas.add(cleaned_data["empresa"])
    for empresa in empresas:
        modo = form.data.get(f"empresa_modo_permisos_{empresa.pk}")
        if modo is None:
            continue  # Compatibilidad con formularios anteriores e integraciones.
        if modo == "personalizado":
            permitidos = {campo for _, grupo in catalogo_permisos_empresa(empresa) for campo, _ in grupo}
            valores = seleccion_permisos_empresa(form.data, empresa.pk)
            if set(valores) - permitidos:
                form.add_error(None, f"Los permisos seleccionados no pertenecen al catálogo de {empresa.nombre}.")
            for campo in ("es_administrador_empresa", "is_superuser"):
                if cleaned_data.get(campo):
                    form.add_error(campo, "Desmarca el acceso completo para aplicar permisos personalizados.")
        elif modo == "rol":
            rol_id = form.data.get(f"empresa_rol_{empresa.pk}")
            rol = None
            if rol_id:
                if str(rol_id).isdigit():
                    rol = RolSistema.objects.filter(pk=rol_id, activo=True).first()
                if rol is None:
                    form.add_error(None, f"Selecciona un rol activo válido para {empresa.nombre}.")
                    continue
            else:
                rol = cleaned_data.get("rol_sistema")
            es_clinica = empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
            if rol and rol.es_rol_clinico != es_clinica:
                form.add_error(None, f"El rol seleccionado no pertenece al catálogo de {empresa.nombre}.")
        else:
            form.add_error(None, f"Selecciona una forma válida de configurar permisos para {empresa.nombre}.")
