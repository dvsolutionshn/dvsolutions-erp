from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0057_permisos_restriccion_citas_paciente")]

    operations = [
        migrations.AddField(model_name="rolsistema", name="usa_permisos_clinicos_v2", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_editar_pagos_clientes", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_anular_pagos_clientes", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_correccion_fiscal_facturas", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_imprimir_facturas", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_enviar_facturas", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_ver_reporte_ventas_producto", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_ver_kardex", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_crear_lotes", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_ver_bodegas", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_registrar_salidas_inventario", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_eliminar_productos", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_descargar_anexos_clinicos", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_crear_bloqueos_agenda", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_editar_bloqueos_agenda", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_eliminar_bloqueos_agenda", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_marcar_citas_pagadas", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_marcar_citas_cortesia", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_ver_antecedentes_clinicos", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_editar_antecedentes_clinicos", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_ver_examen_fisico", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_editar_examen_fisico", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_ver_diagnosticos", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_editar_diagnosticos", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_ver_evaluacion_integral", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_editar_evaluacion_integral", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_ver_capilar", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_editar_capilar", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_ver_tricopigmentacion", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_editar_tricopigmentacion", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_ver_cirugia_clinica", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_editar_cirugia_clinica", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_ver_medicina_estetica", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_editar_medicina_estetica", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_ver_enfermeria", field=models.BooleanField(default=False)),
    ]
