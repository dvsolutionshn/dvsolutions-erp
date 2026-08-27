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
}

class OnixBootstrap {
  const OnixBootstrap({
    required this.userName,
    required this.companyName,
    required this.companySlug,
    required this.welcome,
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
  final List<OnixCategory> categories;
  final Map<String, dynamic> capabilities;
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
  String get total => data['total']?.toString() ?? '';
  String get currency => data['currency']?.toString() ?? 'HNL';
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
