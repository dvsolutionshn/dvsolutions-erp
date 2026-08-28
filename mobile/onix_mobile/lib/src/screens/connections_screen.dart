import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../models.dart';
import '../onix_controller.dart';
import '../theme.dart';

class ConnectionsScreen extends StatefulWidget {
  const ConnectionsScreen({super.key, required this.controller});

  final OnixController controller;

  @override
  State<ConnectionsScreen> createState() => _ConnectionsScreenState();
}

class _ConnectionsScreenState extends State<ConnectionsScreen>
    with WidgetsBindingObserver {
  final _whatsapp = TextEditingController();
  bool _whatsappOptIn = false;
  String _reminderChannel = 'app';
  bool _initializedFields = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    widget.controller.loadConnections();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _whatsapp.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      widget.controller.loadConnections();
    }
  }

  void _syncFields(OnixPersonalProfile profile) {
    if (_initializedFields) return;
    _initializedFields = true;
    _whatsapp.text = profile.whatsapp;
    _whatsappOptIn = profile.whatsappOptIn;
    _reminderChannel = profile.reminderChannel;
  }

  Future<void> _saveProfile(OnixPersonalProfile profile) async {
    final ok = await widget.controller.savePersonalProfile(
      whatsapp: _whatsapp.text,
      whatsappOptIn: _whatsappOptIn,
      timezone: profile.timezone,
      reminderChannel: _reminderChannel,
    );
    if (ok && mounted) {
      _initializedFields = false;
      _syncFields(widget.controller.connections!.profile);
      setState(() {});
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('Preferencias guardadas.')));
    }
  }

  Future<void> _connectGoogle() async {
    final uri = await widget.controller.startGoogleConnection();
    if (uri == null || !mounted) return;
    final opened = await launchUrl(uri, mode: LaunchMode.externalApplication);
    if (!opened && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('No fue posible abrir Google.')),
      );
    }
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: widget.controller,
    builder: (context, _) {
      final data = widget.controller.connections;
      if (data != null) _syncFields(data.profile);
      return Scaffold(
        appBar: AppBar(
          title: const Text('Mis conexiones'),
          actions: [
            IconButton(
              tooltip: 'Actualizar',
              onPressed: widget.controller.loadingConnections
                  ? null
                  : widget.controller.loadConnections,
              icon: const Icon(Icons.refresh_rounded),
            ),
          ],
        ),
        body: data == null
            ? Center(
                child: widget.controller.loadingConnections
                    ? const CircularProgressIndicator()
                    : Padding(
                        padding: const EdgeInsets.all(24),
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            const Icon(Icons.cloud_off_rounded, size: 42),
                            const SizedBox(height: 14),
                            Text(
                              widget.controller.error ??
                                  'No fue posible cargar las conexiones.',
                              textAlign: TextAlign.center,
                            ),
                            const SizedBox(height: 16),
                            FilledButton(
                              onPressed: widget.controller.loadConnections,
                              child: const Text('Intentar nuevamente'),
                            ),
                          ],
                        ),
                      ),
              )
            : ListView(
                padding: const EdgeInsets.fromLTRB(18, 20, 18, 36),
                children: [
                  const Text(
                    'Conecta tu vida con ONIX',
                    style: TextStyle(
                      color: onixInk,
                      fontSize: 25,
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                  const SizedBox(height: 7),
                  const Text(
                    'Tú decides qué información puede usar ONIX. Puedes desconectar cualquier servicio cuando quieras.',
                    style: TextStyle(color: Color(0xFF5D7180), height: 1.45),
                  ),
                  const SizedBox(height: 22),
                  _ProfileCard(
                    profile: data.profile,
                    whatsapp: _whatsapp,
                    whatsappOptIn: _whatsappOptIn,
                    reminderChannel: _reminderChannel,
                    busy: widget.controller.loadingConnections,
                    onOptInChanged: (value) =>
                        setState(() => _whatsappOptIn = value),
                    onChannelChanged: (value) =>
                        setState(() => _reminderChannel = value),
                    onSave: () => _saveProfile(data.profile),
                  ),
                  const SizedBox(height: 18),
                  ...data.services.map(
                    (service) => Padding(
                      padding: const EdgeInsets.only(bottom: 12),
                      child: _ConnectionCard(
                        connection: service,
                        busy: widget.controller.loadingConnections,
                        onConnect: service.id == 'google_calendar'
                            ? _connectGoogle
                            : null,
                        onDisconnect: service.connected && service.id != 'email'
                            ? () => widget.controller.disconnectConnection(
                                service.id,
                              )
                            : null,
                      ),
                    ),
                  ),
                  if (widget.controller.error != null)
                    Padding(
                      padding: const EdgeInsets.only(top: 8),
                      child: Text(
                        widget.controller.error!,
                        style: const TextStyle(color: Colors.redAccent),
                      ),
                    ),
                ],
              ),
      );
    },
  );
}

class _ProfileCard extends StatelessWidget {
  const _ProfileCard({
    required this.profile,
    required this.whatsapp,
    required this.whatsappOptIn,
    required this.reminderChannel,
    required this.busy,
    required this.onOptInChanged,
    required this.onChannelChanged,
    required this.onSave,
  });

  final OnixPersonalProfile profile;
  final TextEditingController whatsapp;
  final bool whatsappOptIn;
  final String reminderChannel;
  final bool busy;
  final ValueChanged<bool> onOptInChanged;
  final ValueChanged<String> onChannelChanged;
  final VoidCallback onSave;

  @override
  Widget build(BuildContext context) => Card(
    child: Padding(
      padding: const EdgeInsets.all(18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Datos personales',
            style: TextStyle(fontSize: 17, fontWeight: FontWeight.w800),
          ),
          const SizedBox(height: 14),
          TextFormField(
            initialValue: profile.email,
            readOnly: true,
            decoration: const InputDecoration(
              labelText: 'Correo de acceso',
              prefixIcon: Icon(Icons.alternate_email_rounded),
            ),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: whatsapp,
            keyboardType: TextInputType.phone,
            decoration: InputDecoration(
              labelText: 'WhatsApp',
              hintText: '+504 9999-9999',
              prefixIcon: const Icon(Icons.chat_rounded),
              suffixIcon: profile.whatsappVerified
                  ? const Icon(Icons.verified_rounded, color: Color(0xFF0A8F72))
                  : null,
            ),
          ),
          SwitchListTile.adaptive(
            contentPadding: EdgeInsets.zero,
            title: const Text('Recibir recordatorios por WhatsApp'),
            subtitle: const Text(
              'ONIX solo enviará avisos que hayas autorizado.',
            ),
            value: whatsappOptIn,
            onChanged: onOptInChanged,
          ),
          DropdownButtonFormField<String>(
            initialValue: reminderChannel,
            decoration: const InputDecoration(labelText: 'Canal principal'),
            items: const [
              DropdownMenuItem(value: 'app', child: Text('Aplicación')),
              DropdownMenuItem(value: 'correo', child: Text('Correo')),
              DropdownMenuItem(value: 'whatsapp', child: Text('WhatsApp')),
            ],
            onChanged: (value) {
              if (value != null) onChannelChanged(value);
            },
          ),
          const SizedBox(height: 16),
          FilledButton.icon(
            onPressed: busy ? null : onSave,
            icon: const Icon(Icons.save_rounded),
            label: const Text('Guardar preferencias'),
          ),
        ],
      ),
    ),
  );
}

class _ConnectionCard extends StatelessWidget {
  const _ConnectionCard({
    required this.connection,
    required this.busy,
    this.onConnect,
    this.onDisconnect,
  });

  final OnixExternalConnection connection;
  final bool busy;
  final VoidCallback? onConnect;
  final VoidCallback? onDisconnect;

  IconData get icon => switch (connection.id) {
    'google_calendar' => Icons.event_available_rounded,
    'apple_calendar' => Icons.calendar_month_rounded,
    'whatsapp' => Icons.chat_bubble_rounded,
    _ => Icons.email_rounded,
  };

  @override
  Widget build(BuildContext context) => Card(
    child: Padding(
      padding: const EdgeInsets.all(17),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          CircleAvatar(
            backgroundColor: const Color(0xFFE8F7F7),
            foregroundColor: const Color(0xFF087E82),
            child: Icon(icon),
          ),
          const SizedBox(width: 13),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        connection.title,
                        style: const TextStyle(fontWeight: FontWeight.w800),
                      ),
                    ),
                    _StatusBadge(connection: connection),
                  ],
                ),
                const SizedBox(height: 5),
                Text(
                  connection.account.isNotEmpty
                      ? connection.account
                      : connection.description,
                  style: const TextStyle(
                    color: Color(0xFF5D7180),
                    height: 1.35,
                  ),
                ),
                if (connection.id == 'google_calendar') ...[
                  const SizedBox(height: 12),
                  if (connection.connected)
                    OutlinedButton(
                      onPressed: busy ? null : onDisconnect,
                      child: const Text('Desconectar'),
                    )
                  else
                    FilledButton(
                      onPressed: busy || !connection.configured
                          ? null
                          : onConnect,
                      child: Text(
                        connection.configured
                            ? 'Conectar con Google'
                            : 'Falta configurar Google',
                      ),
                    ),
                ],
              ],
            ),
          ),
        ],
      ),
    ),
  );
}

class _StatusBadge extends StatelessWidget {
  const _StatusBadge({required this.connection});

  final OnixExternalConnection connection;

  @override
  Widget build(BuildContext context) {
    final (label, color) = connection.connected
        ? ('Conectado', const Color(0xFF0A8F72))
        : connection.pending
        ? ('Pendiente', const Color(0xFFB36B00))
        : ('Sin conectar', const Color(0xFF6C7780));
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: color.withValues(alpha: .10),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: color,
          fontSize: 10,
          fontWeight: FontWeight.w800,
        ),
      ),
    );
  }
}
