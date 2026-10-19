import 'dart:convert';

import 'package:drift/drift.dart';

import '../core/clock.dart';
import '../database/app_database.dart';
import '../repositories/outbox.dart';
import 'record_applier.dart';
import 'remote_gateway.dart';

class SyncReport {
  SyncReport({
    this.pushed = 0,
    this.conflicts = 0,
    this.pulled = 0,
    this.applyFailures = 0,
    this.error,
  });

  int pushed;

  /// Operations the server refused because it held a newer/different version.
  /// The server's version won and the local one was replaced.
  int conflicts;
  int pulled;

  /// Server records that could not be stored locally (kept in the audit log).
  int applyFailures;
  String? error;

  bool get ok => error == null;

  @override
  String toString() =>
      'SyncReport(pushed: $pushed, conflicts: $conflicts, pulled: $pulled, '
      'applyFailures: $applyFailures, error: $error)';
}

/// Two-way sync with the cloud. The device keeps working offline; this pushes
/// the outbox and pulls other devices' changes. Conflict policy: the server's
/// version wins, and whatever the local side lost is written to the audit log.
class SyncService {
  SyncService(this._db, this._clock, this._gateway)
    : _outbox = OutboxWriter(_db, _clock),
      _applier = RecordApplier(_db);

  static const batchSize = 100;
  static const pageSize = 500;
  static const lastSeqKey = 'sync.lastSeq';
  static const lastSuccessKey = 'sync.lastSuccessAt';

  final AppDatabase _db;
  final Clock _clock;
  final RemoteGateway _gateway;
  final OutboxWriter _outbox;
  final RecordApplier _applier;
  bool _running = false;

  Future<int> pendingCount(String gymId) async {
    final n = _db.syncOperations.id.count();
    final row =
        await (_db.selectOnly(_db.syncOperations)
              ..addColumns([n])
              ..where(
                _db.syncOperations.gymId.equals(gymId) &
                    _db.syncOperations.status.isIn(['pending', 'in_flight']),
              ))
            .getSingle();
    return row.read(n) ?? 0;
  }

  Future<DateTime?> lastSuccess(String gymId) async {
    final v = await _setting(gymId, lastSuccessKey);
    final ms = int.tryParse(v ?? '');
    return ms == null ? null : DateTime.fromMillisecondsSinceEpoch(ms);
  }

  Future<String?> _setting(String gymId, String key) async {
    final row =
        await (_db.select(_db.appSettings)
              ..where((s) => s.gymId.equals(gymId) & s.key.equals(key)))
            .getSingleOrNull();
    return row?.value;
  }

  Future<void> _put(String gymId, String key, String value) => _db
      .into(_db.appSettings)
      .insertOnConflictUpdate(
        AppSettingsCompanion.insert(
          gymId: gymId,
          key: key,
          value: value,
          updatedAt: _clock.now(),
        ),
      );

  /// Runs one push + pull cycle. Never throws for network problems; they are
  /// reported in [SyncReport.error] and the work is retried next time.
  Future<SyncReport> sync(String gymId) async {
    final report = SyncReport();
    if (_running) {
      report.error = 'A sync is already running';
      return report;
    }
    _running = true;
    try {
      await _gateway.claimGym(gymId);
      // A crash mid-push can leave rows in_flight; they are safe to resend
      // because the server de-duplicates by idempotency key.
      await (_db.update(
            _db.syncOperations,
          )..where((o) => o.gymId.equals(gymId) & o.status.equals('in_flight')))
          .write(const SyncOperationsCompanion(status: Value('pending')));

      await _push(gymId, report);
      if (report.ok) await _pull(gymId, report);
      if (report.ok) {
        await _put(
          gymId,
          lastSuccessKey,
          '${_clock.now().millisecondsSinceEpoch}',
        );
      }
    } on SyncTransportException catch (e) {
      report.error = e.message;
    } finally {
      _running = false;
    }
    return report;
  }

  // ------------------------------------------------------------------ push

  Future<void> _push(String gymId, SyncReport report) async {
    final conflictedEntities = <String, (String, String)>{};
    while (true) {
      final batch =
          await (_db.select(_db.syncOperations)
                ..where(
                  (o) => o.gymId.equals(gymId) & o.status.equals('pending'),
                )
                // Insertion order (rowid), not createdAt: timestamps have
                // one-second resolution, so several ops can share one and a
                // v2 edit must never overtake its v1 insert.
                ..orderBy([
                  (o) => OrderingTerm.asc(const CustomExpression<int>('rowid')),
                ])
                ..limit(batchSize))
              .get();
      if (batch.isEmpty) break;

      final sendable = <SyncOperation>[];
      for (final op in batch) {
        if (RecordApplier.syncable.contains(op.entityTable)) {
          sendable.add(op);
        } else {
          await _finish(op.id, 'failed', error: 'Table is not synchronised');
        }
      }
      if (sendable.isEmpty) continue;

      final ok = await _sendBatch(sendable, report, conflictedEntities);
      if (!ok) break;
    }
    for (final (table, id) in conflictedEntities.values) {
      await _resolveConflict(gymId, table, id, report);
    }
  }

  /// Returns false when the push must stop (transport problem).
  Future<bool> _sendBatch(
    List<SyncOperation> ops,
    SyncReport report,
    Map<String, (String, String)> conflicted,
  ) async {
    await (_db.update(_db.syncOperations)
          ..where((o) => o.id.isIn(ops.map((o) => o.id))))
        .write(const SyncOperationsCompanion(status: Value('in_flight')));

    try {
      final results = await _gateway.push(ops.first.gymId, [
        for (final o in ops)
          SyncOpDto(
            idempotencyKey: o.idempotencyKey,
            entityTable: o.entityTable,
            entityId: o.entityId,
            operation: o.operation,
            payload: jsonDecode(o.payloadJson) as Map<String, dynamic>,
          ),
      ]);
      final byKey = {for (final r in results) r.idempotencyKey: r.outcome};
      var missing = false;
      for (final o in ops) {
        switch (byKey[o.idempotencyKey]) {
          case OpOutcome.applied:
            await _finish(o.id, 'synced');
            report.pushed++;
          case OpOutcome.conflict:
            await _finish(o.id, 'conflict');
            conflicted['${o.entityTable}/${o.entityId}'] = (
              o.entityTable,
              o.entityId,
            );
          case null:
            missing = true;
            await _finish(o.id, 'pending', error: 'No result from server');
        }
      }
      if (missing) {
        report.error = 'Server did not answer every operation';
        return false;
      }
      return true;
    } on SyncTransportException catch (e) {
      final authProblem = e.status == 401 || e.status == 403;
      if (!e.retryable && !authProblem && ops.length > 1) {
        // One bad operation must not block the rest: retry one by one.
        for (final o in ops) {
          await (_db.update(_db.syncOperations)
                ..where((x) => x.id.equals(o.id)))
              .write(const SyncOperationsCompanion(status: Value('pending')));
        }
        for (final o in ops) {
          if (!await _sendBatch([o], report, conflicted)) return false;
        }
        return true;
      }
      if (!e.retryable && !authProblem && ops.length == 1) {
        await _finish(ops.single.id, 'failed', error: e.message, bump: true);
        return true; // permanent rejection of this one operation
      }
      for (final o in ops) {
        await _finish(o.id, 'pending', error: e.message, bump: true);
      }
      report.error = e.message;
      return false;
    }
  }

  Future<void> _finish(
    String id,
    String status, {
    String? error,
    bool bump = false,
  }) async {
    final cur = await (_db.select(
      _db.syncOperations,
    )..where((o) => o.id.equals(id))).getSingle();
    await (_db.update(_db.syncOperations)..where((o) => o.id.equals(id))).write(
      SyncOperationsCompanion(
        status: Value(status),
        lastError: Value(error),
        attemptCount: Value(cur.attemptCount + (bump ? 1 : 0)),
        syncedAt: status == 'synced'
            ? Value(_clock.now())
            : const Value.absent(),
      ),
    );
  }

  Future<void> _resolveConflict(
    String gymId,
    String table,
    String id,
    SyncReport report,
  ) async {
    final remote = await _gateway.fetch(gymId, table, id);
    if (remote == null) return;
    report.conflicts++;
    final lost =
        await (_db.select(_db.syncOperations)
              ..where(
                (o) =>
                    o.entityTable.equals(table) &
                    o.entityId.equals(id) &
                    o.status.isIn(['conflict', 'pending']),
              )
              ..orderBy([
                (o) => OrderingTerm.asc(const CustomExpression<int>('rowid')),
              ]))
            .get();
    await _db.transaction(() async {
      await _db.customStatement('PRAGMA defer_foreign_keys = ON');
      await _applier.upsert(table, remote.payload);
      // Queued edits on top of a rejected one can no longer apply.
      await (_db.update(_db.syncOperations)..where(
            (o) =>
                o.entityTable.equals(table) &
                o.entityId.equals(id) &
                o.status.equals('pending'),
          ))
          .write(
            const SyncOperationsCompanion(
              status: Value('conflict'),
              lastError: Value('Superseded by server version'),
            ),
          );
      await _outbox.audit(
        gymId: gymId,
        action: 'sync.conflict_server_wins',
        entityTable: table,
        entityId: id,
        details: {
          'serverVersion': remote.version,
          'discardedLocal': [for (final o in lost) jsonDecode(o.payloadJson)],
        },
      );
    });
  }

  // ------------------------------------------------------------------ pull

  Future<void> _pull(String gymId, SyncReport report) async {
    var last = int.tryParse(await _setting(gymId, lastSeqKey) ?? '') ?? 0;
    while (true) {
      final page = await _gateway.pull(gymId, last, limit: pageSize);
      if (page.isEmpty) break;
      try {
        await _db.transaction(() async {
          await _db.customStatement('PRAGMA defer_foreign_keys = ON');
          for (final r in page) {
            report.pulled += await _applyOne(gymId, r) ? 1 : 0;
          }
        });
      } catch (_) {
        // Fall back to record-by-record so one bad row cannot block the page.
        for (final r in page) {
          try {
            await _db.transaction(() async {
              await _db.customStatement('PRAGMA defer_foreign_keys = ON');
              await _applyOne(gymId, r);
            });
          } catch (e) {
            report.applyFailures++;
            await _outbox.audit(
              gymId: gymId,
              action: 'sync.apply_failed',
              entityTable: r.entityTable,
              entityId: r.entityId,
              reason: e.runtimeType.toString(),
              details: {'record': r.payload, 'version': r.version},
            );
          }
        }
      }
      last = page.last.serverSeq;
      await _put(gymId, lastSeqKey, '$last');
      if (page.length < pageSize) break;
    }
  }

  /// Returns true when the local copy changed.
  Future<bool> _applyOne(String gymId, RemoteRecord r) async {
    if (!RecordApplier.syncable.contains(r.entityTable)) return false;
    final local = await _applier.localVersion(r.entityTable, r.entityId);
    if (local != null && r.version <= local) return false;
    if (local != null) {
      // Remote moved on while we hold unsynced edits: they lose (server wins).
      final pending =
          await (_db.select(_db.syncOperations)..where(
                (o) =>
                    o.entityTable.equals(r.entityTable) &
                    o.entityId.equals(r.entityId) &
                    o.status.equals('pending'),
              ))
              .get();
      if (pending.isNotEmpty) {
        await (_db.update(
          _db.syncOperations,
        )..where((o) => o.id.isIn(pending.map((p) => p.id)))).write(
          const SyncOperationsCompanion(
            status: Value('conflict'),
            lastError: Value('Superseded by server version'),
          ),
        );
        await _outbox.audit(
          gymId: gymId,
          action: 'sync.conflict_server_wins',
          entityTable: r.entityTable,
          entityId: r.entityId,
          details: {
            'serverVersion': r.version,
            'discardedLocal': [
              for (final o in pending) jsonDecode(o.payloadJson),
            ],
          },
        );
      }
    }
    await _applier.upsert(r.entityTable, r.payload);
    return true;
  }
}
