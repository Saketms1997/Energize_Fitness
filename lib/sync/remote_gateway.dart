import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

/// One local outbox operation, as sent to the server.
class SyncOpDto {
  const SyncOpDto({
    required this.idempotencyKey,
    required this.entityTable,
    required this.entityId,
    required this.operation,
    required this.payload,
  });

  final String idempotencyKey;
  final String entityTable;
  final String entityId;
  final String operation;
  final Map<String, dynamic> payload;

  Map<String, dynamic> toJson() => {
    'idempotency_key': idempotencyKey,
    'entity_table': entityTable,
    'entity_id': entityId,
    'operation': operation,
    'payload': payload,
  };
}

enum OpOutcome { applied, conflict }

class OpResult {
  const OpResult(this.idempotencyKey, this.outcome);
  final String idempotencyKey;
  final OpOutcome outcome;
}

/// A record as stored on the server.
class RemoteRecord {
  const RemoteRecord({
    required this.entityTable,
    required this.entityId,
    required this.version,
    required this.deleted,
    required this.payload,
    required this.serverSeq,
  });

  factory RemoteRecord.fromJson(Map<String, dynamic> j) => RemoteRecord(
    entityTable: j['entity_table'] as String,
    entityId: j['entity_id'] as String,
    version: (j['version'] as num).toInt(),
    deleted: j['deleted'] as bool,
    payload: Map<String, dynamic>.from(j['payload'] as Map),
    serverSeq: (j['server_seq'] as num).toInt(),
  );

  final String entityTable;
  final String entityId;
  final int version;
  final bool deleted;
  final Map<String, dynamic> payload;
  final int serverSeq;
}

/// Network failure or server rejection. [retryable] failures leave operations
/// pending; permanent ones (auth/permission/validation) are surfaced.
class SyncTransportException implements Exception {
  SyncTransportException(this.message, {this.retryable = true, this.status});
  final String message;
  final bool retryable;
  final int? status;
  @override
  String toString() => 'SyncTransportException($status, $message)';
}

abstract interface class RemoteGateway {
  /// Registers this gym for the signed-in cloud user (idempotent).
  Future<void> claimGym(String gymId);

  Future<List<OpResult>> push(String gymId, List<SyncOpDto> ops);

  /// Records with server_seq > [afterSeq], oldest first.
  Future<List<RemoteRecord>> pull(
    String gymId,
    int afterSeq, {
    int limit = 500,
  });

  Future<RemoteRecord?> fetch(
    String gymId,
    String entityTable,
    String entityId,
  );
}

/// Supabase implementation over PostgREST (no SDK dependency).
class SupabaseGateway implements RemoteGateway {
  SupabaseGateway({
    required this.client,
    required this.projectUrl,
    required this.anonKey,
    required this.accessToken,
    this.timeout = const Duration(seconds: 30),
  });

  final http.Client client;
  final String projectUrl;
  final String anonKey;
  final Future<String?> Function() accessToken;
  final Duration timeout;

  Future<Map<String, String>> _headers() async {
    final token = await accessToken();
    if (token == null) {
      throw SyncTransportException('Not signed in to cloud', retryable: false);
    }
    return {
      'apikey': anonKey,
      'Authorization': 'Bearer $token',
      'Content-Type': 'application/json',
    };
  }

  Future<http.Response> _send(Future<http.Response> Function() call) async {
    try {
      final res = await call().timeout(timeout);
      if (res.statusCode >= 200 && res.statusCode < 300) return res;
      var message = 'HTTP ${res.statusCode}';
      try {
        final body = jsonDecode(res.body);
        if (body is Map && body['message'] is String) {
          message = body['message'] as String;
        }
      } catch (_) {}
      throw SyncTransportException(
        message,
        status: res.statusCode,
        retryable:
            res.statusCode >= 500 ||
            res.statusCode == 429 ||
            res.statusCode == 408,
      );
    } on SyncTransportException {
      rethrow;
    } on TimeoutException {
      throw SyncTransportException('Timed out');
    } catch (e) {
      throw SyncTransportException('Network error: ${e.runtimeType}');
    }
  }

  @override
  Future<void> claimGym(String gymId) async {
    final h = await _headers();
    await _send(
      () => client.post(
        Uri.parse('$projectUrl/rest/v1/rpc/claim_gym'),
        headers: h,
        body: jsonEncode({'p_gym': gymId}),
      ),
    );
  }

  @override
  Future<List<OpResult>> push(String gymId, List<SyncOpDto> ops) async {
    final h = await _headers();
    final res = await _send(
      () => client.post(
        Uri.parse('$projectUrl/rest/v1/rpc/apply_sync_ops'),
        headers: h,
        body: jsonEncode({
          'p_gym': gymId,
          'p_ops': ops.map((o) => o.toJson()).toList(),
        }),
      ),
    );
    final list = jsonDecode(res.body) as List;
    return [
      for (final r in list)
        OpResult(
          (r as Map)['idempotency_key'] as String,
          r['outcome'] == 'applied' ? OpOutcome.applied : OpOutcome.conflict,
        ),
    ];
  }

  @override
  Future<List<RemoteRecord>> pull(
    String gymId,
    int afterSeq, {
    int limit = 500,
  }) async {
    final h = await _headers();
    final uri = Uri.parse('$projectUrl/rest/v1/sync_records').replace(
      queryParameters: {
        'gym_id': 'eq.$gymId',
        'server_seq': 'gt.$afterSeq',
        'order': 'server_seq.asc',
        'limit': '$limit',
      },
    );
    final res = await _send(() => client.get(uri, headers: h));
    return [
      for (final j in jsonDecode(res.body) as List)
        RemoteRecord.fromJson(Map<String, dynamic>.from(j as Map)),
    ];
  }

  @override
  Future<RemoteRecord?> fetch(
    String gymId,
    String entityTable,
    String entityId,
  ) async {
    final h = await _headers();
    final uri = Uri.parse('$projectUrl/rest/v1/sync_records').replace(
      queryParameters: {
        'gym_id': 'eq.$gymId',
        'entity_table': 'eq.$entityTable',
        'entity_id': 'eq.$entityId',
      },
    );
    final res = await _send(() => client.get(uri, headers: h));
    final list = jsonDecode(res.body) as List;
    return list.isEmpty
        ? null
        : RemoteRecord.fromJson(Map<String, dynamic>.from(list.first as Map));
  }
}
