from django.urls import reverse

from .context_processors import erp_access


# This catalog affects only the demo dashboard; access stays with the ERP permissions.
MODULES = (
    ("contabilidad", "modulo_contabilidad", "Contabilidad", "Finanzas", "Gestiona asientos, libros y estados financieros.", "contabilidad_dashboard", "calculator", "accounting"),
    ("facturacion", "modulo_facturacion", "Facturación", "Operación", "Facturas, clientes y productos conectados en un mismo lugar.", "facturacion_dashboard", "file-text", "billing"),
    ("recibos", "recibos", "Recibos", "Cobranza", "Consulta comprobantes de pago y su trazabilidad.", "recibos_dashboard", "receipt", "receipts"),
    ("cotizaciones", "cotizaciones", "Cotizaciones", "Comercial", "Prepara propuestas y conviértelas en ventas.", "cotizaciones_dashboard", "clipboard-list", "billing"),
    ("punto_venta", "modulo_pos", "Punto de Venta", "Venta directa", "Tu caja, inventario y ventas en una sola operación.", "punto_venta", "shopping-bag", "receipts"),
    ("rrhh", "modulo_rrhh", "Recursos Humanos", "Talento", "Gestiona personal, planillas y vacaciones.", "rrhh_dashboard", "users", "building"),
    ("crm_marketing", "modulo_crm", "CRM y Marketing", "Clientes", "Organiza campañas y seguimiento comercial.", "crm_dashboard", "messages-square", "building"),
    ("agenda_citas", "modulo_citas", "Citas", "Agenda", "Coordina horarios, servicios y citas de tus clientes.", "agenda_citas", "calendar-days", "billing"),
    ("clinica_medica", "modulo_clinica", "Clínica Médica", "Salud", "Pacientes, tratamientos y expedientes clínicos.", "clinica_dashboard", "heart-pulse", "building"),
    ("tecnicentro", "modulo_tecnicentro", "Tecnicentro", "Taller", "Recepción, diagnóstico y órdenes de trabajo.", "tecnicentro_dashboard", "wrench", "building"),
)


def dashboard_context(request, empresa, modulos):
    access = erp_access(request)["erp_access"]
    enabled = {modulo.codigo for modulo in modulos}
    # Receipts are included in billing, rather than a separately licensed feature.
    if "facturacion" in enabled:
        enabled.add("recibos")
    cards = []
    available = []
    for code, permission, title, category, description, route, icon, image in MODULES:
        if code in enabled and access.get(permission):
            cards.append({
                "title": title, "category": category, "description": description,
                "url": reverse(route, args=[empresa.slug]), "icon": icon,
                "image": f"core/img/demo-dashboard/{image}.png",
            })
        elif code not in enabled:
            available.append({"title": title, "icon": icon})

    shortcuts = []
    for permission, title, route, icon in (
        ("clientes", "Clientes", "clientes_facturacion", "users"),
        ("productos", "Productos", "productos_facturacion", "package"),
        ("reportes", "Reportes", "reportes_facturacion", "chart-pie"),
    ):
        if access.get(permission):
            shortcuts.append({"title": title, "url": reverse(route, args=[empresa.slug]), "icon": icon})
    admin_links = []
    if request.user.is_superuser or request.user.es_administrador_empresa:
        admin_links = [
            {"title": "Bitácora ERP", "url": reverse("auditoria_empresa", args=[empresa.slug]), "icon": "notebook-tabs"},
            {"title": "Mi respaldo", "url": reverse("empresa_respaldo", args=[empresa.slug]), "icon": "cloud-download"},
        ]
    return {"demo_modules": cards, "demo_solutions": available, "demo_shortcuts": shortcuts + admin_links, "demo_admin_links": admin_links}
