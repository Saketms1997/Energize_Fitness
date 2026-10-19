import 'dart:async';
import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;

import '../core/clock.dart';

/// Where the cloud session tokens live. Production uses the OS credential
/// store (Windows Credential Locker, Android Keystore, iOS Keychain).
abstract interface class SecretStore {
  Future<String?> read(String key);
  Future<void> write(String key, String value);
  Future<void> delete(String key);
}

class SecureSecretStore implements SecretStore {
  SecureSecretStore([FlutterSecureStorage? s])
    : _s = s ?? const FlutterSecureStorage();
  final FlutterSecureStorage _s;
  @override
  Future<String?> read(String key) => _s.read(key: key);
  @override
  Future<void> write(String key, String value) =>
      _s.write(key: key, value: value);
  @override
  Future<void> delete(String key) => _s.delete(key: key);
}

class MemorySecretStore implements SecretStore {
  final Map<String, String> data = {};
  @override
  Future<String?> read(String key) async => data[key];
  @override
  Future<void> write(String key, String value) async => data[key] = value;
  @override
  Future<void> delete(String key) async => data.remove(key);
}

class CloudAuthException implements Exception {
  CloudAuthException(this.message, {this.offline = false});
  final String message;

  /// The service could not be reached (as opposed to rejecting the request).
  final bool offline;
  @override
  String toString() => message;
}

/// Minimal Supabase Auth client (email + password) using the REST API.
class CloudAuth {
  CloudAuth({
    required this.client,
    required this.projectUrl,
    required this.anonKey,
    required this.store,
    required this.clock,
  });

  final http.Client client;
  final String projectUrl;
  final String anonKey;
  final SecretStore store;
  final Clock clock;

  static const _kAccess = 'cloud.accessToken';
  static const _kRefresh = 'cloud.refreshToken';
  static const _kExpiry = 'cloud.expiresAtMs';
  static const _kEmail = 'cloud.email';

  Future<String?> get email => store.read(_kEmail);
  Future<bool> get isSignedIn async => await store.read(_kRefresh) != null;

  Future<Map<String, dynamic>> _post(
    String path,
    Map<String, dynamic> body,
  ) async {
    http.Response res;
    try {
      res = await client
          .post(
            Uri.parse('$projectUrl$path'),
            headers: {'apikey': anonKey, 'Content-Type': 'application/json'},
            body: jsonEncode(body),
          )
          .timeout(const Duration(seconds: 20));
    } on TimeoutException {
      throw CloudAuthException(
        'The cloud service did not respond.',
        offline: true,
      );
    } catch (_) {
      throw CloudAuthException(
        'Cannot reach the cloud service. Check the internet.',
        offline: true,
      );
    }
    Map<String, dynamic> json = const {};
    try {
      json = jsonDecode(res.body) as Map<String, dynamic>;
    } catch (_) {}
    if (res.statusCode < 200 || res.statusCode >= 300) {
      final msg =
          (json['msg'] ?? json['error_description'] ?? json['message'])
              as String?;
      throw CloudAuthException(
        res.statusCode == 400 && msg != null && msg.contains('Invalid login')
            ? 'Wrong email or password.'
            : (msg ?? 'Cloud sign-in failed (${res.statusCode}).'),
      );
    }
    return json;
  }

  Future<void> _saveSession(Map<String, dynamic> j, String? email) async {
    final access = j['access_token'] as String?;
    final refresh = j['refresh_token'] as String?;
    if (access == null || refresh == null) {
      throw CloudAuthException(
        'Account created. Confirm the email we sent, then sign in.',
      );
    }
    final ttl = (j['expires_in'] as num?)?.toInt() ?? 3600;
    await store.write(_kAccess, access);
    await store.write(_kRefresh, refresh);
    await store.write(
      _kExpiry,
      '${clock.now().add(Duration(seconds: ttl)).millisecondsSinceEpoch}',
    );
    if (email != null) await store.write(_kEmail, email);
  }

  Future<void> signIn(String email, String password) async {
    final j = await _post('/auth/v1/token?grant_type=password', {
      'email': email.trim(),
      'password': password,
    });
    await _saveSession(j, email.trim());
  }

  Future<void> signUp(String email, String password) async {
    final j = await _post('/auth/v1/signup', {
      'email': email.trim(),
      'password': password,
    });
    await _saveSession(j, email.trim());
  }

  /// A valid access token, refreshing it when it expires within a minute.
  /// Returns null when signed out or the refresh token was rejected.
  Future<String?> accessToken() async {
    final access = await store.read(_kAccess);
    final refresh = await store.read(_kRefresh);
    if (refresh == null) return null;
    final expiry = int.tryParse(await store.read(_kExpiry) ?? '') ?? 0;
    final soon = clock
        .now()
        .add(const Duration(seconds: 60))
        .millisecondsSinceEpoch;
    if (access != null && expiry > soon) return access;
    try {
      final j = await _post('/auth/v1/token?grant_type=refresh_token', {
        'refresh_token': refresh,
      });
      await _saveSession(j, null);
      return j['access_token'] as String?;
    } on CloudAuthException catch (e) {
      // Offline: keep using the old token (the server decides if it is still
      // good). Rejected refresh token: the user has to sign in again.
      return e.offline ? access : null;
    }
  }

  Future<void> signOut() async {
    for (final k in [_kAccess, _kRefresh, _kExpiry, _kEmail]) {
      await store.delete(k);
    }
  }
}
