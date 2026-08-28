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

  test('expone el resultado fiscal y el PDF de una accion ejecutada', () {
    final action = OnixAction.fromJson({
      'id': 'accion-emision',
      'type': 'emitir_factura',
      'status': 'ejecutada',
      'confirmation_label': 'Validar y emitir',
      'result': {
        'invoice_id': 42,
        'number': '001-001-01-00000042',
        'status': 'emitida',
        'pdf_available': true,
      },
    });

    expect(action.confirmationLabel, 'Validar y emitir');
    expect(action.invoiceId, 42);
    expect(action.invoiceNumber, '001-001-01-00000042');
    expect(action.invoiceStatus, 'emitida');
    expect(action.pdfAvailable, isTrue);
  });

  test('interpreta el perfil y las conexiones externas de Onix', () {
    final connections = OnixConnections.fromJson({
      'profile': {
        'email': 'persona@example.com',
        'whatsapp': '+50499991234',
        'whatsapp_verified': false,
        'whatsapp_opt_in': true,
        'timezone': 'America/Tegucigalpa',
        'reminder_channel': 'whatsapp',
      },
      'services': [
        {
          'id': 'google_calendar',
          'title': 'Google Calendar',
          'description': 'Calendario personal',
          'status': 'conectada',
          'account': 'persona@gmail.com',
          'configured': true,
          'action': 'oauth',
        },
      ],
    });

    expect(connections.profile.whatsappOptIn, isTrue);
    expect(connections.profile.reminderChannel, 'whatsapp');
    expect(connections.services.single.connected, isTrue);
    expect(connections.services.single.account, 'persona@gmail.com');
  });
}
