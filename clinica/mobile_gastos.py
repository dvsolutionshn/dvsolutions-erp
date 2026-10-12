"""Navegación y presentación móvil del flujo existente de Gastos Adicionales."""

from django.db.models import Q
from django.shortcuts import render
from django.urls import reverse

from core.models import RegistroAuditoria
from facturacion.mobile_presentation import preparar_historial_factura_mobile


def es_gastos_app(request):
    return request.GET.get("app") == "1" or request.POST.get("app") == "1"


def url_gastos_app(vista, empresa, gasto_id=None, **parametros):
    from urllib.parse import urlencode

    args = [empresa.slug] + ([gasto_id] if gasto_id is not None else [])
    return f"{reverse(vista, args=args)}?{urlencode({'app': '1', **parametros})}"


def contexto_gastos_app(request, empresa, gasto=None):
    permiso = lambda nombre: request.user.tiene_permiso_erp(nombre, empresa)
    app_base = reverse("agenda_mobile", args=[empresa.slug])
    contexto = {
        "modo_app": True,
        "app_base_url": app_base,
        "app_invoices_url": f"{app_base}#facturas-app",
        "app_modules_url": f"{app_base}#mas-funciones-app",
        "app_gastos_url": url_gastos_app("clinica_gastos_adicionales", empresa),
        "app_gasto_create_url": url_gastos_app("clinica_gasto_adicional_crear", empresa),
        "app_gasto_patient_search_url": url_gastos_app("clinica_gastos_adicionales_pacientes_buscar", empresa),
        "app_gasto_product_search_url": url_gastos_app("clinica_gastos_adicionales_productos_buscar", empresa),
        "can_view_gastos": permiso("puede_ver_gastos_adicionales"),
        "can_create_gasto": permiso("puede_crear_gastos_adicionales"),
        "can_edit_gasto": bool(gasto and gasto.editable and permiso("puede_editar_gastos_adicionales")),
        "can_send_gasto": bool(gasto and permiso("puede_enviar_gastos_adicionales")),
        "can_convert_gasto": bool(
            gasto and empresa.tiene_modulo_activo("facturacion")
            and all(permiso(nombre) for nombre in (
                "puede_convertir_gastos_adicionales_factura", "puede_crear_facturas",
                "puede_editar_facturas", "puede_ver_facturas",
            ))
        ),
        "app_invoice_url": "",
    }
    contexto["app_gasto_form_url"] = url_gastos_app("clinica_gasto_adicional_editar", empresa, gasto.pk) if gasto else contexto["app_gasto_create_url"]
    if gasto:
        for nombre, vista in {
            "detail": "clinica_gasto_adicional_detalle",
            "edit": "clinica_gasto_adicional_editar",
            "pdf": "clinica_gasto_adicional_pdf",
            "send_email": "clinica_gasto_adicional_enviar_correo",
            "send_whatsapp": "clinica_gasto_adicional_enviar_whatsapp",
            "convert": "clinica_gasto_adicional_convertir",
        }.items():
            contexto[f"app_gasto_{nombre}_url"] = url_gastos_app(vista, empresa, gasto.pk)
        contexto["app_gasto_email_url"] = contexto["app_gasto_send_email_url"]
        contexto["app_gasto_whatsapp_url"] = contexto["app_gasto_send_whatsapp_url"]
        if gasto.factura_id and permiso("puede_ver_facturas"):
            vista = "editar_factura" if gasto.factura.estado == "borrador" and permiso("puede_editar_facturas") else "ver_factura"
            contexto["app_invoice_url"] = url_gastos_app(vista, empresa, gasto.factura_id)
    return contexto


def respuesta_gastos_app(request, response):
    if es_gastos_app(request):
        response["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"
    return response


def render_gastos(request, template_name, contexto):
    if es_gastos_app(request):
        contexto.update(contexto_gastos_app(request, contexto["empresa"], contexto.get("gasto")))
    return respuesta_gastos_app(request, render(request, template_name, contexto))


def historial_gastos_mobile(empresa, gasto):
    documentos = RegistroAuditoria.objects.filter(
        empresa=empresa, app_label="clinica", modelo="gastoadicional", objeto_id=str(gasto.pk),
    )
    eventos = list(RegistroAuditoria.objects.filter(empresa=empresa, app_label="clinica").filter(
        Q(modelo="gastoadicional", objeto_id=str(gasto.pk))
        | Q(modelo="lineagastoadicional", identificador_solicitud__in=documentos.values("identificador_solicitud"))
    ).select_related("usuario").order_by("-fecha", "-pk")[:30])
    historial = preparar_historial_factura_mobile(eventos)
    etiquetas = {"Profesional nombre": "Profesional", "Tipo cirugia": "Tipo de cirugía", "Tipo cirugia nombre": "Cirugía", "Fecha": "Fecha del documento", "Observacion": "Observación"}
    for evento, entrada in zip(eventos, historial):
        objeto = "gasto adicional" if evento.modelo == "gastoadicional" else "producto del documento"
        entrada["accion"] = f"{evento.get_accion_display()} de {objeto}"
        for cambio in entrada["cambios"]:
            cambio["campo"] = etiquetas.get(cambio["campo"], cambio["campo"])
        factura = (evento.cambios or {}).get("factura")
        if evento.modelo == "gastoadicional" and isinstance(factura, dict):
            entrada["cambios"].append({"campo": "Factura vinculada", "anterior": factura.get("anterior") or "—", "nuevo": factura.get("nuevo") or "—"})
    return historial
