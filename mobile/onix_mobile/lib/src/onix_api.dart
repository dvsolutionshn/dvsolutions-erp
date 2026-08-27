import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;

import 'models.dart';

class OnixApiException implements Exception {
  const OnixApiException(this.message, {this.statusCode, this.code});

  final String message;
  final int? statusCode;
  final String? code;

  bool get unauthorized => statusCode == 401;

  @override
  String toString() => message;
}

class OnixApi {
  OnixApi({http.Client? client}) : _client = client ?? http.Client();

  static const _tokenKey = 'onix_mobile_token';
  static const _configuredUrl = String.fromEnvironment(
    'ONIX_API_URL',
    defaultValue: 'https://dvsolutionshn.com',
  );

  final http.Client _client;
  final FlutterSecureStorage _storage = const FlutterSecureStorage(
    aOptions: AndroidOptions(migrateWithBackup: true),
  );

  String get _baseUrl => _configuredUrl.endsWith('/')
      ? '${_configuredUrl}api/onix/mobile/v1/'
      : '$_configuredUrl/api/onix/mobile/v1/';

  Uri _uri(String path) => Uri.parse('$_baseUrl$path');

  Future<String?> readToken() => _storage.read(key: _tokenKey);

  Future<OnixLoginResult> login({
    required String empresa,
    required String usuario,
    required String password,
  }) async {
    final payload = await _request(
      'login/',
      method: 'POST',
      body: {
        'empresa': empresa,
        'usuario': usuario,
        'password': password,
        'dispositivo': 'Onix Mobile',
      },
      authenticated: false,
    );
    final token = payload['token']?.toString() ?? '';
    if (token.isEmpty) {
      throw const OnixApiException('El servidor no entrego una sesion valida.');
    }
    await _storage.write(key: _tokenKey, value: token);
    return OnixLoginResult(
      token: token,
      bootstrap: OnixBootstrap.fromJson(
        Map<String, dynamic>.from(payload['bootstrap'] as Map? ?? const {}),
      ),
    );
  }

  Future<OnixBootstrap> bootstrap() async {
    final payload = await _request('bootstrap/');
    return OnixBootstrap.fromJson(
      Map<String, dynamic>.from(payload['bootstrap'] as Map? ?? const {}),
    );
  }

  Future<List<OnixMessage>> history() async {
    final payload = await _request('history/?limit=60');
    return (payload['messages'] as List? ?? const [])
        .whereType<Map>()
        .map((item) => OnixMessage.fromJson(Map<String, dynamic>.from(item)))
        .toList();
  }

  Future<OnixMessage> send(String question) async {
    final payload = await _request(
      'chat/',
      method: 'POST',
      body: {'pregunta': question},
    );
    return OnixMessage.fromJson(
      Map<String, dynamic>.from(payload['message'] as Map? ?? const {}),
    );
  }

  Future<OnixAction> decideAction(String actionId, String decision) async {
    final payload = await _request(
      'actions/$actionId/',
      method: 'POST',
      body: {'decision': decision},
    );
    return OnixAction.fromJson(
      Map<String, dynamic>.from(payload['action'] as Map? ?? const {}),
    );
  }

  Future<void> logout() async {
    try {
      await _request('logout/', method: 'POST', body: const {});
    } finally {
      await clearSession();
    }
  }

  Future<void> clearSession() => _storage.delete(key: _tokenKey);

  Future<Map<String, dynamic>> _request(
    String path, {
    String method = 'GET',
    Map<String, dynamic>? body,
    bool authenticated = true,
  }) async {
    final headers = <String, String>{
      'Accept': 'application/json',
      'Content-Type': 'application/json; charset=UTF-8',
    };
    if (authenticated) {
      final token = await readToken();
      if (token == null || token.isEmpty) {
        throw const OnixApiException(
          'Inicia sesion para continuar.',
          statusCode: 401,
          code: 'authentication_required',
        );
      }
      headers['Authorization'] = 'Bearer $token';
    }

    late http.Response response;
    try {
      response = method == 'POST'
          ? await _client
                .post(
                  _uri(path),
                  headers: headers,
                  body: jsonEncode(body ?? const {}),
                )
                .timeout(const Duration(seconds: 60))
          : await _client
                .get(_uri(path), headers: headers)
                .timeout(const Duration(seconds: 30));
    } on Exception {
      throw const OnixApiException(
        'No pudimos conectar con Onix. Revisa tu conexion e intenta nuevamente.',
      );
    }

    Map<String, dynamic> payload;
    try {
      payload = Map<String, dynamic>.from(
        jsonDecode(utf8.decode(response.bodyBytes)) as Map,
      );
    } on Exception {
      throw OnixApiException(
        'El servidor devolvio una respuesta que la app no pudo interpretar.',
        statusCode: response.statusCode,
      );
    }
    if (response.statusCode < 200 ||
        response.statusCode >= 300 ||
        payload['ok'] == false) {
      final exception = OnixApiException(
        payload['error']?.toString() ?? 'Onix no pudo completar la solicitud.',
        statusCode: response.statusCode,
        code: payload['code']?.toString(),
      );
      if (exception.unauthorized) await clearSession();
      throw exception;
    }
    return payload;
  }

  void dispose() => _client.close();
}
