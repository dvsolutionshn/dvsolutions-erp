"""Presentación de anexos existentes y lectura protegida de sus archivos."""

import re
from pathlib import Path
from urllib.parse import urlencode

from django import forms
from django.core.exceptions import PermissionDenied, SuspiciousFileOperation
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import content_disposition_header

from core.clinical_permissions import rol_clinico_granular, rol_clinico_v2
from .mobile_patients import contexto_pacientes_app, es_pacientes_app, respuesta_pacientes_app, url_paciente_app
from .models import DocumentoClinicoPaciente


es_anexos_app = es_pacientes_app
respuesta_anexos_app = respuesta_pacientes_app
RUTAS_ANEXOS = {
    "examen": ("Exámenes", "clinica_examenes_paciente", "clinica_subir_examen_paciente"),
    "evolucion": ("Evolución", "clinica_evolucion_paciente", "clinica_registrar_foto_evolucion"),
    "consentimiento": ("Consentimientos", "clinica_consentimientos_paciente", "clinica_subir_consentimiento_paciente"),
    "documento": ("Documentos", "clinica_documentos_categoria_paciente", "clinica_subir_documento_categoria_paciente"),
}
TIPOS_ARCHIVO_INLINE = {
    ".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".webp": "image/webp", ".mp4": "video/mp4",
    ".mov": "video/quicktime", ".webm": "video/webm", ".avi": "video/x-msvideo",
}


def permiso_anexos_app(request, empresa, subir=False):
    if rol_clinico_granular(request.user, empresa):
        return request.user.tiene_permiso_erp("puede_subir_anexos_clinicos" if subir else "puede_ver_anexos_clinicos", empresa)
    # Se conserva la combinación efectiva de menú y middleware web legado.
    return request.user.tiene_permiso_erp("puede_expediente_clinico", empresa) and request.user.tiene_permiso_erp("puede_pacientes", empresa)


def autorizar_anexos_app(request, empresa, subir=False):
    if not es_anexos_app(request, empresa):
        return
    if not empresa.tiene_modulo_activo("clinica_medica"):
        raise Http404("El módulo clínico no está habilitado para esta empresa.")
    if not request.user.puede_acceder_empresa(empresa) or not permiso_anexos_app(request, empresa, subir):
        raise PermissionDenied("No tiene permiso para esta acción de anexos clínicos.")
    if not subir and request.method not in {"GET", "HEAD"}:
        raise PermissionDenied("La consulta móvil de anexos no admite modificaciones.")


def url_anexo_app(empresa, paciente, clase, categoria=None, subir=False):
    args = [empresa.slug, paciente.pk] + ([categoria] if clase == "documento" else [])
    return f"{reverse(RUTAS_ANEXOS[clase][2 if subir else 1], args=args)}?app=1"


def url_archivo_anexo_app(empresa, paciente, clase, registro, parte="archivo", descargar=False):
    url = reverse("clinica_archivo_anexo_app", args=[empresa.slug, paciente.pk, clase, registro.pk, parte])
    return f"{url}?{urlencode({'app': '1', **({'descargar': '1'} if descargar else {})})}"


def evolucion_paciente_app(empresa, paciente, registros):
    return [{
        "registro": registro,
        "imagen_url": url_archivo_anexo_app(empresa, paciente, "evolucion", registro, "imagen") if registro.imagen else "",
        "video_url": url_archivo_anexo_app(empresa, paciente, "evolucion", registro, "video") if registro.video else "",
    } for registro in registros if registro.empresa_id == empresa.pk and registro.paciente_id == paciente.pk]


def _item_anexo_app(empresa, paciente, clase, registro, *, puede_descargar=True):
    parte = "imagen" if clase == "evolucion" and registro.imagen else "video" if clase == "evolucion" else "archivo"
    archivo = getattr(registro, parte)
    extension = Path(archivo.name).suffix.lower() if archivo else ""
    url = url_archivo_anexo_app(empresa, paciente, clase, registro, parte) if archivo else ""
    actor = getattr(registro, "subido_por", None) if clase == "examen" else getattr(registro, "creado_por", None)
    fecha = getattr(registro, {"examen": "fecha_examen", "evolucion": "fecha", "consentimiento": "fecha_firma", "documento": "fecha_documento"}[clase])
    metadata = []
    for campo, label in {
        "examen": (("laboratorio", "Laboratorio / centro"),),
        "evolucion": (),
        "consentimiento": (("version", "Versión"), ("firmado_por", "Firmado por")),
        "documento": (("entidad", "Origen / centro"), ("fecha_inicio", "Inicio"), ("fecha_fin", "Fin"), ("dias", "Días indicados")),
    }[clase]:
        valor = getattr(registro, campo, None)
        if valor is not None and valor != "":
            metadata.append({"label": label, "value": valor})
    if clase == "documento" and registro.profesional and registro.profesional.empresa_id == empresa.pk:
        metadata.append({"label": "Profesional", "value": registro.profesional.nombre})
    if clase == "consentimiento":
        if registro.tratamiento and registro.tratamiento.empresa_id == empresa.pk and registro.tratamiento.paciente_id == paciente.pk:
            metadata.append({"label": "Tratamiento", "value": registro.tratamiento.nombre})
        if registro.cita and registro.cita.empresa_id == empresa.pk and registro.cita.paciente_id == paciente.pk:
            metadata.append({"label": "Cita", "value": registro.cita.fecha_hora})
    print_url = ""
    if clase == "documento" and registro.categoria == "incapacidad":
        print_url = f"{reverse('clinica_incapacidad_imprimir', args=[empresa.slug, paciente.pk, registro.pk])}?app=1"
    return {
        "registro": registro, "titulo": registro.titulo, "fecha": fecha,
        "tipo": registro.get_tipo_display() if clase in {"examen", "evolucion"} else registro.get_categoria_display() if clase == "documento" else "Consentimiento",
        "descripcion": getattr(registro, "descripcion", "") or "",
        "usuario": (actor.get_full_name() or actor.username) if actor else "Usuario no disponible",
        "metadatos": metadata, "archivo_url": url,
        "download_url": url_archivo_anexo_app(empresa, paciente, clase, registro, parte, descargar=True) if archivo and puede_descargar else "",
        "imagen_url": url if extension in {".jpg", ".jpeg", ".png", ".webp"} else "",
        "video_url": url if extension in {".mp4", ".mov", ".webm", ".avi"} else "",
        "print_url": print_url, "es_imagen": extension in {".jpg", ".jpeg", ".png", ".webp"},
        "es_video": extension in {".mp4", ".mov", ".webm", ".avi"}, "es_pdf": extension == ".pdf",
        "estado": getattr(registro, "estado", ""),
        "estado_label": registro.get_estado_display() if clase == "consentimiento" else "",
    }


def contexto_anexos_app(request, empresa, paciente, clase, *, categoria=None, config=None, registros=()):
    contexto = contexto_pacientes_app(request, empresa, paciente)
    can_view, can_upload = permiso_anexos_app(request, empresa), permiso_anexos_app(request, empresa, subir=True)
    lista = url_anexo_app(empresa, paciente, clase, categoria)
    subida = url_anexo_app(empresa, paciente, clase, categoria, subir=True)
    modo_selector = "anexos" if can_view else "crear_anexo"
    regreso = contexto["app_patient_detail_url"] if contexto["can_view_patient"] else (
        url_paciente_app("clinica_pacientes", empresa, destino=modo_selector)
        if contexto["can_view_patients"] else contexto["app_modules_url"]
    )
    rutas = [(key, None, valores[0]) for key, valores in RUTAS_ANEXOS.items() if key != "documento"]
    rutas.extend(("documento", key, titulo) for key, titulo in DocumentoClinicoPaciente.CATEGORIA_CHOICES)
    puede_descargar = (
        request.user.tiene_permiso_erp("puede_descargar_anexos_clinicos", empresa)
        if rol_clinico_v2(request.user, empresa) else can_view
    )
    contexto.update({
        "can_view_annexes": can_view, "can_view_attachments": can_view, "can_upload_annexes": can_upload,
        "mobile_annex_class": clase, "mobile_annex_title": config["titulo"] if config else RUTAS_ANEXOS[clase][0],
        "app_annex_list_url": lista if can_view else "", "app_annex_upload_url": subida if can_upload else "",
        "app_annex_form_url": subida, "app_annex_cancel_url": lista if can_view else contexto["app_modules_url"],
        "app_annex_back_url": regreso,
        "mobile_annex_navigation": [{
            "clase": key, "categoria": cat or "", "titulo": titulo,
            "url": url_anexo_app(empresa, paciente, key, cat, subir=not can_view),
            "active": clase == key and categoria == cat,
        } for key, cat, titulo in rutas if can_view or can_upload],
        "mobile_annex_items": [
            _item_anexo_app(empresa, paciente, clase, item, puede_descargar=puede_descargar)
            for item in registros
            if item.empresa_id == empresa.pk and item.paciente_id == paciente.pk
        ] if can_view else [],
    })
    return contexto


def render_anexos_app(request, empresa, paciente, clase, web_template, contexto, *, categoria=None, config=None, registros=(), form=False):
    if es_anexos_app(request, empresa):
        if form:
            # Sólo formato HTML de los widgets originales; defaults y validadores
            # siguen siendo los mismos, incluidos los datos de un POST inválido.
            for campo in contexto["form"].fields.values():
                if isinstance(campo.widget, forms.DateTimeInput) and campo.widget.attrs.get("type") == "datetime-local":
                    campo.widget.format = "%Y-%m-%dT%H:%M"
                elif isinstance(campo.widget, forms.DateInput) and campo.widget.attrs.get("type") == "date":
                    campo.widget.format = "%Y-%m-%d"
        contexto.update(contexto_anexos_app(request, empresa, paciente, clase, categoria=categoria, config=config, registros=registros))
        contexto["paciente"] = paciente
        template = "clinica/mobile_annex_form.html" if form else "clinica/mobile_annex_list.html"
    else:
        template = web_template
    return respuesta_anexos_app(request, empresa, render(request, template, contexto))


def volver_anexo_app(request, empresa, paciente, clase, categoria=None):
    if es_anexos_app(request, empresa):
        destino = url_anexo_app(empresa, paciente, clase, categoria, subir=not permiso_anexos_app(request, empresa))
        return respuesta_anexos_app(request, empresa, redirect(destino))
    args = [empresa.slug, paciente.pk] + ([categoria] if clase == "documento" else [])
    return redirect(reverse(RUTAS_ANEXOS[clase][1], args=args))


class _LecturaArchivoAcotada:
    """Lectura del mismo archivo limitada a un rango HTTP, con cierre normal."""
    def __init__(self, archivo, cantidad):
        self.archivo, self.restante = archivo, cantidad

    def read(self, cantidad=-1):
        if self.restante <= 0:
            return b""
        datos = self.archivo.read(min(self.restante, cantidad if cantidad >= 0 else self.restante))
        self.restante -= len(datos)
        return datos

    def close(self):
        self.archivo.close()


def respuesta_archivo_anexo_app(request, empresa, archivo):
    """Mismos bytes; contenido activo desconocido siempre se descarga."""
    nombre = Path(archivo.name).name
    tipo = TIPOS_ARCHIVO_INLINE.get(Path(nombre).suffix.lower(), "application/octet-stream")
    descargar = request.GET.get("descargar") == "1" or tipo == "application/octet-stream"
    try:
        archivo.open("rb")
        total = archivo.size
    except (OSError, ValueError, SuspiciousFileOperation):
        archivo.close()
        raise Http404("El archivo del anexo no está disponible.")
    rango = request.META.get("HTTP_RANGE", "") if request.method == "GET" else ""
    inicio, fin, parcial = 0, total - 1, False
    if rango:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", rango)
        try:
            if not match or not any(match.groups()) or not total:
                raise ValueError
            primero, ultimo = match.groups()
            if primero:
                inicio = int(primero)
                fin = min(int(ultimo), fin) if ultimo else fin
            else:
                sufijo = int(ultimo)
                if sufijo <= 0:
                    raise ValueError
                inicio = max(total - sufijo, 0)
            if inicio > fin or inicio >= total:
                raise ValueError
            archivo.seek(inicio)
            parcial = True
        except (ValueError, OverflowError, OSError):
            archivo.close()
            respuesta = HttpResponse(status=416)
            respuesta["Content-Range"] = f"bytes */{total}"
            respuesta["X-Content-Type-Options"] = "nosniff"
            return respuesta_anexos_app(request, empresa, respuesta)
    if request.method == "HEAD":
        archivo.close()
        respuesta = HttpResponse(content_type=tipo)
    else:
        lectura = _LecturaArchivoAcotada(archivo, fin - inicio + 1) if parcial else archivo
        respuesta = FileResponse(lectura, content_type=tipo, status=206 if parcial else 200)
    respuesta["Content-Length"] = str(fin - inicio + 1)
    respuesta["Content-Disposition"] = content_disposition_header(descargar, nombre)
    respuesta["Accept-Ranges"] = "bytes"
    respuesta["X-Content-Type-Options"] = "nosniff"
    if parcial:
        respuesta["Content-Range"] = f"bytes {inicio}-{fin}/{total}"
    return respuesta_anexos_app(request, empresa, respuesta)
