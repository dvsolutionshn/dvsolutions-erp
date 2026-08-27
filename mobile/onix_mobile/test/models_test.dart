import 'package:flutter_test/flutter_test.dart';
import 'package:onix_mobile/src/models.dart';

void main() {
  test('construye el bootstrap y conserva categorias', () {
    final bootstrap = OnixBootstrap.fromJson({
      'user': {'name': 'Daniel'},
      'company': {'name': 'Demo 1', 'slug': 'demo_1'},
      'assistant': {'welcome': 'Hola'},
      'capabilities': {'chat': true},
      'categories': [
        {
          'id': 'facturas',
          'title': 'Facturas',
          'description': 'Consulta facturas',
          'icon': 'receipt_long',
          'status': 'available',
          'prompt': 'Muestrame facturas',
        },
      ],
    });

    expect(bootstrap.companySlug, 'demo_1');
    expect(bootstrap.categories.single.available, isTrue);
  });

  test('reemplaza una accion sin alterar el mensaje', () {
    const pending = OnixAction(
      id: 'accion-1',
      type: 'crear_borrador_factura',
      status: 'pendiente',
      data: {},
    );
    const executed = OnixAction(
      id: 'accion-1',
      type: 'crear_borrador_factura',
      status: 'ejecutada',
      data: {},
    );
    final message = OnixMessage(
      id: 'mensaje-1',
      role: 'asistente',
      content: 'Confirma',
      createdAt: DateTime(2026),
      actions: const [pending],
    );

    final updated = message.replaceAction(executed);

    expect(updated.content, 'Confirma');
    expect(updated.actions.single.status, 'ejecutada');
  });
}

