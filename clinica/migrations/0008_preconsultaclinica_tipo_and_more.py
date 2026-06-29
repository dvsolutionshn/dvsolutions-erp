from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clinica', '0007_alter_historiaclinicaespecialidad_tipo'),
    ]

    operations = [
        migrations.AddField(
            model_name='preconsultaclinica',
            name='tipo',
            field=models.CharField(
                choices=[
                    ('general', 'General'),
                    ('capilar', 'Capilar'),
                    ('cirugia_plastica', 'Cirugia plastica y reconstructiva'),
                    ('medicina_estetica', 'Medicina Estetica'),
                    ('enfermeria', 'Enfermeria'),
                    ('terapias', 'Terapias'),
                    ('camara_hiperbarica', 'Camara hiperbarica'),
                ],
                default='general',
                max_length=30,
            ),
        ),
        migrations.AddIndex(
            model_name='preconsultaclinica',
            index=models.Index(
                fields=['empresa', 'paciente', 'tipo'],
                name='clinica_pre_empresa_395437_idx',
            ),
        ),
    ]
