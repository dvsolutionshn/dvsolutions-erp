"""Presentación y selección segura de controles clínicos directos existentes."""

from urllib.parse import urlencode

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, rol_clinico_granular
from crm.forms import (
    ProgramaCamaraHiperbaricaForm, ProgramaTerapiaPostQuirurgicaForm,
    SeleccionUnicaCheckboxField, SesionCamaraHiperbaricaForm, SesionTerapiaPostQuirurgicaForm,
)
from crm.models import (
    ProgramaCamaraHiperbarica, ProgramaTerapiaPostQuirurgica,
    SesionCamaraHiperbarica, SesionTerapiaPostQuirurgica,
)
from .mobile_patients import (
    autorizar_pacientes_app, contexto_pacientes_app, es_pacientes_app,
    permiso_pacientes_app, respuesta_pacientes_app,
)


CONTROLES_APP = {
    "camara_hiperbarica": ("Cámara hiperbárica", "puede_escribir_camara_hiperbarica", ProgramaCamaraHiperbarica, SesionCamaraHiperbarica, ProgramaCamaraHiperbaricaForm, SesionCamaraHiperbaricaForm),
    "terapias_postquirurgicas": ("Terapias postquirúrgicas", "puede_escribir_postquirurgicas", ProgramaTerapiaPostQuirurgica, SesionTerapiaPostQuirurgica, ProgramaTerapiaPostQuirurgicaForm, SesionTerapiaPostQuirurgicaForm),
}


def _url_control(empresa, paciente, tipo, tablero=False, **parametros):
    vista = "clinica_crear_historia_especialidad" if tablero else "clinica_registrar_control_especial"
    return f"{reverse(vista, args=[empresa.slug, paciente.pk, tipo])}?{urlencode({'app': '1', **{key: value for key, value in parametros.items() if value is not None and value != ''}})}"


def controles_disponibles_app(request, empresa, paciente=None):
    if not (empresa.slug in EMPRESAS_CLINICAS_CON_ROLES and interfaz_clinica_activa(empresa)
            and empresa.tiene_modulo_activo("clinica_medica") and request.user.is_authenticated
            and request.user.puede_acceder_empresa(empresa)):
        return []
    controles = []
    for codigo, (nombre, permiso, *_resto) in CONTROLES_APP.items():
        tablero = permiso_pacientes_app(request, empresa, permiso, "puede_expediente_clinico")
        registrar = permiso_pacientes_app(request, empresa, permiso, "puede_pacientes")
        if tablero or registrar:
            controles.append({"codigo": codigo, "nombre": nombre, "puede_tablero": tablero, "puede_registrar": registrar,
                              "url": _url_control(empresa, paciente, codigo, tablero=tablero) if paciente else ""})
    return controles


def autorizar_control_app(request, empresa, tipo, tablero=False):
    if not es_pacientes_app(request, empresa):
        return
    if tipo not in CONTROLES_APP:
        raise Http404("Control clínico móvil no válido.")
    autorizar_pacientes_app(request, empresa, CONTROLES_APP[tipo][1], "puede_expediente_clinico" if tablero else "puede_pacientes")
    if request.method not in ({"GET", "HEAD"} if tablero else {"GET", "HEAD", "POST"}):
        raise PermissionDenied("Método no permitido para este control móvil.")


def _valor_unico(datos, clave):
    valores = datos.getlist(clave)
    if len(valores) > 1:
        raise PermissionDenied("No se pueden mezclar selecciones del control clínico.")
    return (valores[0] if valores else "").strip()


def _identificador(valor):
    if not valor:
        return None
    if (not valor.isascii() or not valor.isdecimal() or len(valor) > 19
            or not 0 < int(valor) <= 9223372036854775807 or valor != str(int(valor))):
        raise Http404("Identificador de control no válido.")
    return int(valor)


def validar_seleccion_control_app(request, empresa, paciente, tipo, tablero=False, agregar=False):
    """Rechazar selecciones ajenas antes de los fallbacks del circuito web."""
    if not es_pacientes_app(request, empresa):
        return
    _nombre, _permiso, Programa, Sesion, ProgramaForm, SesionForm = CONTROLES_APP[tipo]
    if set(request.GET) - ({"app", "programa", "fase"} if tablero or agregar else {"app", "programa", "sesion", "fase"}):
        raise PermissionDenied("El control contiene selecciones no compatibles.")
    programa_get = _identificador(_valor_unico(request.GET, "programa"))
    programa_post = _identificador(_valor_unico(request.POST, "programa_id"))
    if programa_get is not None and "programa_id" in request.POST and programa_get != programa_post:
        raise PermissionDenied("El programa seleccionado no coincide con el formulario.")
    programa_id = programa_post or programa_get
    programa = get_object_or_404(Programa, pk=programa_id, empresa=empresa, paciente=paciente) if programa_id else None
    numero = _identificador(_valor_unico(request.GET, "sesion"))
    sesion_id = _identificador(_valor_unico(request.POST, "sesion_id"))
    fase = _valor_unico(request.GET, "fase")
    if fase and (tipo != "terapias_postquirurgicas" or fase not in {"1", "2"}):
        raise Http404("Fase del control no válida.")
    sesion = get_object_or_404(Sesion, pk=sesion_id, empresa=empresa, paciente=paciente) if sesion_id else None
    if sesion and ((programa and sesion.programa_id != programa.pk)
                   or (numero and numero not in {sesion.numero_sesion, sesion.numero_sesion_adicional})
                   or (fase and sesion.fase != int(fase))):
        raise PermissionDenied("La sesión no corresponde a la selección actual.")
    if not tablero and not agregar and not sesion and numero:
        programa = programa or Programa.objects.filter(empresa=empresa, paciente=paciente).order_by("-activo", "-fecha_creacion", "-id").first()
        if programa:
            consulta = programa.sesiones.filter(Q(numero_sesion=numero) | Q(numero_sesion_adicional=numero))
            if fase:
                consulta = consulta.filter(fase=int(fase))
            sesion = consulta.first()
    if sesion:
        if sesion.empresa_id != empresa.pk or sesion.paciente_id != paciente.pk:
            raise Http404("La sesión no pertenece a este expediente.")
        get_object_or_404(Programa, pk=sesion.programa_id, empresa=empresa, paciente=paciente)
    if tablero:
        # El tablero original usa la fecha de la cita antes de renderizar.
        # Comprobar IDs del programa elegido evita un acceso huérfano y no
        # modifica la selección, sus fases ni los datos de ninguna sesión.
        programa_tablero = programa or Programa.objects.filter(
            empresa=empresa, paciente=paciente,
        ).only("id").order_by("-activo", "-fecha_creacion", "-id").first()
        if programa_tablero:
            from crm.models import CitaCliente

            sesiones_tablero = Sesion.objects.filter(programa_id=programa_tablero.pk)
            if sesiones_tablero.exclude(empresa_id=empresa.pk, paciente_id=paciente.pk).exists():
                raise PermissionDenied("El programa contiene un registro ajeno a este expediente.")
            citas_propias = CitaCliente.objects.filter(empresa_id=empresa.pk, paciente_id=paciente.pk).values("pk")
            if sesiones_tablero.filter(cita_id__isnull=False).exclude(cita_id__in=citas_propias).exists():
                raise PermissionDenied("El programa contiene una cita no disponible en este expediente.")
    if not tablero and sesion and sesion.cita_id:
        raise PermissionDenied("Los controles asociados a citas se conservan en su circuito de agenda.")
    if request.method == "POST":
        if request.POST.getlist("app") != ["1"] or len(request.POST.getlist("programa_id")) != 1:
            raise PermissionDenied("El control móvil requiere una selección única.")
        permitidos = {"app", "programa_id", "csrfmiddlewaretoken"}
        if agregar:
            if _valor_unico(request.POST, "fase") not in {"1", "2"}:
                raise Http404("Fase del control no válida.")
            permitidos.add("fase")
            if programa_get is not None or sesion_id is not None or numero is not None:
                raise PermissionDenied("No se pueden mezclar acciones de sesión.")
        else:
            if request.POST.getlist("accion") not in (["borrador"], ["finalizar"]) or len(request.POST.getlist("sesion_id")) != 1:
                raise PermissionDenied("Acción móvil de sesión no válida.")
            permitidos.update({"sesion_id", "accion", *ProgramaForm().fields, *SesionForm().fields})
        if set(request.POST) - permitidos:
            raise PermissionDenied("El control contiene campos o acciones no compatibles.")


def _secciones(form, programa=False):
    campos = {campo.name: campo for campo in form.visible_fields()}
    grupos = [("programa", "Datos del programa", tuple(campos))] if programa else [
        ("registro", "Datos de la sesión", ("fase", "numero_sesion", "numero_sesion_adicional", "hora_inicio", "hora_finalizacion")),
        ("previo", "Control previo", tuple(SesionCamaraHiperbaricaForm.CAMPOS_SI_NO) + ("observaciones_previas", "firma_control_previo")),
        ("parametros", "Parámetros y evolución", ("presion_arterial_antes", "saturacion_oxigeno_antes", "presion_camara", "tiempo_minutos", "compensacion_oidos", "tolerancia", "presion_arterial_despues", "saturacion_oxigeno_despues", "evolucion_evento_adverso", "firma_parametros")),
        ("signos", "Signos registrados", ("presion_arterial", "frecuencia_cardiaca", "frecuencia_respiratoria", "saturacion_oxigeno", "temperatura", "escala_dolor")),
        ("cuidados", "Estado, protocolo y cuidados", ("estado_paciente", "equipos_utilizados", "minutos_area", "cuidados_realizados", "cuidado_otro")),
        ("nota", "Nota y firma de enfermería", ("nota_enfermeria", "enfermera_nombre", "firma_enfermeria")),
    ]
    secciones, usados = [], set()
    for key, titulo, nombres in grupos:
        fields = [{"field": campos[nombre], "single_choice": isinstance(campos[nombre].field, SeleccionUnicaCheckboxField)} for nombre in nombres if nombre in campos and nombre not in usados]
        if fields:
            usados.update(item["field"].name for item in fields)
            secciones.append({"key": key, "titulo": titulo, "campos": fields})
    restantes = [{"field": campo, "single_choice": isinstance(campo.field, SeleccionUnicaCheckboxField)} for nombre, campo in campos.items() if nombre not in usados]
    if restantes:
        secciones.append({"key": "otros", "titulo": "Datos adicionales", "campos": restantes})
    return secciones


def _campos_registro(registro, tipo):
    campos = []
    for nombre in CONTROLES_APP[tipo][5].Meta.fields:
        if nombre in {"fase", "numero_sesion", "numero_sesion_adicional"}:
            continue
        valor = getattr(registro, nombre)
        etiqueta = CONTROLES_APP[tipo][5].base_fields.get(nombre)
        if valor not in (None, "", []):
            display = getattr(registro, f"get_{nombre}_display", None) or getattr(registro, f"{nombre}_display", None)
            valor = display() if callable(display) else display if display is not None else valor
            if isinstance(valor, (list, tuple)):
                valor = " · ".join(str(item) for item in valor)
            campos.append({"label": etiqueta.label or registro._meta.get_field(nombre).verbose_name, "value": valor})
    return campos


def _url_atencion_con_cita_app(empresa, paciente, tipo, programa, registro):
    """Enlazar sólo la atención original propia, nunca el editor sin cita."""
    from crm.mobile_appointment_controls import _validar_cita, url_control_cita_app
    from crm.models import CitaCliente
    from crm.views import _es_cita_camara_hiperbarica, _es_cita_terapia_postquirurgica

    Programa = CONTROLES_APP[tipo][2]
    if not Programa.objects.filter(pk=programa.pk, empresa_id=empresa.pk, paciente_id=paciente.pk).exists():
        raise PermissionDenied("El programa no pertenece al expediente de esta empresa.")
    try:
        cita = CitaCliente.objects.only(
            "id", "empresa_id", "paciente_id", "servicio_clinico_id",
            "producto_id", "profesional_salud_id", "fecha_hora", "titulo",
        ).get(pk=registro.cita_id, empresa_id=empresa.pk, paciente_id=paciente.pk)
        # Reutilizar integridad y clasificación originales; una cita cuyo
        # servicio cambió no debe ofrecer una atención de otro tipo.
        _validar_cita(empresa, cita)
        clasificador = _es_cita_camara_hiperbarica if tipo == "camara_hiperbarica" else _es_cita_terapia_postquirurgica
        if not clasificador(cita):
            return ""
    except ObjectDoesNotExist as exc:
        raise PermissionDenied("La atención vinculada contiene una relación no disponible.") from exc
    return url_control_cita_app(empresa, tipo, fecha=timezone.localtime(cita.fecha_hora).date(), cita=cita)


def contexto_control_app(request, empresa, paciente, tipo, contexto, tablero=False):
    base = contexto_pacientes_app(request, empresa, paciente)
    if not rol_clinico_granular(request.user, empresa) and not request.user.tiene_permiso_erp("puede_citas", empresa):
        base["app_home_disponible"] = False
        base["app_modules_url"] = reverse("agenda_mobile_modules_screen", args=[empresa.slug])
    permiso = CONTROLES_APP[tipo][1]
    puede_tablero = permiso_pacientes_app(request, empresa, permiso, "puede_expediente_clinico")
    puede_registrar = permiso_pacientes_app(request, empresa, permiso, "puede_pacientes")
    post = tipo == "terapias_postquirurgicas"
    programa = contexto.get("programa_terapia" if post else "programa_camara")
    sesion = contexto.get("sesion_terapia" if post else "sesion_camara")
    fase = contexto.get("fase_terapia_actual", 1) if post else None
    url_tablero = _url_control(empresa, paciente, tipo, tablero=True, programa=programa.pk if programa else None, fase=fase)
    url_form = _url_control(empresa, paciente, tipo, programa=programa.pk if programa else None, fase=fase)
    base.update({
        "paciente": paciente, "mobile_control_type": {"codigo": tipo, "nombre": CONTROLES_APP[tipo][0]},
        "mobile_control_program": programa, "mobile_control_session": sesion,
        "can_view_control_board": puede_tablero, "can_register_control": puede_registrar,
        "can_add_control_session": post and puede_registrar,
        "control_readonly": bool(sesion and sesion.bloqueada),
        "app_control_board_url": url_tablero, "app_control_form_url": url_form if tablero else _url_control(empresa, paciente, tipo),
        "app_control_back_url": base["app_modules_url"] if tablero else url_tablero if puede_tablero else base["app_modules_url"],
        "app_control_add_session_url": f"{reverse('clinica_agregar_sesion_terapia_postquirurgica', args=[empresa.slug, paciente.pk])}?app=1" if post else "",
        "mobile_control_phases": [{"numero": numero, "nombre": nombre, "active": numero == fase, "url": _url_control(empresa, paciente, tipo, tablero=tablero, programa=programa.pk if programa else None, fase=numero)} for numero, nombre in SesionTerapiaPostQuirurgica.FASE_CHOICES] if post else [],
    })
    programa_form = contexto.get("programa_terapia_form" if post else "programa_camara_form")
    sesion_form = contexto.get("sesion_terapia_form" if post else "sesion_camara_form")
    if programa_form and sesion_form:
        if base["control_readonly"]:
            for field in programa_form.fields.values():
                field.disabled = True
        base.update({"programa_form": programa_form, "sesion_form": sesion_form,
                     "mobile_control_program_sections": _secciones(programa_form, programa=True),
                     "mobile_control_session_sections": _secciones(sesion_form)})
    programas = contexto.get("programas_terapia" if post else "programas_camara", [])
    base["mobile_control_programs"] = [{"registro": item, "url": _url_control(empresa, paciente, tipo, tablero=tablero, programa=item.pk, fase=fase)} for item in programas]
    items = contexto.get("tablero_sesiones_terapia" if post else "tablero_sesiones_camara", [])
    records = {}
    cards = []
    tipos_atencion = None
    urls_atencion = {}
    for item in items:
        registro = item.get("registro")
        if registro and (registro.empresa_id != empresa.pk or registro.paciente_id != paciente.pk
                         or programa is None or registro.programa_id != programa.pk):
            raise PermissionDenied("El registro no pertenece al programa de este expediente.")
        ligada = bool(registro and registro.cita_id)
        url = _url_control(empresa, paciente, tipo, programa=programa.pk if programa else None, sesion=item["numero"], fase=fase) if puede_registrar and not ligada else ""
        appointment_url = ""
        if ligada:
            if tipos_atencion is None:
                from crm.mobile_appointment_controls import controles_cita_disponibles_app

                tipos_atencion = {control["codigo"] for control in controles_cita_disponibles_app(request, empresa)}
            if tipo in tipos_atencion:
                if registro.pk not in urls_atencion:
                    urls_atencion[registro.pk] = _url_atencion_con_cita_app(empresa, paciente, tipo, programa, registro)
                appointment_url = urls_atencion[registro.pk]
        cards.append({"numero": item["numero"], "registro": registro, "url": url, "appointment_url": appointment_url, "ligada_cita": ligada, "bloqueada": bool(registro and registro.bloqueada)})
        if registro:
            records[registro.pk] = {"registro": registro, "campos": _campos_registro(registro, tipo) if not ligada or appointment_url else [], "url": url, "appointment_url": appointment_url, "ligada_cita": ligada, "bloqueada": registro.bloqueada}
    base["mobile_control_board_items"] = cards
    base["mobile_control_records"] = list(records.values())
    base["mobile_control_stats"] = {"total": len(cards), "finalizadas": contexto.get("sesiones_finalizadas", contexto.get("sesiones_terapia_completadas" if post else "sesiones_camara_completadas", 0)), "borradores": contexto.get("sesiones_borrador", sum(bool(item["registro"] and item["registro"].estado == "borrador") for item in cards))}
    return base


def render_control_app(request, empresa, paciente, tipo, template_web, contexto, tablero=False):
    if es_pacientes_app(request, empresa):
        contexto.update(contexto_control_app(request, empresa, paciente, tipo, contexto, tablero))
        template = "clinica/mobile_control_board.html" if tablero else "clinica/mobile_control_form.html"
    else:
        template = template_web
    return respuesta_pacientes_app(request, empresa, render(request, template, contexto))


def volver_control_app(request, empresa, paciente, tipo, programa, sesion=None, fase=None):
    puede_tablero = permiso_pacientes_app(request, empresa, CONTROLES_APP[tipo][1], "puede_expediente_clinico")
    destino = _url_control(empresa, paciente, tipo, tablero=puede_tablero, programa=programa.pk,
                           fase=fase, sesion=sesion.numero_sesion if sesion and not puede_tablero else None)
    return respuesta_pacientes_app(request, empresa, redirect(destino))
