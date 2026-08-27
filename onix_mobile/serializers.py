from core.models import AccionOnix, ConversacionOnix


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
            "mode": "chat",
            "welcome": f"Hola, {nombre}. Soy Onix. Dime que necesitas hacer en {empresa.nombre}.",
        },
        "capabilities": {
            "chat": True,
            "history": True,
            "invoice_drafts": usuario.tiene_permiso_erp("puede_crear_facturas", empresa),
            "voice": False,
            "file_uploads": False,
        },
        "categories": [dict(categoria) for categoria in CATEGORIAS_ONIX],
    }

