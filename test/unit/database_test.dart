import 'package:flutter_test/flutter_test.dart';
import 'package:gym_management_system/database/app_database.dart';

void main() {
  late AppDatabase db;
  setUp(() => db = AppDatabase.inMemory());
  tearDown(() => db.close());

  GymsCompanion gym(String id) => GymsCompanion.insert(
    id: id,
    name: 'G',
    createdAt: DateTime.utc(2026),
    updatedAt: DateTime.utc(2026),
  );

  test('foreign keys are enforced', () async {
    await expectLater(
      db
          .into(db.gymOwners)
          .insert(
            GymOwnersCompanion.insert(
              id: 'o1',
              gymId: 'missing-gym',
              displayName: 'x',
              email: 'a@b.co',
              passwordHash: 'h',
              recoveryHash: 'h',
              createdAt: DateTime.utc(2026),
              updatedAt: DateTime.utc(2026),
            ),
          ),
      throwsA(anything),
    );
  });

  test('owner email is unique', () async {
    await db.into(db.gyms).insert(gym('g1'));
    GymOwnersCompanion owner(String id) => GymOwnersCompanion.insert(
      id: id,
      gymId: 'g1',
      displayName: 'x',
      email: 'a@b.co',
      passwordHash: 'h',
      recoveryHash: 'h',
      createdAt: DateTime.utc(2026),
      updatedAt: DateTime.utc(2026),
    );
    await db.into(db.gymOwners).insert(owner('o1'));
    await expectLater(
      db.into(db.gymOwners).insert(owner('o2')),
      throwsA(anything),
    );
  });

  test('outbox idempotency key is unique (no duplicate processing)', () async {
    SyncOperationsCompanion op(String id) => SyncOperationsCompanion.insert(
      id: id,
      gymId: 'g1',
      entityTable: 'gyms',
      entityId: 'g1',
      operation: 'insert',
      payloadJson: '{}',
      idempotencyKey: 'same-key',
      createdAt: DateTime.utc(2026),
    );
    await db.into(db.syncOperations).insert(op('a'));
    await expectLater(
      db.into(db.syncOperations).insert(op('b')),
      throwsA(anything),
    );
  });

  test('defaults: Asia/Kolkata, INR, version 1', () async {
    await db.into(db.gyms).insert(gym('g1'));
    final g = await db.select(db.gyms).getSingle();
    expect(g.timezone, 'Asia/Kolkata');
    expect(g.currency, 'INR');
    expect(g.version, 1);
    expect(g.deletedAt, isNull);
  });

  test('schema version is 3 and indexes exist', () async {
    expect(db.schemaVersion, 3);
    final idx = await db
        .customSelect("SELECT name FROM sqlite_master WHERE type='index'")
        .get();
    final names = idx.map((r) => r.read<String>('name'));
    expect(
      names,
      containsAll(['idx_sync_ops_status', 'idx_audit_gym_created']),
    );
  });
}
