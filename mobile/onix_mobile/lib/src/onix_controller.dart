import 'package:flutter/foundation.dart';

import 'models.dart';
import 'onix_api.dart';

class OnixController extends ChangeNotifier {
  OnixController({OnixApi? api}) : _api = api ?? OnixApi();

  final OnixApi _api;
  OnixBootstrap? bootstrap;
  List<OnixMessage> messages = [];
  bool initializing = true;
  bool authenticating = false;
  bool sending = false;
  String? error;

  bool get authenticated => bootstrap != null;

  Future<void> initialize() async {
    initializing = true;
    notifyListeners();
    try {
      final token = await _api.readToken();
      if (token != null && token.isNotEmpty) {
        bootstrap = await _api.bootstrap();
        messages = await _api.history();
      }
    } on OnixApiException catch (exception) {
      if (!exception.unauthorized) error = exception.message;
      await _api.clearSession();
      bootstrap = null;
    } finally {
      initializing = false;
      notifyListeners();
    }
  }

  Future<bool> login({
    required String company,
    required String user,
    required String password,
  }) async {
    authenticating = true;
    error = null;
    notifyListeners();
    try {
      final result = await _api.login(
        empresa: company.trim(),
        usuario: user.trim(),
        password: password,
      );
      bootstrap = result.bootstrap;
      messages = await _api.history();
      return true;
    } on OnixApiException catch (exception) {
      error = exception.message;
      return false;
    } finally {
      authenticating = false;
      notifyListeners();
    }
  }

  Future<void> logout() async {
    try {
      await _api.logout();
    } on OnixApiException {
      await _api.clearSession();
    }
    bootstrap = null;
    messages = [];
    error = null;
    notifyListeners();
  }

  Future<bool> send(String text) async {
    final question = text.trim();
    if (question.isEmpty || sending) return false;
    sending = true;
    error = null;
    messages = [
      ...messages,
      OnixMessage(
        id: 'local-${DateTime.now().microsecondsSinceEpoch}',
        role: 'usuario',
        content: question,
        createdAt: DateTime.now(),
        pending: true,
      ),
    ];
    notifyListeners();
    try {
      final answer = await _api.send(question);
      messages = [...messages, answer];
      return true;
    } on OnixApiException catch (exception) {
      error = exception.message;
      if (exception.unauthorized) {
        bootstrap = null;
        messages = [];
      }
      return false;
    } finally {
      sending = false;
      notifyListeners();
    }
  }

  Future<bool> decide(OnixAction action, String decision) async {
    error = null;
    notifyListeners();
    try {
      final updated = await _api.decideAction(action.id, decision);
      messages = messages.map((message) => message.replaceAction(updated)).toList();
      notifyListeners();
      return true;
    } on OnixApiException catch (exception) {
      error = exception.message;
      if (exception.unauthorized) {
        bootstrap = null;
        messages = [];
      }
      notifyListeners();
      return false;
    }
  }

  void clearError() {
    error = null;
    notifyListeners();
  }

  @override
  void dispose() {
    _api.dispose();
    super.dispose();
  }
}

