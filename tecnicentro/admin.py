from django.contrib import admin

from .models import BahiaServicio, ConfiguracionTecnicentro, CotizacionTaller, DiagnosticoVehicular, EvidenciaOrden, HistorialEstadoOrden, LineaCotizacionTaller, OrdenServicio, Vehiculo


admin.site.register([ConfiguracionTecnicentro, BahiaServicio, Vehiculo, OrdenServicio, HistorialEstadoOrden, DiagnosticoVehicular, EvidenciaOrden, CotizacionTaller, LineaCotizacionTaller])
