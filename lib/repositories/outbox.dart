import 'dart:convert';

import 'package:drift/drift.dart';

import '../core/clock.dart';
import '../database/app_database.dart';
import 'ids.dart';

/// Writes outbox and audit rows. Always call inside the same transaction as
/// the business write so local state, outbox and audit trail commit together.
class OutboxWriter {
  OutboxWriter(this._db, this._clock);

  final AppDatabase _db;
  final Clock _clock;

  Future<void> enqueue({
    required String gymId,
    required String entityTable,
    required String entityId,
    required String operation,
    required Map<String, Object?> payload,
    int baseVersion = 0,
  }) {
    return _db
        .into(_db.syncOperations)
        .insert(
          SyncOperationsCompanion.insert(
            id: newId(),
            gymId: gymId,
            entityTable: entityTable,
            entityId: entityId,
            operation: operation,
            payloadJson: jsonEncode(payload),
            baseVersion: Value(baseVersion),
            idempotencyKey: newId(),
            createdAt: _clock.now(),
          ),
        );
  }

  Future<void> audit({
    required String gymId,
    required String action,
    String? actorId,
    String? entityTable,
    String? entityId,
    String? reason,
    Map<String, Object?> details = const {},
  }) {
    return _db
        .into(_db.auditLogs)
        .insert(
          AuditLogsCompanion.insert(
            id: newId(),
            gymId: gymId,
            actorId: Value(actorId),
            action: action,
            entityTable: Value(entityTable),
            entityId: Value(entityId),
            reason: Value(reason),
            detailsJson: Value(jsonEncode(details)),
            createdAt: _clock.now(),
          ),
        );
  }
}
