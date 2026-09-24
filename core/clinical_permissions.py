"""Catálogo y resolución central de permisos clínicos granulares.

Los nombres de roles nunca participan en la autorización. Las rutas se traducen
a una acción concreta y el middleware comprueba esa acción contra el rol
asignado al usuario para la empresa de la URL.
"""

EMPRESAS_CLINICAS_CON_ROLES = {
    "hospital_mia",
    "medical_spa",
    "luque_aestetic",
    "serviciosmedicos",
}

PERMISOS_CLINICOS = (
    ("Calendario", (
        ("puede_ver_calendario", "Ver calendario y citas"),
        ("puede_crear_citas", "Crear citas"),
        ("puede_editar_citas", "Editar, reagendar o cambiar estado de citas"),
        ("puede_eliminar_citas", "Eliminar citas"),
    )),
    ("Pacientes", (
        ("puede_ver_pacientes", "Ver listado y buscar pacientes"),
        ("puede_ver_datos_generales_paciente", "Ver datos generales del paciente"),
        ("puede_crear_pacientes", "Crear pacientes"),
        ("puede_editar_pacientes", "Editar datos generales"),
        ("puede_eliminar_pacientes", "Eliminar pacientes"),
        ("puede_crear_recordatorios_paciente", "Agendar recordatorios"),
    )),
    ("Historia clínica", (
        ("puede_ver_historia_clinica", "Ver historia clínica y diagnósticos"),
        ("puede_crear_historia_clinica", "Crear historia clínica y diagnósticos"),
        ("puede_editar_historia_clinica", "Editar historia clínica"),
    )),
    ("Anexos", (
        ("puede_ver_anexos_clinicos", "Ver anexos anteriores"),
        ("puede_subir_anexos_clinicos", "Subir anexos nuevos"),
        ("puede_eliminar_anexos_clinicos", "Eliminar anexos"),
    )),
    ("Recetas", (
        ("puede_ver_recetas", "Ver recetas"),
        ("puede_crear_recetas", "Crear recetas"),
        ("puede_enviar_recetas", "Imprimir o enviar recetas"),
    )),
    ("Plan y tratamiento", (
        ("puede_ver_planes_tratamiento", "Ver plan y tratamiento"),
        ("puede_editar_planes_tratamiento", "Crear o editar plan y tratamiento"),
    )),
    ("Enfermería", (("puede_escribir_enfermeria", "Registrar formulario de Enfermería"),)),
    ("Terapias", (
        ("puede_ver_terapias", "Ver terapias"),
        ("puede_escribir_terapias", "Registrar terapias"),
    )),
    ("Cámara hiperbárica", (
        ("puede_ver_camara_hiperbarica", "Ver controles de cámara hiperbárica"),
        ("puede_escribir_camara_hiperbarica", "Registrar controles de cámara hiperbárica"),
    )),
    ("Postquirúrgicas", (
        ("puede_ver_postquirurgicas", "Ver terapias postquirúrgicas"),
        ("puede_escribir_postquirurgicas", "Registrar terapias postquirúrgicas"),
    )),
    ("Manuales PDF", (
        ("puede_ver_manuales_pdf", "Ver y descargar manuales PDF"),
        ("puede_enviar_manuales_pdf", "Enviar manuales PDF"),
        ("puede_administrar_manuales_pdf", "Crear y editar manuales PDF"),
    )),
    ("Facturación", (
        ("puede_crear_facturas", "Crear facturas"),
        ("puede_ver_facturas", "Ver facturas anteriores"),
        ("puede_editar_facturas", "Editar facturas"),
        ("puede_anular_facturas", "Anular facturas"),
    )),
    ("Inventario y productos", (
        ("puede_inventario", "Ver inventario"),
        ("puede_productos", "Ver productos"),
        ("puede_crear_productos", "Crear productos"),
        ("puede_editar_productos", "Editar productos"),
        ("puede_transferir_inventario", "Realizar transferencias rápidas"),
    )),
    ("Proveedores", (
        ("puede_proveedores", "Ver proveedores"),
        ("puede_crear_proveedores", "Crear proveedores"),
        ("puede_editar_proveedores", "Editar proveedores"),
    )),
    ("Configuración", (("puede_configuracion_clinica", "Administrar configuración clínica"),)),
)

TODOS_LOS_PERMISOS_CLINICOS = tuple(
    campo for _, permisos in PERMISOS_CLINICOS for campo, _ in permisos
)


def rol_clinico_granular(usuario, empresa):
    rol = usuario.rol_para_empresa(empresa)
    return rol if rol and rol.activo and rol.usa_permisos_clinicos_granulares else None


def permiso_clinica_granular(path_suffix, method="GET"):
    parts = [part for part in (path_suffix or "").strip("/").split("/") if part]
    if not parts:
        return None
    if parts[0] == "configuracion":
        return "puede_configuracion_clinica"
    if parts[0] == "manuales-pdf":
        return "puede_enviar_manuales_pdf" if method != "GET" or "enviar" in parts else "puede_ver_manuales_pdf"
    if parts[0] in {"profesionales", "servicios"}:
        return "puede_configuracion_clinica"
    if parts[0] == "citas":
        return "puede_crear_citas" if "crear" in parts else "puede_ver_calendario"
    if parts[0] == "tratamientos":
        return "puede_editar_planes_tratamiento" if method != "GET" or "crear" in parts or "editar" in parts or "eliminar" in parts else "puede_ver_planes_tratamiento"
    if parts[0] == "expediente":
        return "puede_crear_historia_clinica"
    if parts[0] == "recetas":
        if len(parts) > 1 and parts[1] == "manuales":
            if "enviar-correo" in parts:
                return "puede_enviar_manuales_pdf"
            if "archivo" in parts:
                return "puede_ver_manuales_pdf"
            return "puede_administrar_manuales_pdf"
        return "puede_editar_historia_clinica"
    if parts[0] != "pacientes":
        return "__denegar__"
    if len(parts) == 1 or (len(parts) == 2 and parts[1] == "sugerencias"):
        return "puede_ver_pacientes"
    if len(parts) > 1 and parts[1] in {"crear", "enlace-registro", "enlaces-registro"}:
        return "puede_crear_pacientes"
    if len(parts) == 2 and parts[1].isdigit():
        return "puede_ver_datos_generales_paciente"
    if len(parts) < 3 or not parts[1].isdigit():
        return "puede_ver_pacientes"
    action = parts[2:]
    if not action:
        return "puede_ver_datos_generales_paciente"
    if action[0] == "editar":
        return "puede_editar_pacientes"
    if action[0] == "eliminar":
        return "puede_eliminar_pacientes"
    if action[0] in {"seguimientos", "recordatorios-tratamiento"}:
        return "puede_crear_recordatorios_paciente"
    if action[0] == "evolucion":
        return "puede_subir_anexos_clinicos" if "registrar" in action else "puede_ver_anexos_clinicos"
    if action[0] in {"examenes", "documentos", "consentimientos"}:
        return "puede_subir_anexos_clinicos" if "subir" in action else "puede_ver_anexos_clinicos"
    if action[0] == "recetas":
        if "crear" in action:
            return "puede_crear_recetas"
        if any(x in action for x in {"imprimir", "pdf", "enviar-correo", "enviar-whatsapp"}):
            return "puede_enviar_recetas"
        return "puede_ver_recetas"
    if action[0] == "planes-tratamiento":
        return "puede_editar_planes_tratamiento" if method != "GET" else "puede_ver_planes_tratamiento"
    if action[0] == "historias":
        if "nueva" in action:
            tipo = action[-1]
            return {
                "enfermeria": "puede_escribir_enfermeria",
                "terapias": "puede_escribir_terapias",
                "camara-hiperbarica": "puede_escribir_camara_hiperbarica",
                "camara_hiperbarica": "puede_escribir_camara_hiperbarica",
                "terapias-postquirurgicas": "puede_escribir_postquirurgicas",
                "terapias_postquirurgicas": "puede_escribir_postquirurgicas",
            }.get(tipo, "puede_crear_historia_clinica")
        if "editar" in action:
            return "puede_editar_historia_clinica"
        return "puede_ver_historia_clinica"
    if action[0] == "historia-clinica":
        return "puede_editar_historia_clinica"
    if action[0] == "controles":
        tipo = action[1] if len(action) > 1 else ""
        return {
            "terapias": "puede_escribir_terapias",
            "camara-hiperbarica": "puede_escribir_camara_hiperbarica",
            "camara_hiperbarica": "puede_escribir_camara_hiperbarica",
            "terapias-postquirurgicas": "puede_escribir_postquirurgicas",
            "terapias_postquirurgicas": "puede_escribir_postquirurgicas",
        }.get(tipo, "puede_editar_historia_clinica")
    if action[0] == "preconsulta":
        return "puede_ver_historia_clinica" if method == "GET" else "puede_editar_historia_clinica"
    if action[0] in {"expediente"}:
        return "puede_crear_historia_clinica"
    return "__denegar__"


def permiso_agenda_granular(path_suffix, method="GET"):
    parts = [part for part in (path_suffix or "").strip("/").split("/") if part]
    if not parts:
        return "puede_crear_citas" if method != "GET" else "puede_ver_calendario"
    if parts[0] == "app":
        return "puede_crear_citas" if method != "GET" else "puede_ver_calendario"
    if parts[0] == "camara-hiperbarica":
        return "puede_ver_camara_hiperbarica"
    if parts[0] == "terapias-post-quirurgicas":
        return "puede_ver_postquirurgicas"
    if parts[0] == "disponibilidad":
        return "puede_editar_citas"
    if parts[:2] == ["pacientes", "buscar"] or parts[:2] == ["clientes", "buscar"]:
        return "puede_ver_pacientes"
    if parts[:2] == ["pacientes", "crear-rapido"]:
        return "puede_crear_pacientes"
    if parts[:2] == ["tipos-consulta", "crear-rapido"]:
        return "puede_configuracion_clinica"
    if parts[:2] == ["tratamientos", "crear-rapido"]:
        return "puede_editar_planes_tratamiento"
    if parts[0] == "progreso-servicios":
        return "puede_ver_planes_tratamiento"
    if parts[0].isdigit():
        if "camara-hiperbarica" in parts:
            return "puede_escribir_camara_hiperbarica"
        if "terapias-post-quirurgicas" in parts:
            return "puede_escribir_postquirurgicas"
        if "eliminar" in parts:
            return "puede_eliminar_citas"
        return "puede_editar_citas"
    return "__denegar__"
