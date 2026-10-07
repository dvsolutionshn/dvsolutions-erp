"""Documentos clínicos internos, sin emisión ni numeración fiscal."""
import base64
import json
import logging
import mimetypes
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.paginator import Paginator
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q, Value
from django.db.models.functions import Replace
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from weasyprint import HTML

from core.access import gastos_adicionales_habilitados
from core.models import Empresa
from crm.models import ConfiguracionCRM
from crm.services import enviar_documento_whatsapp, subir_documento_whatsapp
from facturacion.models import ConfiguracionFacturacionEmpresa
from .catalogo_gastos_adicionales import productos_gastos_adicionales
from .forms_gastos_adicionales import GastoAdicionalForm, es_id_valido, validar_lineas_gasto
from .models import GastoAdicional, LineaGastoAdicional, Paciente, ProfesionalSalud
from .services_gastos_adicionales import convertir_gasto_adicional

logger = logging.getLogger(__name__)


def _empresa_autorizada(request, slug, *permisos):
    empresa = get_object_or_404(Empresa, slug=slug, activa=True)
    if not request.user.is_superuser and not request.user.puede_acceder_empresa(empresa):
        raise PermissionDenied("No tiene acceso a esta empresa.")
    if not gastos_adicionales_habilitados(empresa):
        raise Http404("Gastos Adicionales no está habilitado para esta empresa.")
    if permisos and not any(request.user.tiene_permiso_erp(permiso, empresa) for permiso in permisos):
        raise PermissionDenied("No tiene permiso para realizar esta acción de Gastos Adicionales.")
    return empresa


def _gasto(empresa, gasto_id):
    return get_object_or_404(
        GastoAdicional.objects.select_related("paciente", "profesional", "creado_por", "factura", "convertido_por")
        .prefetch_related("lineas__producto__empresa", "lineas__producto__impuesto_predeterminado"), empresa=empresa, pk=gasto_id,
    )


def _volver(request, empresa, gasto):
    if not request.user.tiene_permiso_erp("puede_ver_gastos_adicionales", empresa):
        return redirect("dashboard", slug=empresa.slug)
    return redirect("clinica_gasto_adicional_detalle", empresa_slug=empresa.slug, gasto_id=gasto.pk)


@login_required
@require_GET
def gastos_adicionales(request, empresa_slug):
    empresa = _empresa_autorizada(request, empresa_slug, "puede_ver_gastos_adicionales")
    filtros = {clave: (request.GET.get(clave) or "").strip() for clave in ("paciente", "desde", "hasta", "numero", "estado")}
    gastos = GastoAdicional.objects.filter(empresa=empresa).select_related("paciente", "creado_por", "factura")
    paciente_id = request.GET.get("paciente_id")
    if paciente_id and not es_id_valido(paciente_id):
        raise Http404("Paciente no válido.")
    if paciente_id:
        paciente = get_object_or_404(Paciente, empresa=empresa, pk=paciente_id)
        gastos = gastos.filter(paciente=paciente)
        filtros["paciente"] = paciente.nombre
    elif filtros["paciente"]:
        gastos = gastos.filter(Q(paciente__nombre__icontains=filtros["paciente"]) | Q(paciente__identidad__icontains=filtros["paciente"]))
    if filtros["numero"]:
        gastos = gastos.filter(numero__icontains=filtros["numero"])
    for campo, lookup in (("desde", "fecha__gte"), ("hasta", "fecha__lte")):
        try:
            fecha = parse_date(filtros[campo]) if filtros[campo] else None
        except ValueError:
            fecha = None
        if fecha:
            gastos = gastos.filter(**{lookup: fecha})
    if filtros["estado"] == "facturado":
        gastos = gastos.filter(factura__estado="emitida")
    elif filtros["estado"] == "pendiente":
        gastos = gastos.filter(Q(factura__isnull=True) | ~Q(factura__estado="emitida"))
    pagina = Paginator(gastos.order_by("-fecha", "-pk"), 25).get_page(request.GET.get("page"))
    parametros = request.GET.copy()
    parametros.pop("page", None)
    return render(request, "clinica/gastos_adicionales.html", {"empresa": empresa, "gastos": pagina, "filtros": filtros, "querystring": parametros.urlencode()})


def _paciente_payload(paciente):
    return {
        "id": paciente.pk, "nombre": paciente.nombre, "text": paciente.nombre,
        "identidad": paciente.identidad or "", "expediente": paciente.expediente_codigo,
        "telefono": paciente.telefono or paciente.whatsapp or paciente.celular_2 or "",
        "whatsapp": paciente.whatsapp or "", "celular_2": paciente.celular_2 or "",
    }


def _producto_payload(producto):
    impuesto = producto.impuesto_predeterminado
    return {
        "id": producto.pk, "nombre": producto.nombre, "text": producto.nombre,
        "codigo": producto.codigo or "", "descripcion": producto.descripcion or "",
        "precio": str(producto.precio), "empresa_nombre": producto.empresa.nombre,
        "empresa_slug": producto.empresa.slug, "tipo_item": producto.tipo_item,
        "unidad_medida": producto.unidad_medida,
        "impuesto_id": producto.impuesto_predeterminado_id,
        "impuesto_nombre": impuesto.nombre if impuesto else "",
        "impuesto_porcentaje": str(impuesto.porcentaje) if impuesto else None,
        "impuesto_activo": bool(impuesto and impuesto.activo),
    }


def _resultados_paginados(queryset, request, payload):
    pagina = Paginator(queryset, 40).get_page(request.GET.get("page"))
    return JsonResponse({
        "results": [payload(objeto) for objeto in pagina],
        "page": pagina.number, "total": pagina.paginator.count,
        "has_more": pagina.has_next(),
        "next_page": pagina.next_page_number() if pagina.has_next() else None,
    })


@login_required
@require_GET
def pacientes_buscar(request, empresa_slug):
    empresa = _empresa_autorizada(request, empresa_slug, "puede_crear_gastos_adicionales", "puede_editar_gastos_adicionales")
    q = (request.GET.get("q") or "").strip()[:160]
    pacientes = Paciente.objects.filter(empresa=empresa, activo=True)
    if q:
        coincidencias = (
            Q(nombre__icontains=q) | Q(identidad__icontains=q) | Q(expediente_codigo__icontains=q)
            | Q(telefono__icontains=q) | Q(whatsapp__icontains=q) | Q(celular_2__icontains=q)
        )
        digitos = "".join(caracter for caracter in q if caracter.isascii() and caracter.isdecimal())
        if digitos and not any(caracter.isalpha() for caracter in q):
            normalizados = {}
            for campo in ("telefono", "whatsapp", "celular_2"):
                valor = campo
                for separador in (" ", "-", "(", ")", "+", ".", "\u00a0"):
                    valor = Replace(valor, Value(separador), Value(""))
                alias = f"_{campo}_busqueda"
                normalizados[alias] = valor
                coincidencias |= Q(**{f"{alias}__contains": digitos})
            pacientes = pacientes.annotate(**normalizados)
        pacientes = pacientes.filter(coincidencias)
    return _resultados_paginados(pacientes.order_by("nombre", "pk"), request, _paciente_payload)


@login_required
@require_GET
def productos_buscar(request, empresa_slug):
    empresa = _empresa_autorizada(request, empresa_slug, "puede_crear_gastos_adicionales", "puede_editar_gastos_adicionales")
    q = (request.GET.get("q") or "").strip()[:160]
    productos = productos_gastos_adicionales(empresa)
    if q:
        productos = productos.filter(
            Q(nombre__icontains=q) | Q(codigo__icontains=q) | Q(descripcion__icontains=q)
            | Q(empresa__nombre__icontains=q) | Q(empresa__slug__icontains=q)
        )
    return _resultados_paginados(productos.order_by("nombre", "empresa__nombre", "pk"), request, _producto_payload)


def _formulario(request, empresa, gasto=None):
    if gasto and gasto.factura_id:
        messages.warning(request, "Este gasto tiene una factura asociada y se conserva como documento histórico. Revise la factura vinculada.")
        return _volver(request, empresa, gasto)
    inicial = {"fecha": timezone.localdate()}
    paciente = None
    lineas_iniciales = []
    if gasto:
        inicial.update({"paciente": gasto.paciente_id, "fecha": gasto.fecha, "profesional": gasto.profesional_id, "observacion": gasto.observacion})
        paciente = gasto.paciente
        lineas_iniciales = [
            {**_producto_payload(linea.producto), "producto_id": linea.producto_id,
             "nombre": linea.descripcion, "cantidad": str(linea.cantidad), "precio_unitario": str(linea.precio_unitario)}
            for linea in gasto.lineas.all()
        ]
    else:
        profesional = ProfesionalSalud.objects.filter(empresa=empresa, usuario=request.user, activo=True).first()
        if profesional:
            inicial["profesional"] = profesional.pk
        paciente_id = request.GET.get("paciente")
        if paciente_id:
            if not es_id_valido(paciente_id):
                raise Http404("Paciente no válido.")
            paciente = get_object_or_404(Paciente, pk=paciente_id, empresa=empresa, activo=True)
            inicial["paciente"] = paciente.pk
    form = GastoAdicionalForm(request.POST if request.method == "POST" else None, initial=inicial, empresa=empresa, gasto=gasto)
    errores_lineas = []
    if request.method == "POST":
        valido = form.is_valid()
        try:
            lineas = validar_lineas_gasto(request.POST.get("lineas"), empresa, gasto=gasto)
        except ValidationError as exc:
            errores_lineas = exc.messages
            lineas = None
        if valido and lineas:
            try:
                with transaction.atomic():
                    if gasto:
                        documento = GastoAdicional.objects.select_for_update().get(pk=gasto.pk, empresa=empresa)
                        if documento.factura_id:
                            raise ValidationError("El gasto ya fue convertido y se conserva como documento histórico.")
                    else:
                        documento = GastoAdicional(empresa=empresa, creado_por=request.user)
                    for campo in ("paciente", "fecha", "profesional", "observacion"):
                        setattr(documento, campo, form.cleaned_data[campo])
                    documento.actualizado_por = request.user
                    documento.save()
                    documento.lineas.all().delete()
                    for datos in lineas:
                        LineaGastoAdicional.objects.create(gasto=documento, descripcion=datos["producto"].nombre, **datos)
                    documento.calcular_totales()
                    documento.save()
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, f"Gasto Adicional {documento.numero} guardado.")
                puede_ver = request.user.tiene_permiso_erp("puede_ver_gastos_adicionales", empresa)
                if request.POST.get("accion") == "pdf" and puede_ver:
                    return redirect("clinica_gasto_adicional_pdf", empresa_slug=empresa.slug, gasto_id=documento.pk)
                if not puede_ver:
                    if gasto:
                        return redirect("clinica_gasto_adicional_editar", empresa_slug=empresa.slug, gasto_id=documento.pk)
                    return redirect("clinica_gasto_adicional_crear", empresa_slug=empresa.slug)
                return _volver(request, empresa, documento)
        paciente_valor = request.POST.get("paciente", "")
        paciente = Paciente.objects.filter(empresa=empresa, pk=paciente_valor).first() if es_id_valido(paciente_valor) else None
        # Reconstruir solo selecciones del mismo catálogo autorizado del POST.
        try:
            datos_post = json.loads(request.POST.get("lineas") or "[]")
        except (ValueError, TypeError):
            datos_post = []
        lineas_iniciales = []
        if isinstance(datos_post, list):
            catalogo = productos_gastos_adicionales(empresa, gasto=gasto)
            ids_post = [int(dato["producto_id"]) for dato in datos_post[:100] if isinstance(dato, dict) and es_id_valido(dato.get("producto_id", ""))]
            productos_post = {producto.pk: producto for producto in catalogo.filter(pk__in=ids_post)}
            for dato in datos_post[:100]:
                if not isinstance(dato, dict) or not es_id_valido(dato.get("producto_id", "")):
                    continue
                producto = productos_post.get(int(dato["producto_id"]))
                if producto:
                    lineas_iniciales.append({**_producto_payload(producto), "producto_id": producto.pk, "cantidad": str(dato.get("cantidad", ""))[:30], "precio_unitario": str(dato.get("precio_unitario", ""))[:30]})
    from facturacion.views import _precios_incluyen_impuesto
    permiso_precio = "puede_editar_gastos_adicionales" if gasto else "puede_crear_gastos_adicionales"
    return render(request, "clinica/gastos_adicionales_form.html", {
        "empresa": empresa, "gasto": gasto, "form": form,
        "paciente_inicial": _paciente_payload(paciente) if paciente else None,
        "lineas_iniciales": lineas_iniciales, "errores_lineas": errores_lineas,
        "precios_incluyen_impuesto": gasto.precio_incluye_impuesto if gasto else _precios_incluyen_impuesto(empresa),
        "puede_editar_precio": request.user.tiene_permiso_erp(permiso_precio, empresa),
    })


@login_required
@require_http_methods(["GET", "POST"])
def crear(request, empresa_slug):
    empresa = _empresa_autorizada(request, empresa_slug, "puede_crear_gastos_adicionales")
    return _formulario(request, empresa)


@login_required
@require_http_methods(["GET", "POST"])
def editar(request, empresa_slug, gasto_id):
    empresa = _empresa_autorizada(request, empresa_slug, "puede_editar_gastos_adicionales")
    return _formulario(request, empresa, _gasto(empresa, gasto_id))


@login_required
@require_GET
def detalle(request, empresa_slug, gasto_id):
    empresa = _empresa_autorizada(request, empresa_slug, "puede_ver_gastos_adicionales")
    return render(request, "clinica/gastos_adicionales_detalle.html", {"empresa": empresa, "gasto": _gasto(empresa, gasto_id)})


def contexto_pdf(empresa, gasto):
    logo_url = ""
    if empresa.logo:
        try:
            with empresa.logo.open("rb") as archivo:
                mime = mimetypes.guess_type(empresa.logo.name)[0] or "image/png"
                logo_url = f"data:{mime};base64," + base64.b64encode(archivo.read()).decode("ascii")
        except (OSError, ValueError):
            logger.warning("No se pudo leer logo para GA de empresa %s", empresa.pk)
    configuracion = ConfiguracionFacturacionEmpresa.objects.filter(empresa=empresa).first()
    return {"empresa": empresa, "gasto": gasto, "logo_url": logo_url, "nombre_clinica": (configuracion.nombre_comercial_documentos if configuracion else None) or empresa.nombre, "pie_institucional": (configuracion.pie_factura if configuracion else None) or f"{empresa.nombre} · Atención y cuidado de su salud."}


def generar_pdf_bytes(empresa, gasto):
    html = render_to_string("clinica/gastos_adicionales_pdf.html", contexto_pdf(empresa, gasto))
    return HTML(string=html, base_url=str(settings.BASE_DIR)).write_pdf()


@login_required
@require_GET
def pdf(request, empresa_slug, gasto_id):
    empresa = _empresa_autorizada(request, empresa_slug, "puede_ver_gastos_adicionales")
    gasto = _gasto(empresa, gasto_id)
    response = HttpResponse(generar_pdf_bytes(empresa, gasto), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{gasto.numero}.pdf"'
    return response


@login_required
@require_POST
def convertir(request, empresa_slug, gasto_id):
    empresa = _empresa_autorizada(request, empresa_slug, "puede_convertir_gastos_adicionales_factura")
    if not empresa.tiene_modulo_activo("facturacion") or not all(request.user.tiene_permiso_erp(permiso, empresa) for permiso in ("puede_crear_facturas", "puede_editar_facturas", "puede_ver_facturas")):
        raise PermissionDenied("La conversión requiere Facturación activa y permisos para crear y revisar la factura.")
    gasto = _gasto(empresa, gasto_id)
    try:
        factura, creada = convertir_gasto_adicional(gasto, request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return _volver(request, empresa, gasto)
    if creada:
        messages.success(request, f"Factura en borrador creada desde {gasto.numero}. Revise y confirme la emisión en Facturación.")
    else:
        messages.warning(request, f"{gasto.numero} ya tiene una factura asociada. Se abre la existente.")
    destino = "editar_factura" if factura.estado == "borrador" else "ver_factura"
    return redirect(destino, empresa_slug=empresa.slug, factura_id=factura.pk)


@login_required
@require_POST
def enviar_correo(request, empresa_slug, gasto_id):
    empresa = _empresa_autorizada(request, empresa_slug, "puede_enviar_gastos_adicionales")
    gasto = _gasto(empresa, gasto_id)
    destino = (request.POST.get("email") or gasto.paciente.correo or "").strip()
    try:
        validate_email(destino)
    except ValidationError:
        messages.error(request, "Indique un correo válido o agréguelo en la ficha del paciente.")
        return _volver(request, empresa, gasto)
    try:
        mensaje = EmailMultiAlternatives(subject=f"Gastos Adicionales {gasto.numero} - {empresa.nombre}", body=f"Hola {gasto.paciente.nombre},\n\nAdjuntamos su documento de Gastos Adicionales {gasto.numero}.\n\n{empresa.nombre}", from_email=settings.DEFAULT_FROM_EMAIL, to=[destino])
        mensaje.attach(f"{gasto.numero}.pdf", generar_pdf_bytes(empresa, gasto), "application/pdf")
        enviado = mensaje.send(fail_silently=False)
        if not enviado:
            raise RuntimeError("El proveedor de correo no confirmó el envío.")
    except Exception:
        logger.exception("No se pudo enviar GA %s por correo", gasto.pk)
        messages.error(request, "No se pudo enviar el documento. Revise la configuración de correo.")
    else:
        messages.success(request, f"Documento enviado por correo a {destino}.")
    return _volver(request, empresa, gasto)


@login_required
@require_POST
def enviar_whatsapp(request, empresa_slug, gasto_id):
    empresa = _empresa_autorizada(request, empresa_slug, "puede_enviar_gastos_adicionales")
    gasto = _gasto(empresa, gasto_id)
    config = ConfiguracionCRM.objects.filter(empresa=empresa, whatsapp_activo=True).first()
    telefono = (request.POST.get("telefono") or gasto.paciente.whatsapp or gasto.paciente.telefono or "").strip()
    if not config or not telefono:
        messages.error(request, "Configure WhatsApp en CRM e indique el teléfono del paciente para enviar el documento.")
        return _volver(request, empresa, gasto)
    try:
        with tempfile.TemporaryDirectory(prefix="gasto_adicional_") as directorio:
            ruta = Path(directorio) / f"{gasto.numero}.pdf"
            ruta.write_bytes(generar_pdf_bytes(empresa, gasto))
            media_id = subir_documento_whatsapp(config, ruta, "application/pdf")
            enviar_documento_whatsapp(config, telefono, media_id, ruta.name, caption=f"{empresa.nombre} le comparte sus Gastos Adicionales {gasto.numero}.")
    except Exception:
        logger.exception("No se pudo enviar GA %s por WhatsApp", gasto.pk)
        messages.error(request, "No se pudo enviar por WhatsApp. Revise la configuración e intente nuevamente.")
    else:
        messages.success(request, "Documento enviado por WhatsApp.")
    return _volver(request, empresa, gasto)
