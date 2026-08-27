class OnixCategory {
  const OnixCategory({
    required this.id,
    required this.title,
    required this.description,
    required this.icon,
    required this.status,
    required this.prompt,
  });

  factory OnixCategory.fromJson(Map<String, dynamic> json) => OnixCategory(
    id: json['id']?.toString() ?? '',
    title: json['title']?.toString() ?? '',
    description: json['description']?.toString() ?? '',
    icon: json['icon']?.toString() ?? '',
    status: json['status']?.toString() ?? 'next',
    prompt: json['prompt']?.toString() ?? '',
  );

  final String id;
  final String title;
  final String description;
  final String icon;
  final String status;
  final String prompt;

  bool get available => status == 'available';
  bool get restricted => status == 'restricted';
}

class OnixBootstrap {
  const OnixBootstrap({
    required this.userName,
    required this.companyName,
    required this.companySlug,
    required this.welcome,
    required this.assistantMode,
    required this.assistantStatus,
    required this.model,
    required this.categories,
    required this.capabilities,
  });

  factory OnixBootstrap.fromJson(Map<String, dynamic> json) {
    final user = Map<String, dynamic>.from(json['user'] as Map? ?? const {});
    final company = Map<String, dynamic>.from(
      json['company'] as Map? ?? const {},
    );
    final assistant = Map<String, dynamic>.from(
      json['assistant'] as Map? ?? const {},
    );
    return OnixBootstrap(
      userName: user['name']?.toString() ?? '',
      companyName: company['name']?.toString() ?? '',
      companySlug: company['slug']?.toString() ?? '',
      welcome: assistant['welcome']?.toString() ?? 'Hola. Soy Onix.',
      assistantMode: assistant['mode']?.toString() ?? 'guided',
      assistantStatus: assistant['status']?.toString() ?? 'Modo guiado',
      model: assistant['model']?.toString() ?? '',
      categories: (json['categories'] as List? ?? const [])
          .whereType<Map>()
          .map((item) => OnixCategory.fromJson(Map<String, dynamic>.from(item)))
          .toList(),
      capabilities: Map<String, dynamic>.from(
        json['capabilities'] as Map? ?? const {},
      ),
    );
  }

  final String userName;
  final String companyName;
  final String companySlug;
  final String welcome;
  final String assistantMode;
  final String assistantStatus;
  final String model;
  final List<OnixCategory> categories;
  final Map<String, dynamic> capabilities;

  bool get aiActive => assistantMode == 'ai' && capabilities['ai'] == true;
}

class OnixAction {
  const OnixAction({
    required this.id,
    required this.type,
    required this.status,
    required this.data,
  });

  factory OnixAction.fromJson(Map<String, dynamic> json) => OnixAction(
    id: json['id']?.toString() ?? '',
    type: json['type']?.toString() ?? '',
    status: json['status']?.toString() ?? '',
    data: json,
  );

  final String id;
  final String type;
  final String status;
  final Map<String, dynamic> data;

  bool get pending => status == 'pendiente';
  String get title => data['title']?.toString() ?? 'Accion de Onix';
  String get description => data['description']?.toString() ?? '';
  String get confirmationLabel =>
      data['confirmation_label']?.toString() ?? 'Confirmar';
  String get total => data['total']?.toString() ?? '';
  String get currency => data['currency']?.toString() ?? 'HNL';
  Map<String, dynamic> get result =>
      Map<String, dynamic>.from(data['result'] as Map? ?? const {});
  int? get invoiceId => int.tryParse(
    (result['invoice_id'] ?? data['invoice_id'])?.toString() ?? '',
  );
  String get invoiceNumber =>
      result['number']?.toString() ?? data['number']?.toString() ?? '';
  String get invoiceStatus => result['status']?.toString() ?? '';
  bool get pdfAvailable => result['pdf_available'] == true;
  String get clientName {
    final client = data['client'];
    if (client is Map) return client['name']?.toString() ?? '';
    return '';
  }

  List<Map<String, dynamic>> get items => (data['items'] as List? ?? const [])
      .whereType<Map>()
      .map((item) => Map<String, dynamic>.from(item))
      .toList();
}

class OnixMessage {
  const OnixMessage({
    required this.id,
    required this.role,
    required this.content,
    required this.createdAt,
    this.actions = const [],
    this.pending = false,
  });

  factory OnixMessage.fromJson(Map<String, dynamic> json) => OnixMessage(
    id:
        json['id']?.toString() ??
        DateTime.now().microsecondsSinceEpoch.toString(),
    role: json['role']?.toString() ?? 'asistente',
    content: json['content']?.toString() ?? '',
    createdAt:
        DateTime.tryParse(json['created_at']?.toString() ?? '') ??
        DateTime.now(),
    actions: (json['actions'] as List? ?? const [])
        .whereType<Map>()
        .map((item) => OnixAction.fromJson(Map<String, dynamic>.from(item)))
        .toList(),
  );

  final String id;
  final String role;
  final String content;
  final DateTime createdAt;
  final List<OnixAction> actions;
  final bool pending;

  bool get fromUser => role == 'usuario';

  OnixMessage replaceAction(OnixAction action) => OnixMessage(
    id: id,
    role: role,
    content: content,
    createdAt: createdAt,
    pending: pending,
    actions: actions
        .map((current) => current.id == action.id ? action : current)
        .toList(),
  );
}

class OnixLoginResult {
  const OnixLoginResult({required this.token, required this.bootstrap});

  final String token;
  final OnixBootstrap bootstrap;
}
