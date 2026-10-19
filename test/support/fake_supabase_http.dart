// ignore_for_file: curly_braces_in_flow_control_structures
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'fake_gateway.dart';

import 'package:gym_management_system/sync/remote_gateway.dart';

/// Speaks the subset of Supabase's HTTP API the app uses (GoTrue password and
/// refresh grants, PostgREST RPC + select) on top of [FakeServer], so the real
/// SupabaseGateway/CloudAuth request building is exercised.
class FakeSupabase {
  FakeSupabase(
    this.server, {
    this.email = 'owner@example.com',
    this.password = 'cloud-pass-123',
  });

  final FakeServer server;
  final String email;
  final String password;
  static const anonKey = 'anon-key';

  int _n = 0;
  final validTokens = <String>{};
  final validRefresh = <String>{};
  final requests = <http.Request>[];
  String? claimedBy;

  late final MockClient client = MockClient(_handle);

  Map<String, dynamic> _session() {
    final a = 'access-${++_n}', r = 'refresh-$_n';
    validTokens.add(a);
    validRefresh.add(r);
    return {'access_token': a, 'refresh_token': r, 'expires_in': 3600};
  }

  http.Response _json(Object body, [int status = 200]) => http.Response(
    jsonEncode(body),
    status,
    headers: {'content-type': 'application/json'},
  );

  Future<http.Response> _handle(http.Request req) async {
    requests.add(req);
    if (server.offline) throw http.ClientException('offline');
    final path = req.url.path;
    if (req.headers['apikey'] != anonKey) {
      return _json({'message': 'No API key'}, 401);
    }
    if (path == '/auth/v1/token') {
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      switch (req.url.queryParameters['grant_type']) {
        case 'password':
          return body['email'] == email && body['password'] == password
              ? _json(_session())
              : _json({'msg': 'Invalid login credentials'}, 400);
        case 'refresh_token':
          return validRefresh.remove(body['refresh_token'])
              ? _json(_session())
              : _json({'msg': 'Invalid Refresh Token'}, 400);
      }
      return _json({'msg': 'bad grant'}, 400);
    }
    final token = (req.headers['Authorization'] ?? '').replaceFirst(
      'Bearer ',
      '',
    );
    if (!validTokens.contains(token))
      return _json({'message': 'JWT expired'}, 401);

    if (path == '/rest/v1/rpc/claim_gym') {
      if (claimedBy != null &&
          claimedBy != token.split('-').first + email &&
          claimedBy != email) {
        return _json({'message': 'gym already claimed'}, 403);
      }
      claimedBy = email;
      return http.Response('', 204);
    }
    if (path == '/rest/v1/rpc/apply_sync_ops') {
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      final ops = [
        for (final o in body['p_ops'] as List)
          SyncOpDto(
            idempotencyKey: o['idempotency_key'] as String,
            entityTable: o['entity_table'] as String,
            entityId: o['entity_id'] as String,
            operation: o['operation'] as String,
            payload: Map<String, dynamic>.from(o['payload'] as Map),
          ),
      ];
      return _json([
        for (final r in server.apply(ops))
          {'idempotency_key': r.idempotencyKey, 'outcome': r.outcome.name},
      ]);
    }
    if (path == '/rest/v1/sync_records') {
      final q = req.url.queryParameters;
      String? eq(String k) => q[k]?.replaceFirst('eq.', '');
      final after =
          int.tryParse((q['server_seq'] ?? 'gt.0').replaceFirst('gt.', '')) ??
          0;
      var rows = server.records.values.where((r) => r.serverSeq > after);
      if (eq('entity_table') != null)
        rows = rows.where((r) => r.entityTable == eq('entity_table'));
      if (eq('entity_id') != null)
        rows = rows.where((r) => r.entityId == eq('entity_id'));
      final list = rows.toList()
        ..sort((a, b) => a.serverSeq.compareTo(b.serverSeq));
      final limit = int.tryParse(q['limit'] ?? '') ?? list.length;
      return _json([
        for (final r in list.take(limit))
          {
            'gym_id': eq('gym_id'),
            'entity_table': r.entityTable,
            'entity_id': r.entityId,
            'version': r.version,
            'deleted': r.deleted,
            'payload': r.payload,
            'server_seq': r.serverSeq,
          },
      ]);
    }
    return _json({'message': 'not found: $path'}, 404);
  }
}
