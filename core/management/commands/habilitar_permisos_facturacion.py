from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from core.models import Empresa, RolSistema, Usuario, UsuarioEmpresaPermiso


class Command(BaseCommand):
    help = 'Habilita los tres permisos avanzados en roles individuales por empresa, conservando los demás permisos.'

    def add_arguments(self, parser):
        usuario = parser.add_mutually_exclusive_group(required=True)
        usuario.add_argument('--usuario-id', type=int)
        usuario.add_argument('--usuario-email')
        usuario.add_argument('--usuario', help='Nombre de usuario exacto (username).')
        empresas = parser.add_mutually_exclusive_group(required=True)
        empresas.add_argument('--empresa-ids', type=int, nargs='+')
        empresas.add_argument('--empresas', nargs='+', help='Slugs usados únicamente para localizar las empresas.')

    @transaction.atomic
    def handle(self, *args, **options):
        if options.get('usuario_id'):
            usuarios = Usuario.objects.filter(pk=options['usuario_id'])
        elif options.get('usuario'):
            usuarios = Usuario.objects.filter(username__iexact=options['usuario'])
        else:
            usuarios = Usuario.objects.filter(email__iexact=options['usuario_email'])
        if usuarios.count() != 1:
            raise CommandError('Debe existir exactamente un usuario con ese identificador; no se realizó ningún cambio.')
        usuario = usuarios.get()
        identificadores = set(options.get('empresa_ids') or options['empresas'])
        filtro = {'pk__in': identificadores} if options.get('empresa_ids') else {'slug__in': identificadores}
        empresas = list(Empresa.objects.filter(**filtro))
        if len(empresas) != len(identificadores):
            raise CommandError('El usuario y todas las empresas deben existir; no se realizó ningún cambio.')
        if any(not usuario.puede_acceder_empresa(empresa) for empresa in empresas):
            raise CommandError('El usuario debe tener acceso previo a todas las empresas.')
        for empresa in empresas:
            actual = usuario.rol_para_empresa(empresa)
            valores = {
                campo.name: bool(actual and actual.activo and getattr(actual, campo.name))
                for campo in RolSistema._meta.fields if campo.name.startswith('puede_')
            }
            valores.update(puede_editar_facturas=True, puede_anular_facturas=True, puede_cambiar_fecha_factura=True)
            rol, _ = RolSistema.objects.update_or_create(
                codigo=f'clinico-{empresa.pk}-{usuario.pk}',
                defaults={'nombre': f'Permisos de {usuario.username} - {empresa.nombre}'[:120], 'activo': True, **valores},
            )
            UsuarioEmpresaPermiso.objects.update_or_create(
                usuario=usuario, empresa=empresa, defaults={'rol_sistema': rol, 'activo': True},
            )
            self.stdout.write(self.style.SUCCESS(f'Permisos habilitados: usuario {usuario.pk}, empresa {empresa.pk}'))
