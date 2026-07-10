from django.urls import path

from . import views


urlpatterns = [
    path("", views.clinica_dashboard, name="clinica_dashboard"),
    path("pacientes/", views.pacientes, name="clinica_pacientes"),
    path("pacientes/sugerencias/", views.pacientes_sugerencias, name="clinica_pacientes_sugerencias"),
    path("pacientes/crear/", views.crear_paciente, name="clinica_crear_paciente"),
    path("pacientes/enlace-registro/generar/", views.generar_enlace_registro_paciente, name="clinica_generar_enlace_registro_paciente"),
    path("pacientes/<int:paciente_id>/", views.paciente_detalle, name="clinica_paciente_detalle"),
    path("pacientes/<int:paciente_id>/editar/", views.editar_paciente, name="clinica_editar_paciente"),
    path("pacientes/<int:paciente_id>/expediente/agregar/", views.crear_evento_expediente, name="clinica_crear_evento_paciente"),
    path("pacientes/<int:paciente_id>/evolucion/", views.evolucion_paciente, name="clinica_evolucion_paciente"),
    path("pacientes/<int:paciente_id>/evolucion/registrar/", views.registrar_foto_evolucion, name="clinica_registrar_foto_evolucion"),
    path("pacientes/<int:paciente_id>/consentimientos/", views.consentimientos_paciente, name="clinica_consentimientos_paciente"),
    path("pacientes/<int:paciente_id>/consentimientos/subir/", views.subir_consentimiento_paciente, name="clinica_subir_consentimiento_paciente"),
    path("pacientes/<int:paciente_id>/historias/", views.historias_especialidad, name="clinica_historias_especialidad"),
    path("pacientes/<int:paciente_id>/historias/consolidado/", views.historial_clinico_consolidado, name="clinica_historial_clinico_consolidado"),
    path("pacientes/<int:paciente_id>/historias/nueva/<slug:tipo>/", views.crear_historia_especialidad, name="clinica_crear_historia_especialidad"),
    path("pacientes/<int:paciente_id>/historias/<int:historia_id>/editar/", views.editar_historia_especialidad, name="clinica_editar_historia_especialidad"),
    path("pacientes/<int:paciente_id>/preconsulta/generar/", views.generar_enlace_preconsulta, name="clinica_generar_enlace_preconsulta"),
    path("pacientes/<int:paciente_id>/preconsulta/generar/<slug:tipo>/", views.generar_enlace_preconsulta, name="clinica_generar_enlace_preconsulta_tipo"),
    path("pacientes/<int:paciente_id>/preconsulta/<int:preconsulta_id>/", views.preconsulta_detalle, name="clinica_preconsulta_detalle"),
    path("citas/", views.citas, name="clinica_citas"),
    path("citas/crear/", views.crear_cita, name="clinica_crear_cita"),
    path("citas/pacientes/crear-rapido/", views.crear_paciente_rapido_cita, name="clinica_crear_paciente_rapido"),
    path("expediente/agregar/", views.crear_evento_expediente, name="clinica_crear_evento"),
    path("tratamientos/", views.tratamientos, name="clinica_tratamientos"),
    path("tratamientos/crear/", views.crear_tratamiento, name="clinica_crear_tratamiento"),
    path("profesionales/", views.profesionales, name="clinica_profesionales"),
    path("servicios/", views.servicios, name="clinica_servicios"),
]
