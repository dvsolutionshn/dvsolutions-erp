from django.db import migrations

# Solo nombres y orden de la referencia Nordic 2025, sin importes históricos.
CUENTAS = ((15, 'costo', 'Compras en PriceSmart'),
 (16, 'costo', 'Cinco Estrellas'),
 (17, 'costo', 'Compras en Supermercado la Colonia'),
 (18, 'costo', 'Larach'),
 (19, 'costo', 'Pedidos Ya'),
 (20, 'costo', 'DIPROVA'),
 (21, 'costo', 'La Mundial'),
 (22, 'costo', 'Almacenes Xtra'),
 (23, 'costo', 'La Confianza'),
 (24, 'costo', 'Compras wallmart'),
 (25, 'costo', 'Compras en Paiz'),
 (26, 'costo', 'Compras de Café'),
 (27, 'costo', 'Compra de Extintores'),
 (28, 'costo', 'Compra de Telefono'),
 (29, 'costo', 'Compra en Supermercado mas x menos'),
 (30, 'costo', 'Huevos'),
 (31, 'costo', 'Lacteos'),
 (32, 'costo', 'Azucar'),
 (33, 'costo', 'hielo'),
 (34, 'costo', 'Reposteria'),
 (35, 'costo', 'Servilletas, Pajillas, contenedor termo u otros'),
 (36, 'costo', 'Empaques,plasticos'),
 (37, 'costo', 'Gas LPG'),
 (38, 'costo', 'Insumos Varios'),
 (39, 'costo', 'Compras de Refrescos y Agua'),
 (40, 'costo', 'Refrecos y agua'),
 (47, 'gasto', 'Sueldos y salarios'),
 (48, 'gasto', 'Sueldos Temporales'),
 (49, 'gasto', 'Decimo Tercer mes'),
 (50, 'gasto', 'Decimo cuarto mes'),
 (51, 'gasto', 'Alimentacion de personal'),
 (52, 'gasto', 'Gastos de Personal'),
 (53, 'gasto', 'Prestaciones laborales'),
 (54, 'gasto', 'Gasolina y trasporte'),
 (55, 'gasto', 'IHSS'),
 (56, 'gasto', 'Diversos Gastos'),
 (57, 'gasto', 'Alquiler de Local'),
 (58, 'gasto', 'Matenimiento'),
 (59, 'gasto', 'Otras Reparaciones y Materiales (Mantenimiento)'),
 (60, 'gasto', 'Estacionamiento y Parqueo'),
 (61, 'gasto', 'Energia Electrica'),
 (62, 'gasto', 'Agua potable'),
 (63, 'gasto', 'Telefonos fijos y celulares'),
 (64, 'gasto', 'Cable e Internet'),
 (65, 'gasto', 'Materiales de aseo y Limpieza'),
 (66, 'gasto', 'Utensilios de cocina al costo'),
 (67, 'gasto', 'Papeleria y Utiles'),
 (68, 'gasto', 'Envios'),
 (69, 'gasto', 'Licencias y Suscripciones'),
 (70, 'gasto', 'Otras Reparaciones y Materiales'),
 (71, 'gasto', 'Depreciacion Mobiliario y Equipo'),
 (72, 'gasto', 'Depreciacion de Accesorios y'),
 (73, 'gasto', 'Depreciacion Sistemas de Aires'),
 (74, 'gasto', 'Depreciacion de Vidrios'),
 (75, 'gasto', 'Depreciacion de Sistemas de Audio'),
 (76, 'gasto', 'Depreciacion de Lamparas y'),
 (77, 'gasto', 'Depreciaciones Instalaciones area'),
 (78, 'gasto', 'Depreciacion de Camaras de'),
 (79, 'gasto', 'Depreciacion de Rotulos'),
 (80, 'gasto', 'Amortizacion de Construcciones'),
 (81, 'gasto', 'Amortizacion de Gastos de Organización'),
 (82, 'gasto', 'Amortizacion de Software'),
 (83, 'gasto', 'Volumen de Ventas'),
 (84, 'gasto', 'Tasa de Seguridad  Ficohsa'),
 (85, 'gasto', 'Comisiones Ficohsa'),
 (91, 'financiero', 'Gastos Financieros -Intereses y Comisiones prestamos'))


def preparar_nordic(apps, schema_editor):
    Cliente = apps.get_model('facturacion', 'ClienteContable')
    Cuenta = apps.get_model('facturacion', 'CuentaAcumuladoCompra')
    alias = schema_editor.connection.alias
    for cliente in Cliente.objects.using(alias).filter(empresa__slug='dubon_asociados', nombre__iexact='Nordic'):
        for orden, grupo, nombre in CUENTAS:
            Cuenta.objects.using(alias).get_or_create(cliente_contable_id=cliente.pk, nombre=nombre,
                defaults={'orden':orden, 'grupo':grupo})


class Migration(migrations.Migration):
    dependencies = [('facturacion', '0077_cuentas_acumulado_compras')]
    operations = [migrations.RunPython(preparar_nordic, migrations.RunPython.noop)]
