from django.contrib import admin

from .models import BahiaServicio, CitaTaller, ConfiguracionTecnicentro, CotizacionTaller, DiagnosticoVehicular, EvidenciaOrden, HistorialEstadoOrden, InspeccionRecepcion, LineaCotizacionTaller, OrdenServicio, Vehiculo


admin.site.register([ConfiguracionTecnicentro, BahiaServicio, Vehiculo, OrdenServicio, CitaTaller, InspeccionRecepcion, HistorialEstadoOrden, DiagnosticoVehicular, EvidenciaOrden, CotizacionTaller, LineaCotizacionTaller])
