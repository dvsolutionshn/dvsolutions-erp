from django.conf import settings

from core.models import AccionOnix, ConfiguracionOnix, ConversacionOnix


CATEGORIAS_ONIX = (
    {
        "id": "resumen",
        "title": "Resumen",
        "description": "Indicadores y situacion general de la empresa.",
        "icon": "dashboard",
        "status": "available",
        "prompt": "Dame un resumen ejecutivo de la empresa hoy",
    },
    {
        "id": "facturas",
        "title": "Facturas",
        "description": "Consulta facturas y prepara nuevos borradores.",
        "icon": "receipt_long",
        "status": "available",
        "prompt": "Muestrame las facturas mas recientes",
    },
    {
        "id": "cobros",
        "title": "Cobros",
        "description": "Saldos pendientes y cuentas por cobrar.",
        "icon": "payments",
        "status": "available",
        "prompt": "Que clientes tienen saldos pendientes",
    },
    {
        "id": "clientes",
        "title": "Clientes",
        "description": "Busca y revisa clientes de la empresa.",
        "icon": "groups",
        "status": "available",
        "prompt": "Muestrame los clientes recientes",
    },
    {
        "id": "productos",
        "title": "Productos",
        "description": "Catalogo de productos y servicios.",
        "icon": "inventory_2",
        "status": "available",
        "prompt": "Busca productos y servicios disponibles",
    },
    {
        "id": "calendario",
        "title": "Calendario",
        "description": "Citas de hoy y proximas actividades.",
        "icon": "calendar_month",
        "status": "next",
        "prompt": "Que citas tengo hoy",
    },
    {
        "id": "gastos",
        "title": "Gastos",
        "description": "Registro y control de gastos.",
        "icon": "trending_down",
        "status": "next",
        "prompt": "Dame un resumen de gastos del mes",
    },
    {
        "id": "pagos",
        "title": "Pagos",
        "description": "Pagos realizados y pendientes.",
        "icon": "account_balance_wallet",
        "status": "next",
        "prompt": "Muestrame los pagos pendientes",
    },
    {
        "id": "bancos",
        "title": "Bancos",
        "description": "Estados de cuenta y conciliacion.",
        "icon": "account_balance",
        "status": "next",
        "prompt": "Quiero revisar un estado de cuenta",
    },
    {
        "id": "inquilinos",
        "title": "Inquilinos",
        "description": "Mensualidades, contratos y cobros de alquiler.",
        "icon": "apartment",
        "status": "next",
        "prompt": "Muestrame el control de inquilinos",
    },
)


def serializar_accion(accion):
    if isinstance(accion, dict):
        datos = dict(accion)
        datos.pop("decision_url", None)
        if datos.get("id"):
            datos["endpoint"] = f"/api/onix/mobile/v1/actions/{datos['id']}/"
        return datos

    datos = {
        "id": str(accion.id),
        "type": accion.tipo,
        "status": accion.estado,
        "expires_at": accion.expira_en.isoformat(),
        "endpoint": f"/api/onix/mobile/v1/actions/{accion.id}/",
        **(accion.vista_previa or {}),
    }
    if accion.resultado:
        datos["result"] = accion.resultado
    if accion.detalle_error:
        datos["error"] = accion.detalle_error
    return datos


def serializar_mensajes(*, empresa, usuario, limite=50):
    conversacion = (
        ConversacionOnix.objects.filter(empresa=empresa, usuario=usuario, activa=True)
        .order_by("-fecha_actualizacion", "-id")
        .first()
    )
    if not conversacion:
        return []

    mensajes = list(conversacion.mensajes.order_by("-fecha", "-id")[:limite])
    mensajes.reverse()
    ids_acciones = {
        str(accion_id)
        for mensaje in mensajes
        for accion_id in (mensaje.metadatos or {}).get("acciones", [])
    }
    acciones = {
        str(accion.id): serializar_accion(accion)
        for accion in AccionOnix.objects.filter(
            id__in=ids_acciones,
            empresa=empresa,
            usuario=usuario,
        )
    }
    return [
        {
            "id": mensaje.id,
            "role": mensaje.rol,
            "content": mensaje.contenido,
            "created_at": mensaje.fecha.isoformat(),
            "actions": [
                acciones[str(accion_id)]
                for accion_id in (mensaje.metadatos or {}).get("acciones", [])
                if str(accion_id) in acciones
            ],
        }
        for mensaje in mensajes
    ]


def construir_bootstrap(*, usuario, empresa):
    nombre = usuario.get_full_name().strip() or usuario.username
    configuracion = ConfiguracionOnix.objects.filter(empresa=empresa).first()
    configuracion_activa = configuracion.activo if configuracion else True
    consultas_activas = configuracion.herramientas_consulta_activas if configuracion else True
    acciones_configuradas = (
        configuracion.herramientas_accion_activas
        if configuracion
        else empresa.slug == "demo_1"
    )
    modelo = (
        configuracion.modelo
        if configuracion and configuracion.modelo
        else getattr(settings, "ONIX_MODEL", "gpt-5.6-luna")
    )
    ia_activa = bool(
        getattr(settings, "ONIX_ENABLED", False)
        and getattr(settings, "OPENAI_API_KEY", "")
        and configuracion_activa
    )
    facturacion_activa = empresa.tiene_modulo_activo("facturacion")
    agenda_activa = empresa.tiene_modulo_activo("agenda_citas")
    puede_ver_facturas = usuario.tiene_permiso_erp("puede_ver_facturas", empresa)
    puede_clientes = usuario.tiene_permiso_erp("puede_clientes", empresa) or puede_ver_facturas
    puede_productos = usuario.tiene_permiso_erp("puede_productos", empresa) or usuario.tiene_permiso_erp(
        "puede_facturas", empresa
    )
    puede_cobros = puede_ver_facturas or usuario.tiene_permiso_erp("puede_cxc", empresa)
    puede_pagos = any(
        usuario.tiene_permiso_erp(permiso, empresa)
        for permiso in ("puede_recibos", "puede_cxc", "puede_ver_facturas")
    )
    puede_citas = agenda_activa and usuario.tiene_permiso_erp("puede_citas", empresa)
    puede_preparar_facturas = bool(
        facturacion_activa
        and acciones_configuradas
        and usuario.tiene_permiso_erp("puede_crear_facturas", empresa)
    )
    categorias_disponibles = {
        "resumen": consultas_activas,
        "facturas": consultas_activas and facturacion_activa and puede_ver_facturas,
        "cobros": consultas_activas and facturacion_activa and puede_cobros,
        "clientes": consultas_activas and facturacion_activa and puede_clientes,
        "productos": consultas_activas and facturacion_activa and puede_productos,
        "calendario": consultas_activas and puede_citas,
        "pagos": consultas_activas and facturacion_activa and puede_pagos,
    }
    categorias = []
    for categoria_base in CATEGORIAS_ONIX:
        categoria = dict(categoria_base)
        if categoria["id"] in categorias_disponibles:
            categoria["status"] = "available" if categorias_disponibles[categoria["id"]] else "restricted"
        categorias.append(categoria)

    return {
        "api_version": "v1",
        "user": {
            "id": usuario.id,
            "name": nombre,
            "username": usuario.username,
            "email": usuario.email or "",
        },
        "company": {
            "id": empresa.id,
            "name": empresa.nombre,
            "slug": empresa.slug,
            "logo_url": empresa.logo.url if empresa.logo else None,
        },
        "assistant": {
            "name": "Onix",
            "mode": "ai" if ia_activa else "guided",
            "status": "IA activa" if ia_activa else "Modo guiado",
            "model": modelo if ia_activa else "",
            "welcome": f"Hola, {nombre}. Soy Onix. Dime que necesitas hacer en {empresa.nombre}.",
        },
        "capabilities": {
            "chat": True,
            "history": True,
            "ai": ia_activa,
            "query_tools": consultas_activas,
            "invoice_drafts": puede_preparar_facturas,
            "calendar": puede_citas,
            "payments": facturacion_activa and puede_pagos,
            "voice": False,
            "file_uploads": False,
        },
        "categories": categorias,
    }
