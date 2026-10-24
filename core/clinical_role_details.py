"""Presentación de permisos configurados: no resuelve ni modifica autorizaciones.

Las claves y etiquetas proceden de los catálogos originales. Las explicaciones
describen su función, no garantizan acceso a una pantalla ni nuevas operaciones.
"""

from .access import interfaz_clinica_activa
from .clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, PERMISOS_CLINICOS


# Basado en clinical_permissions, access y los guards de las vistas existentes.
# No se deducen permisos a partir de nombres como Recepción, Enfermería o Bodega.
DESCRIPCIONES_PERMISOS = {
    "puede_ver_calendario": "Consultar el calendario y las citas autorizadas. No permite crear, editar ni eliminar citas por sí solo.",
    "puede_crear_citas": "Registrar citas nuevas mediante el formulario de agenda. La consulta del calendario y otras acciones conservan sus permisos propios.",
    "puede_editar_citas": "Editar, reagendar o cambiar el estado de citas. Los bloqueos de disponibilidad también comprueban las reglas del profesional y de la empresa.",
    "puede_eliminar_citas": "Solicitar la eliminación de una cita en las pantallas que admiten esa acción, con sus verificaciones originales.",
    "puede_ver_pacientes": "Consultar el listado y buscar pacientes. Los datos generales, historias y acciones de cada expediente tienen permisos separados.",
    "puede_ver_datos_generales_paciente": "Abrir los datos generales de un paciente autorizado. No equivale a editar sus datos ni a consultar su historia clínica.",
    "puede_crear_pacientes": "Registrar pacientes y acceder a los flujos de registro autorizados. Los enlaces de registro mantienen sus restricciones adicionales de propietario y empresa.",
    "puede_editar_pacientes": "Modificar los datos generales de pacientes mediante el formulario existente; no concede edición de historias por sí solo.",
    "puede_eliminar_pacientes": "Es el permiso de la acción de eliminar pacientes. La vista además exige administración de empresa; esta selección por sí sola no basta.",
    "puede_crear_recordatorios_paciente": "Consultar y programar seguimientos o recordatorios del paciente mediante los flujos existentes. No sustituye los permisos propios del calendario.",
    "puede_ver_historia_clinica": "Consultar historias clínicas, diagnósticos y preconsultas autorizadas. No permite registrar ni editar notas por sí solo.",
    "puede_crear_historia_clinica": "Registrar nuevas notas e historias de las áreas que usan este permiso. Enfermería, Terapias y los controles especializados conservan sus permisos específicos.",
    "puede_editar_historia_clinica": "Editar historias existentes y completar el formulario de historia general. No implica acceso automático al listado de pacientes ni a todos los anexos.",
    "puede_ver_anexos_clinicos": "Consultar los anexos clínicos y sus archivos autorizados. Subir archivos y otras acciones son permisos distintos.",
    "puede_subir_anexos_clinicos": "Registrar nuevos anexos, documentos, exámenes o consentimientos en las pantallas habilitadas. No permite consultar todos los anteriores por sí solo.",
    "puede_eliminar_anexos_clinicos": "Identifica la eliminación de anexos en el catálogo de permisos. Mostrar esta selección no añade botones de borrado ni habilita una acción que la pantalla no ofrezca.",
    "puede_ver_recetas": "Consultar recetas ya registradas para pacientes autorizados. Crear, imprimir o enviar recetas conserva sus permisos específicos.",
    "puede_crear_recetas": "Preparar y guardar recetas nuevas mediante el formulario existente, sin conceder por sí solo la consulta general del expediente.",
    "puede_enviar_recetas": "Imprimir, obtener el PDF o enviar una receta mediante las opciones existentes. Mostrar este permiso no realiza ningún envío.",
    "puede_ver_gastos_adicionales": "Consultar Gastos Adicionales y obtener su PDF cuando el módulo esté habilitado. Crear, editar, enviar o convertir son acciones separadas.",
    "puede_crear_gastos_adicionales": "Registrar Gastos Adicionales nuevos mediante su formulario. No equivale a consultar todos los documentos ni a convertirlos en factura.",
    "puede_editar_gastos_adicionales": "Modificar Gastos Adicionales mediante el editor existente, sujeto al estado del documento y las verificaciones de esa vista.",
    "puede_enviar_gastos_adicionales": "Usar las opciones existentes de envío por correo o WhatsApp de Gastos Adicionales. No envía documentos al consultar este resumen.",
    "puede_convertir_gastos_adicionales_factura": "Solicitar la conversión de un Gasto Adicional a factura mediante el flujo original y sus validaciones; no convierte nada desde esta pantalla.",
    "puede_ver_planes_tratamiento": "Consultar los tratamientos y los planes históricos del paciente. Registrar tratamientos y añadir planes históricos requiere el permiso correspondiente; cada pantalla mantiene sus acciones disponibles.",
    "puede_editar_planes_tratamiento": "Registrar tratamientos y añadir planes históricos de texto mediante los flujos existentes. La administración del catálogo de tratamientos conserva sus requisitos adicionales; este permiso no añade una edición que la pantalla no ofrezca.",
    "puede_escribir_enfermeria": "Registrar el formulario clínico de Enfermería. Consultar historias previas requiere además los permisos de lectura correspondientes.",
    "puede_ver_terapias": "Identifica la consulta de Terapias. El acceso mediante el selector de pacientes también requiere listado de pacientes y lectura de historia clínica.",
    "puede_escribir_terapias": "Registrar notas clínicas de Terapias. No habilita por sí solo la lectura de notas anteriores ni el listado de pacientes.",
    "puede_ver_camara_hiperbarica": "Consultar programas y sesiones autorizados de Cámara Hiperbárica. La atención ligada a una cita mantiene sus restricciones de empresa y de agenda.",
    "puede_escribir_camara_hiperbarica": "Registrar programas y controles de Cámara Hiperbárica mediante sus formularios existentes, sin omitir la validación de paciente, programa o cita.",
    "puede_ver_postquirurgicas": "Consultar programas y sesiones de seguimiento postquirúrgico. No equivale a los recordatorios generales del calendario ni concede edición por sí solo.",
    "puede_escribir_postquirurgicas": "Registrar programas y sesiones de seguimiento postquirúrgico. Las sesiones vinculadas a citas usan los controles originales de esa atención.",
    "puede_ver_manuales_pdf": "Consultar y descargar manuales PDF autorizados cuando la función esté habilitada. No permite administrarlos ni enviarlos por sí solo.",
    "puede_enviar_manuales_pdf": "Usar la opción existente para enviar manuales PDF. La consulta de este resumen no envía archivos ni habilita su administración.",
    "puede_administrar_manuales_pdf": "Crear y editar el catálogo de manuales PDF; la regla actual también permite consultarlos. El envío conserva su permiso separado.",
    "puede_crear_facturas": "Crear facturas mediante el flujo existente. No sustituye los permisos de consultar, editar, anular o registrar pagos.",
    "puede_ver_facturas": "Consultar facturas existentes y sus opciones autorizadas. La edición, anulación y los cobros tienen permisos propios.",
    "puede_editar_facturas": "Modificar facturas en los estados y condiciones admitidos por el editor original; no elimina sus validaciones fiscales.",
    "puede_anular_facturas": "Solicitar la anulación de facturas mediante el flujo existente y sus comprobaciones. No se anula ningún documento desde este resumen.",
    "puede_productos": "Consultar el catálogo de productos. La baja lógica también utiliza este permiso y su confirmación original; crear y editar son permisos separados.",
    "puede_crear_productos": "Registrar productos nuevos con su formulario y distribución por bodega originales. No concede automáticamente lectura de inventario ni edición de otros productos.",
    "puede_editar_productos": "Editar productos, incluidos los campos y la distribución por bodega admitidos por el formulario original. No cambia por sí solo otros permisos de inventario.",
    "puede_inventario": "Acceder a existencias y Kardex, y a las operaciones de inventario que utilicen este permiso. Los ajustes y las transferencias rápidas conservan sus permisos específicos.",
    "puede_transferir_inventario": "Realizar transferencias rápidas entre las bodegas y rutas admitidas por el flujo original. No concede consulta general de inventario por sí solo.",
    "puede_proveedores": "Consultar proveedores y los resúmenes de compras autorizados de su ficha. Las acciones de crear, editar o abrir una compra conservan sus permisos propios.",
    "puede_crear_proveedores": "Registrar proveedores nuevos mediante el formulario existente. No concede por sí solo acceso a compras o pagos a proveedores.",
    "puede_editar_proveedores": "Modificar los datos de proveedores mediante el editor existente; no habilita otras operaciones de compras o pagos.",
    "puede_configuracion_clinica": "Acceder a la configuración clínica y a sus catálogos autorizados. Algunas ediciones exigen además administración o propietario; no concede administración de usuarios.",
    # Catálogo legado: sus permisos pueden cubrir más de una operación.
    "puede_facturas": "Acceso legado al módulo de facturación y a las rutas que utilizan este permiso, como cotizaciones. Cada acción conserva los demás controles existentes.",
    "puede_cambiar_fecha_factura": "Usar la acción existente de cambiar la fecha de una factura, sujeta al estado del documento y a las validaciones originales.",
    "puede_eliminar_borradores": "Eliminar facturas en borrador mediante la acción original y sus restricciones. No autoriza por sí solo eliminar facturas de otros estados.",
    "puede_eliminar_facturas": "Usar la acción original de eliminar facturas cuando el documento y las validaciones existentes lo permitan.",
    "puede_registrar_pagos_clientes": "Registrar o corregir cobros de facturas mediante los flujos existentes y sus reglas de saldo, cuentas y contabilización.",
    "puede_punto_venta": "Acceder al punto de venta. Las acciones sobre documentos, clientes y productos siguen sujetas a sus permisos y validaciones.",
    "puede_cierres_caja": "Acceder a la gestión de cierres de caja disponible en el sistema. No realiza ni modifica un cierre al consultar estos permisos.",
    "puede_clientes": "Acceder al catálogo de clientes. Crear o modificar clientes utiliza los permisos de acción correspondientes.",
    "puede_crear_clientes": "Registrar clientes nuevos mediante el formulario existente, con las reglas vigentes de identificación y empresa.",
    "puede_editar_clientes": "Modificar datos de clientes mediante el editor existente y sus verificaciones de empresa.",
    "puede_recibos": "Consultar recibos y su PDF, y editar sus datos documentales mediante los flujos existentes. No equivale a registrar o corregir el pago asociado.",
    "puede_egresos": "Acceder a los comprobantes de egreso y sus documentos autorizados. No concede por sí solo registrar pagos a proveedores.",
    "puede_notas_credito": "Acceder al catálogo de notas de crédito. Crear, editar o anular documentos conserva los permisos específicos de cada acción.",
    "puede_crear_notas_credito": "Crear notas de crédito por el flujo original y sus verificaciones de factura, importes y estado.",
    "puede_editar_notas_credito": "Modificar notas de crédito en los estados admitidos por el editor original, sin omitir sus validaciones.",
    "puede_anular_notas_credito": "Solicitar la anulación de notas de crédito mediante la acción original y sus comprobaciones.",
    "puede_reportes": "Acceder a reportes de facturación disponibles para la empresa. Exportar información conserva el permiso de exportación correspondiente.",
    "puede_exportar_reportes": "Usar las exportaciones de reportes autorizadas, como Excel o PDF, dentro de los filtros y controles del reporte original.",
    "puede_cxc": "Consultar cuentas por cobrar y sus saldos calculados por el reporte existente. No habilita registrar cobros por sí solo.",
    "puede_clinica": "Acceso legado al módulo clínico y a las rutas que lo utilicen. Pacientes, expedientes y configuración pueden requerir permisos adicionales.",
    "puede_pacientes": "Permiso legado de pacientes: varias vistas lo utilizan también para crear, editar o gestionar seguimientos. No es equivalente al permiso granular de sólo ver el listado.",
    "puede_expediente_clinico": "Acceso legado a expedientes, historias y otras funciones clínicas que utilicen esta clave; cada vista mantiene sus requisitos adicionales.",
    "puede_tratamientos_clinicos": "Acceso legado a los flujos de tratamientos clínicos. Los permisos granulares de planes no sustituyen automáticamente este modo de rol.",
    "puede_citas": "Acceso legado a agenda y citas. Las vistas mantienen sus reglas sobre profesionales, disponibilidad y acciones permitidas.",
    "puede_ajustar_inventario": "Registrar ajustes de inventario mediante la acción original y sus validaciones. No modifica existencias al visualizar el permiso.",
    "puede_compras": "Consultar compras, sus líneas, pagos y documentos autorizados. Crear, aplicar, anular o pagar compras requiere los permisos específicos de esas acciones.",
    "puede_crear_compras": "Registrar compras nuevas mediante el flujo original. Su aplicación al inventario conserva un permiso separado.",
    "puede_editar_compras": "Modificar compras en los estados admitidos por el editor original, con sus verificaciones de líneas e importes.",
    "puede_aplicar_compras": "Aplicar una compra mediante la operación original, incluidos los efectos de inventario y contabilidad que ésta ya tenga.",
    "puede_anular_compras": "Solicitar la anulación de una compra mediante el flujo y las restricciones originales.",
    "puede_registrar_pagos_proveedores": "Registrar pagos a proveedores por los flujos originales, con sus validaciones de compra, cuenta y saldo.",
    "puede_cxp": "Consultar cuentas por pagar en las rutas autorizadas. No equivale al permiso de registrar pagos a proveedores.",
    "puede_contabilidad": "Acceso legado al módulo contable y a las operaciones que utilicen esta clave. Las acciones con permisos específicos mantienen esos controles.",
    "puede_catalogo_cuentas": "Consultar y administrar el catálogo de cuentas mediante sus rutas existentes. No otorga por sí solo contabilización de asientos.",
    "puede_crear_asientos": "Crear o preparar asientos contables y las acciones del editor que utilicen este permiso. Contabilizarlos requiere el permiso correspondiente.",
    "puede_contabilizar_asientos": "Ejecutar las acciones originales de contabilización, reversión y otras operaciones que comprueben esta clave, con sus validaciones.",
    "puede_reportes_contables": "Consultar reportes contables autorizados. No concede edición de cuentas ni contabilización por sí solo.",
    "puede_cai": "Acceder a la administración original de CAI y sus controles fiscales. Este resumen no cambia numeraciones ni autorizaciones.",
    "puede_impuestos": "Acceder al catálogo y a la administración original de impuestos, sin modificar tasas al visualizar estos permisos.",
    "puede_configuracion_facturacion": "Acceder a la configuración original de facturación. Sus formularios y restricciones se mantienen sin cambios.",
    "puede_rrhh": "Acceso legado a Recursos Humanos y a las rutas que utilicen esta clave. Los catálogos y operaciones específicas pueden exigir otros permisos.",
    "puede_empleados": "Acceder a la gestión de empleados disponible en el módulo de Recursos Humanos, con los controles originales de la empresa.",
    "puede_planillas": "Acceder a planillas y movimientos laborales que utilicen este permiso. Las validaciones y efectos del flujo original se conservan.",
    "puede_vacaciones": "Acceder a la gestión original de vacaciones y a sus acciones autorizadas.",
    "puede_configuracion_rrhh": "Acceder a la configuración de Recursos Humanos disponible para la empresa, con sus permisos y formularios originales.",
    "puede_crm": "Acceso legado al módulo CRM y a las rutas que utilicen esta clave. Agenda, campañas y configuración tienen sus comprobaciones adicionales.",
    "puede_campanias": "Acceder a campañas y plantillas de CRM en sus rutas existentes. Consultar este resumen no envía mensajes ni ejecuta campañas.",
    "puede_configuracion_crm": "Acceder a la configuración original de CRM, agenda y comunicaciones. No realiza conexiones, pruebas ni envíos al mostrar estos permisos.",
}


def detalle_rol_clinico(rol):
    """DTO puro con los True del mismo catálogo que usa _permisos_visibles_rol."""
    if rol is None:
        return None
    from .views import PERMISOS_ROL_CLINICO_LEGADO, _permisos_visibles_rol

    granular = bool(rol.usa_permisos_clinicos_granulares)
    catalogo = PERMISOS_CLINICOS if granular else PERMISOS_ROL_CLINICO_LEGADO
    visibles = {grupo["grupo"]: set(grupo["permisos"]) for grupo in _permisos_visibles_rol(rol)}
    grupos = []
    for nombre, permisos in catalogo:
        detalles = [
            {"campo": campo, "etiqueta": etiqueta, "descripcion": DESCRIPCIONES_PERMISOS[campo]}
            for campo, etiqueta in permisos if etiqueta in visibles.get(nombre, set())
        ]
        if detalles:
            grupos.append({"grupo": nombre, "permisos": detalles})
    advertencias = [
        "Son permisos configurados en el rol, no una garantía de acceso efectivo ni de disponibilidad en la app.",
        "Cada pantalla conserva sus controles de empresa, módulos, licencia y permisos adicionales.",
        "Las autorizaciones independientes de la cuenta, como administración de empresa, no se eliminan ni se describen como permisos de este rol.",
        "El resumen utiliza el catálogo de este modo de rol; otros permisos almacenados fuera de ese catálogo no se muestran aquí.",
    ]
    if not rol.activo:
        advertencias.insert(0, "El rol está inactivo: sus permisos configurados no conceden acceso mediante este rol.")
    return {
        "id": rol.pk, "nombre": rol.nombre, "descripcion_registrada": rol.descripcion or "",
        "activo": bool(rol.activo), "modo": "granular" if granular else "legado",
        "modo_label": "Permisos clínicos granulares" if granular else "Permisos legados",
        "total_permisos": sum(len(grupo["permisos"]) for grupo in grupos),
        "grupos": grupos, "advertencias": advertencias,
    }


def contexto_detalles_roles(empresa, roles, rol_actual=None, usuario=None):
    """Sólo presentación para las cuatro clínicas; no consulta relaciones u opciones."""
    if empresa.slug not in EMPRESAS_CLINICAS_CON_ROLES or not interfaz_clinica_activa(empresa):
        return {"clinical_role_help_enabled": False}
    detalles = {str(rol.pk): detalle_rol_clinico(rol) for rol in roles}
    actual = detalles.get(str(rol_actual.pk)) if rol_actual is not None else None
    if rol_actual is not None and actual is None:
        actual = detalle_rol_clinico(rol_actual)
    return {
        "clinical_role_help_enabled": True,
        "clinical_role_details": detalles,
        "clinical_current_role_details": actual,
        "clinical_role_selection_details": detalles.get(str(rol_actual.pk)) if rol_actual is not None else None,
        "clinical_role_independent_admin": bool(usuario and (usuario.es_administrador_empresa or usuario.is_superuser)),
    }
