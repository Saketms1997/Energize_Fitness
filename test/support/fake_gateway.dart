import 'dart:convert';

import 'package:gym_management_system/sync/remote_gateway.dart';

/// In-memory server that follows the same rules as
/// backend/migrations/0001_sync_core.sql (which is tested against real
/// PostgreSQL in backend/tests): per-record versions, idempotency keys,
/// conflict on stale/different writes, monotonically increasing server_seq.
class FakeServer {
  final Map<String, RemoteRecord> records = {};
  final Map<String, OpOutcome> applied = {};
  int _seq = 0;
  int pushCalls = 0;
  int opsSeen = 0;

  /// (table/id, version) of every operation, in arrival order.
  final arrivals = <(String, int)>[];
  bool offline = false;
  Object? failNextPush;

  String _k(String t, String id) => '$t/$id';

  List<OpResult> apply(List<SyncOpDto> ops) {
    final out = <OpResult>[];
    for (final op in ops) {
      opsSeen++;
      arrivals.add((
        _k(op.entityTable, op.entityId),
        (op.payload['version'] as num).toInt(),
      ));
      final prior = applied[op.idempotencyKey];
      if (prior != null) {
        out.add(OpResult(op.idempotencyKey, prior));
        continue;
      }
      final incoming = (op.payload['version'] as num).toInt();
      final cur = records[_k(op.entityTable, op.entityId)];
      OpOutcome res;
      if (cur == null || incoming > cur.version) {
        records[_k(op.entityTable, op.entityId)] = RemoteRecord(
          entityTable: op.entityTable,
          entityId: op.entityId,
          version: incoming,
          deleted: op.operation == 'delete',
          // round-trip through JSON like a jsonb column would
          payload: Map<String, dynamic>.from(
            jsonDecode(jsonEncode(op.payload)) as Map,
          ),
          serverSeq: ++_seq,
        );
        res = OpOutcome.applied;
      } else if (incoming == cur.version &&
          jsonEncode(cur.payload) == jsonEncode(op.payload)) {
        res = OpOutcome.applied;
      } else {
        res = OpOutcome.conflict;
      }
      applied[op.idempotencyKey] = res;
      out.add(OpResult(op.idempotencyKey, res));
    }
    return out;
  }
}

class FakeGateway implements RemoteGateway {
  FakeGateway(this.server);
  final FakeServer server;
  final claimed = <String>[];

  void _check() {
    if (server.offline) throw SyncTransportException('Network error: offline');
  }

  @override
  Future<void> claimGym(String gymId) async {
    _check();
    claimed.add(gymId);
  }

  @override
  Future<List<OpResult>> push(String gymId, List<SyncOpDto> ops) async {
    _check();
    server.pushCalls++;
    final f = server.failNextPush;
    if (f != null) {
      server.failNextPush = null;
      throw f;
    }
    return server.apply(ops);
  }

  @override
  Future<List<RemoteRecord>> pull(
    String gymId,
    int afterSeq, {
    int limit = 500,
  }) async {
    _check();
    final list =
        server.records.values
            .where((r) => r.payload['gymId'] == gymId || r.entityId == gymId)
            .where((r) => r.serverSeq > afterSeq)
            .toList()
          ..sort((a, b) => a.serverSeq.compareTo(b.serverSeq));
    return list.take(limit).toList();
  }

  @override
  Future<RemoteRecord?> fetch(
    String gymId,
    String entityTable,
    String entityId,
  ) async {
    _check();
    return server.records['$entityTable/$entityId'];
  }
}
