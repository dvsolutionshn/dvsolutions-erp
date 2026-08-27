import 'package:flutter/material.dart';

import '../app.dart';
import '../models.dart';
import '../onix_controller.dart';
import '../theme.dart';

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key, required this.controller});

  final OnixController controller;

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final _composer = TextEditingController();
  final _scroll = ScrollController();

  @override
  void didUpdateWidget(covariant ChatScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    _scheduleScroll();
  }

  @override
  void dispose() {
    _composer.dispose();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _send([String? selectedPrompt]) async {
    final text = (selectedPrompt ?? _composer.text).trim();
    if (text.isEmpty || widget.controller.sending) return;
    _composer.clear();
    _scheduleScroll();
    await widget.controller.send(text);
    _scheduleScroll();
  }

  void _scheduleScroll() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(
          _scroll.position.maxScrollExtent,
          duration: const Duration(milliseconds: 280),
          curve: Curves.easeOut,
        );
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final bootstrap = widget.controller.bootstrap!;
    return Scaffold(
      appBar: AppBar(
        backgroundColor: onixNavy,
        foregroundColor: Colors.white,
        toolbarHeight: 72,
        titleSpacing: 16,
        title: Row(
          children: [
            const OnixAvatar(size: 46),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'Onix',
                    style: TextStyle(fontSize: 19, fontWeight: FontWeight.w800),
                  ),
                  Row(
                    children: [
                      const SizedBox(
                        width: 7,
                        height: 7,
                        child: DecoratedBox(
                          decoration: BoxDecoration(
                            color: onixCyan,
                            shape: BoxShape.circle,
                          ),
                        ),
                      ),
                      const SizedBox(width: 6),
                      Flexible(
                        child: Text(
                          '${bootstrap.companyName} · conectado',
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            fontSize: 12,
                            color: Colors.white.withValues(alpha: .66),
                          ),
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ],
        ),
        actions: [
          PopupMenuButton<String>(
            onSelected: (value) {
              if (value == 'logout') widget.controller.logout();
            },
            itemBuilder: (_) => const [
              PopupMenuItem(
                value: 'logout',
                child: ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: Icon(Icons.logout_rounded),
                  title: Text('Cerrar sesion'),
                ),
              ),
            ],
          ),
        ],
      ),
      body: SafeArea(
        top: false,
        child: Column(
          children: [
            _CategoryStrip(
              categories: bootstrap.categories,
              onSelected: (category) {
                if (category.available) {
                  setState(() {
                    _composer.text = category.prompt;
                    _composer.selection = TextSelection.collapsed(
                      offset: _composer.text.length,
                    );
                  });
                } else {
                  ScaffoldMessenger.of(context).showSnackBar(
                    SnackBar(
                      content: Text(
                        '${category.title} sera conectado en la siguiente etapa.',
                      ),
                    ),
                  );
                }
              },
            ),
            if (widget.controller.error != null)
              MaterialBanner(
                content: Text(widget.controller.error!),
                leading: const Icon(Icons.info_outline_rounded),
                actions: [
                  TextButton(
                    onPressed: widget.controller.clearError,
                    child: const Text('Cerrar'),
                  ),
                ],
              ),
            Expanded(
              child: widget.controller.messages.isEmpty
                  ? _EmptyConversation(
                      welcome: bootstrap.welcome,
                      onPrompt: _send,
                    )
                  : ListView.builder(
                      controller: _scroll,
                      padding: const EdgeInsets.fromLTRB(16, 20, 16, 24),
                      itemCount:
                          widget.controller.messages.length +
                          (widget.controller.sending ? 1 : 0),
                      itemBuilder: (context, index) {
                        if (index == widget.controller.messages.length) {
                          return const _ThinkingBubble();
                        }
                        return _MessageBubble(
                          message: widget.controller.messages[index],
                          onDecision: (action, decision) async {
                            final ok = await widget.controller.decide(
                              action,
                              decision,
                            );
                            if (ok && context.mounted) {
                              ScaffoldMessenger.of(context).showSnackBar(
                                SnackBar(
                                  content: Text(
                                    decision == 'confirmar'
                                        ? 'Onix ejecuto la accion correctamente.'
                                        : 'Accion descartada sin realizar cambios.',
                                  ),
                                ),
                              );
                            }
                          },
                        );
                      },
                    ),
            ),
            _Composer(
              controller: _composer,
              sending: widget.controller.sending,
              onSend: _send,
            ),
          ],
        ),
      ),
    );
  }
}

class _CategoryStrip extends StatelessWidget {
  const _CategoryStrip({required this.categories, required this.onSelected});

  final List<OnixCategory> categories;
  final ValueChanged<OnixCategory> onSelected;

  @override
  Widget build(BuildContext context) => Container(
    height: 76,
    color: Colors.white,
    child: ListView.separated(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 13),
      scrollDirection: Axis.horizontal,
      itemCount: categories.length,
      separatorBuilder: (_, _) => const SizedBox(width: 8),
      itemBuilder: (context, index) {
        final category = categories[index];
        return ActionChip(
          onPressed: () => onSelected(category),
          avatar: Icon(_categoryIcon(category.icon), size: 18),
          label: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                category.title,
                style: const TextStyle(fontWeight: FontWeight.w700),
              ),
              if (!category.available)
                const Text(
                  'Proximamente',
                  style: TextStyle(fontSize: 9, color: Color(0xFF7C6FE7)),
                ),
            ],
          ),
          side: BorderSide(
            color: category.available
                ? const Color(0xFFCCE3E4)
                : const Color(0xFFE3DFFD),
          ),
          backgroundColor: category.available
              ? const Color(0xFFF1FAFA)
              : const Color(0xFFF8F6FF),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(16),
          ),
        );
      },
    ),
  );
}

IconData _categoryIcon(String icon) => switch (icon) {
  'dashboard' => Icons.dashboard_rounded,
  'receipt_long' => Icons.receipt_long_rounded,
  'payments' => Icons.payments_rounded,
  'groups' => Icons.groups_rounded,
  'inventory_2' => Icons.inventory_2_rounded,
  'calendar_month' => Icons.calendar_month_rounded,
  'trending_down' => Icons.trending_down_rounded,
  'account_balance_wallet' => Icons.account_balance_wallet_rounded,
  'account_balance' => Icons.account_balance_rounded,
  'apartment' => Icons.apartment_rounded,
  _ => Icons.auto_awesome_rounded,
};

class _EmptyConversation extends StatelessWidget {
  const _EmptyConversation({required this.welcome, required this.onPrompt});

  final String welcome;
  final ValueChanged<String> onPrompt;

  @override
  Widget build(BuildContext context) => SingleChildScrollView(
    padding: const EdgeInsets.all(24),
    child: Column(
      children: [
        const SizedBox(height: 28),
        const OnixAvatar(size: 76),
        const SizedBox(height: 22),
        Text(
          welcome,
          textAlign: TextAlign.center,
          style: const TextStyle(
            fontSize: 22,
            fontWeight: FontWeight.w800,
            color: onixInk,
            height: 1.25,
          ),
        ),
        const SizedBox(height: 10),
        const Text(
          'Puedes preguntarme, consultar informacion o pedirme que prepare una accion.',
          textAlign: TextAlign.center,
          style: TextStyle(color: Color(0xFF5D7180), height: 1.45),
        ),
        const SizedBox(height: 28),
        _QuickPrompt(text: 'Dame el resumen de la empresa', onTap: onPrompt),
        _QuickPrompt(text: 'Muestrame las facturas recientes', onTap: onPrompt),
        _QuickPrompt(
          text: 'Que clientes tienen saldos pendientes',
          onTap: onPrompt,
        ),
      ],
    ),
  );
}

class _QuickPrompt extends StatelessWidget {
  const _QuickPrompt({required this.text, required this.onTap});

  final String text;
  final ValueChanged<String> onTap;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(bottom: 10),
    child: Material(
      color: Colors.white,
      borderRadius: BorderRadius.circular(16),
      child: InkWell(
        onTap: () => onTap(text),
        borderRadius: BorderRadius.circular(16),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Row(
            children: [
              const Icon(
                Icons.auto_awesome_rounded,
                color: Color(0xFF087E82),
                size: 20,
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  text,
                  style: const TextStyle(fontWeight: FontWeight.w600),
                ),
              ),
              const Icon(Icons.arrow_forward_ios_rounded, size: 14),
            ],
          ),
        ),
      ),
    ),
  );
}

class _MessageBubble extends StatelessWidget {
  const _MessageBubble({required this.message, required this.onDecision});

  final OnixMessage message;
  final Future<void> Function(OnixAction, String) onDecision;

  @override
  Widget build(BuildContext context) => Align(
    alignment: message.fromUser ? Alignment.centerRight : Alignment.centerLeft,
    child: Container(
      constraints: BoxConstraints(
        maxWidth: MediaQuery.sizeOf(context).width * .86,
      ),
      margin: const EdgeInsets.only(bottom: 14),
      child: Column(
        crossAxisAlignment: message.fromUser
            ? CrossAxisAlignment.end
            : CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              if (!message.fromUser) ...[
                const OnixAvatar(size: 30),
                const SizedBox(width: 8),
              ],
              Flexible(
                child: Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 16,
                    vertical: 13,
                  ),
                  decoration: BoxDecoration(
                    color: message.fromUser ? onixNavy : Colors.white,
                    borderRadius: BorderRadius.only(
                      topLeft: const Radius.circular(19),
                      topRight: const Radius.circular(19),
                      bottomLeft: Radius.circular(message.fromUser ? 19 : 5),
                      bottomRight: Radius.circular(message.fromUser ? 5 : 19),
                    ),
                    boxShadow: const [
                      BoxShadow(
                        color: Color(0x12000000),
                        blurRadius: 14,
                        offset: Offset(0, 5),
                      ),
                    ],
                  ),
                  child: SelectableText(
                    message.content,
                    style: TextStyle(
                      color: message.fromUser ? Colors.white : onixInk,
                      height: 1.45,
                      fontSize: 15,
                    ),
                  ),
                ),
              ),
            ],
          ),
          for (final action in message.actions)
            _ActionCard(action: action, onDecision: onDecision),
        ],
      ),
    ),
  );
}

class _ActionCard extends StatefulWidget {
  const _ActionCard({required this.action, required this.onDecision});

  final OnixAction action;
  final Future<void> Function(OnixAction, String) onDecision;

  @override
  State<_ActionCard> createState() => _ActionCardState();
}

class _ActionCardState extends State<_ActionCard> {
  bool processing = false;

  Future<void> _decide(String decision) async {
    setState(() => processing = true);
    await widget.onDecision(widget.action, decision);
    if (mounted) setState(() => processing = false);
  }

  @override
  Widget build(BuildContext context) {
    final action = widget.action;
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(left: 38, top: 10),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: const Color(0xFFF0FBFA),
        border: Border.all(color: const Color(0xFF9ADAD7)),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.fact_check_rounded, color: Color(0xFF087E82)),
              const SizedBox(width: 9),
              Expanded(
                child: Text(
                  action.title,
                  style: const TextStyle(fontWeight: FontWeight.w800),
                ),
              ),
              _ActionStatus(status: action.status),
            ],
          ),
          if (action.description.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(
              action.description,
              style: const TextStyle(color: Color(0xFF4C6670)),
            ),
          ],
          if (action.clientName.isNotEmpty) ...[
            const SizedBox(height: 14),
            Text(
              'Cliente: ${action.clientName}',
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
          ],
          for (final item in action.items.take(4))
            Padding(
              padding: const EdgeInsets.only(top: 7),
              child: Row(
                children: [
                  Expanded(child: Text(item['producto']?.toString() ?? 'Item')),
                  Text('x${item['cantidad'] ?? ''}'),
                  const SizedBox(width: 10),
                  Text(item['total']?.toString() ?? ''),
                ],
              ),
            ),
          if (action.total.isNotEmpty) ...[
            const Divider(height: 26),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Text(
                  'Total',
                  style: TextStyle(fontWeight: FontWeight.w700),
                ),
                Text(
                  '${action.currency} ${action.total}',
                  style: const TextStyle(
                    fontSize: 19,
                    fontWeight: FontWeight.w900,
                    color: onixInk,
                  ),
                ),
              ],
            ),
          ],
          if (action.pending) ...[
            const SizedBox(height: 14),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: processing ? null : () => _decide('cancelar'),
                    child: const Text('Descartar'),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: FilledButton(
                    onPressed: processing ? null : () => _decide('confirmar'),
                    style: FilledButton.styleFrom(
                      minimumSize: const Size.fromHeight(46),
                    ),
                    child: processing
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(
                              color: Colors.white,
                              strokeWidth: 2,
                            ),
                          )
                        : const Text('Confirmar'),
                  ),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

class _ActionStatus extends StatelessWidget {
  const _ActionStatus({required this.status});

  final String status;

  @override
  Widget build(BuildContext context) {
    final label = switch (status) {
      'pendiente' => 'Por confirmar',
      'ejecutada' => 'Ejecutada',
      'cancelada' => 'Descartada',
      'expirada' => 'Vencida',
      _ => status,
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(99),
      ),
      child: Text(
        label,
        style: const TextStyle(fontSize: 10, fontWeight: FontWeight.w800),
      ),
    );
  }
}

class _ThinkingBubble extends StatelessWidget {
  const _ThinkingBubble();

  @override
  Widget build(BuildContext context) => const Align(
    alignment: Alignment.centerLeft,
    child: Padding(
      padding: EdgeInsets.only(bottom: 14),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          OnixAvatar(size: 30),
          SizedBox(width: 8),
          DecoratedBox(
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.all(Radius.circular(18)),
            ),
            child: Padding(
              padding: EdgeInsets.symmetric(horizontal: 18, vertical: 13),
              child: SizedBox(
                width: 18,
                height: 18,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: Color(0xFF087E82),
                ),
              ),
            ),
          ),
        ],
      ),
    ),
  );
}

class _Composer extends StatelessWidget {
  const _Composer({
    required this.controller,
    required this.sending,
    required this.onSend,
  });

  final TextEditingController controller;
  final bool sending;
  final VoidCallback onSend;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.fromLTRB(12, 10, 12, 12),
    decoration: const BoxDecoration(
      color: Colors.white,
      border: Border(top: BorderSide(color: Color(0xFFE0E8ED))),
    ),
    child: Row(
      crossAxisAlignment: CrossAxisAlignment.end,
      children: [
        IconButton(
          onPressed: () => ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(
              content: Text(
                'Archivos y estados de cuenta se conectaran en la siguiente etapa.',
              ),
            ),
          ),
          tooltip: 'Adjuntar',
          icon: const Icon(Icons.add_circle_outline_rounded),
        ),
        Expanded(
          child: TextField(
            controller: controller,
            minLines: 1,
            maxLines: 5,
            textCapitalization: TextCapitalization.sentences,
            textInputAction: TextInputAction.send,
            onSubmitted: (_) => onSend(),
            decoration: const InputDecoration(
              hintText: 'Escribe lo que necesitas...',
              contentPadding: EdgeInsets.symmetric(
                horizontal: 16,
                vertical: 12,
              ),
            ),
          ),
        ),
        const SizedBox(width: 7),
        IconButton.filled(
          onPressed: sending ? null : onSend,
          tooltip: 'Enviar',
          style: IconButton.styleFrom(
            backgroundColor: const Color(0xFF087E82),
            foregroundColor: Colors.white,
          ),
          icon: const Icon(Icons.arrow_upward_rounded),
        ),
      ],
    ),
  );
}
