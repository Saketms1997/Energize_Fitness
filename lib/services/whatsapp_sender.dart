import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../database/app_database.dart';
import '../repositories/message_repository.dart';
import 'message_templates.dart';

class SendResult {
  const SendResult.ok(this.providerMessageId) : error = null, retryable = false;
  const SendResult.failed(this.error, {this.retryable = true})
    : providerMessageId = null;
  final String? providerMessageId;
  final String? error;
  final bool retryable;
  bool get isOk => error == null;
}

abstract interface class WhatsAppSender {
  Future<SendResult> send(WhatsappMessage message);
}

/// Sends through the `send-whatsapp` Supabase Edge Function. The WhatsApp
/// access token lives only on the server; the app sends its own session token.
class EdgeFunctionSender implements WhatsAppSender {
  EdgeFunctionSender({
    required this.client,
    required this.functionsBaseUrl,
    required this.accessToken,
    this.timeout = const Duration(seconds: 20),
  });

  final http.Client client;
  final String functionsBaseUrl;
  final Future<String?> Function() accessToken;
  final Duration timeout;

  @override
  Future<SendResult> send(WhatsappMessage m) async {
    final template = MessageTemplate.byKey(m.templateKey);
    if (template == null) {
      return SendResult.failed(
        'Unknown template ${m.templateKey}',
        retryable: false,
      );
    }
    final token = await accessToken();
    if (token == null) {
      return const SendResult.failed('Not signed in to cloud');
    }
    try {
      final res = await client
          .post(
            Uri.parse('$functionsBaseUrl/send-whatsapp'),
            headers: {
              'Authorization': 'Bearer $token',
              'Content-Type': 'application/json',
            },
            body: jsonEncode({
              'gymId': m.gymId,
              'messageId': m.id,
              'to': m.toPhone,
              'template': template.cloudName,
              'params': jsonDecode(m.paramsJson),
            }),
          )
          .timeout(timeout);
      Map<String, dynamic> body;
      try {
        body = jsonDecode(res.body) as Map<String, dynamic>;
      } catch (_) {
        body = const {};
      }
      if (res.statusCode == 200 && body['providerMessageId'] is String) {
        return SendResult.ok(body['providerMessageId'] as String);
      }
      final msg = (body['error'] as String?) ?? 'HTTP ${res.statusCode}';
      // Client errors (other than rate limits/timeouts) will not fix themselves.
      final retry =
          res.statusCode >= 500 ||
          res.statusCode == 429 ||
          res.statusCode == 408;
      return SendResult.failed(msg, retryable: retry);
    } on TimeoutException {
      return const SendResult.failed('Timed out');
    } catch (e) {
      return SendResult.failed('Network error: ${e.runtimeType}');
    }
  }
}

/// Drains the queue through a [WhatsAppSender]. One run at a time.
class WhatsAppDispatcher {
  WhatsAppDispatcher(this._messages, this._sender);

  final MessageRepository _messages;
  final WhatsAppSender _sender;
  bool _running = false;

  /// Returns how many messages were sent.
  Future<int> processQueue(String gymId) async {
    if (_running) return 0;
    _running = true;
    var sent = 0;
    try {
      for (final m in await _messages.queued(gymId)) {
        final r = await _sender.send(m);
        if (r.isOk) {
          await _messages.markSent(
            m.id,
            providerMessageId: r.providerMessageId,
          );
          sent++;
        } else {
          await _messages.recordFailure(
            m.id,
            r.error ?? 'Unknown error',
            retryable: r.retryable,
          );
        }
      }
    } finally {
      _running = false;
    }
    return sent;
  }
}
