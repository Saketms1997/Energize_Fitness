import 'dart:io';

import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gym_management_system/database/app_database.dart';
import 'package:sqlite3/sqlite3.dart' as sql;

void main() {
  late Directory dir;
  late File file;

  setUp(() {
    dir = Directory.systemTemp.createTempSync('gym_migration_');
    file = File('${dir.path}/gym.sqlite');
  });
  tearDown(() => dir.deleteSync(recursive: true));

  /// Builds a genuine schema-v1 database (captured from the shipped v1 code)
  /// holding real rows, as an installed app would have.
  void createV1WithData() {
    final raw = sql.sqlite3.open(file.path);
    raw.execute(File('test/fixtures/schema_v1.sql').readAsStringSync());
    raw.execute(
      "INSERT INTO gyms (id, version, created_at, updated_at, name, address, contact_number, timezone, currency) "
      "VALUES ('g1', 3, 1700000000, 1700000000, 'Iron Temple', '12 MG Road', '9876543210', 'Asia/Kolkata', 'INR')",
    );
    raw.execute(
      "INSERT INTO gym_owners (id, version, created_at, updated_at, gym_id, display_name, email, password_hash, recovery_hash) "
      "VALUES ('o1', 1, 1700000000, 1700000000, 'g1', 'Asha', 'a@b.co', 'hash', 'rhash')",
    );
    raw.execute(
      "INSERT INTO sync_operations (id, gym_id, entity_table, entity_id, operation, payload_json, idempotency_key, created_at) "
      "VALUES ('s1', 'g1', 'gyms', 'g1', 'insert', '{}', 'key-1', 1700000000)",
    );
    raw.execute('PRAGMA user_version = 1');
    raw.close();
  }

  test('v1 -> v2 upgrade keeps all data and adds the new tables', () async {
    createV1WithData();
    final db = AppDatabase(NativeDatabase(file));

    final gym = await db.select(db.gyms).getSingle();
    expect(gym.name, 'Iron Temple');
    expect(gym.version, 3);
    expect((await db.select(db.gymOwners).getSingle()).email, 'a@b.co');
    final op = await db.select(db.syncOperations).getSingle();
    expect(op.status, 'pending', reason: 'unsynced outbox rows must survive');

    for (final t in [
      'membership_plans',
      'members',
      'memberships',
      'payments',
      'payment_events',
      'membership_freezes',
    ]) {
      final r = await db
          .customSelect(
            "SELECT count(*) c FROM sqlite_master WHERE name = '$t'",
          )
          .getSingle();
      expect(r.read<int>('c'), 1, reason: 'missing table $t');
    }
    final idx =
        (await db
                .customSelect(
                  "SELECT name FROM sqlite_master WHERE type='index'",
                )
                .get())
            .map((r) => r.read<String>('name'));
    expect(
      idx,
      containsAll([
        'idx_members_gym_phone',
        'idx_memberships_status_end',
        'idx_payments_status_verified',
        'idx_sync_ops_status', // v1 index preserved
      ]),
    );
    final v = await db.customSelect('PRAGMA user_version').getSingle();
    expect(v.read<int>('user_version'), AppDatabase.currentSchemaVersion);
    await db.close();
  });

  test('upgraded database is fully usable', () async {
    createV1WithData();
    final db = AppDatabase(NativeDatabase(file));
    await db.customStatement(
      "INSERT INTO membership_plans (id, version, created_at, updated_at, gym_id, name, duration_unit, duration_value, price_paise, is_active) "
      "VALUES ('p1', 1, 1, 1, 'g1', 'Monthly', 'months', 1, 100000, 1)",
    );
    expect(await db.select(db.membershipPlans).get(), hasLength(1));
    await db.close();
  });

  test('re-opening an already-upgraded database is a no-op', () async {
    createV1WithData();
    final first = AppDatabase(NativeDatabase(file));
    expect(await first.select(first.gyms).get(), hasLength(1)); // migrates
    await first.close();
    final second = AppDatabase(NativeDatabase(file));
    expect(await second.select(second.gyms).get(), hasLength(1));
    expect(await second.select(second.syncOperations).get(), hasLength(1));
    await second.close();
  });

  test('SQL CHECK constraints reject invalid rows', () async {
    final db = AppDatabase.inMemory();
    Future<void> insertPlan(
      String unit,
      int value,
      int price,
    ) => db.customStatement(
      "INSERT INTO membership_plans (id, version, created_at, updated_at, gym_id, name, duration_unit, duration_value, price_paise, is_active) "
      "VALUES (lower(hex(randomblob(8))), 1, 1, 1, 'g1', hex(randomblob(4)), '$unit', $value, $price, 1)",
    );
    await db.customStatement('PRAGMA foreign_keys = OFF');
    await insertPlan('months', 1, 0); // valid
    await expectLater(insertPlan('weeks', 1, 100), throwsA(anything));
    await expectLater(insertPlan('months', 0, 100), throwsA(anything));
    await expectLater(insertPlan('months', 1, -1), throwsA(anything));
    await db.close();
  });

  test(
    'v2 -> v3 upgrade keeps members and adds messaging/attendance',
    () async {
      // Start from a current database, then strip it back to the v2 shape.
      var db = AppDatabase(NativeDatabase(file));
      await db.customStatement(
        "INSERT INTO gyms (id, version, created_at, updated_at, name) "
        "VALUES ('g1', 1, 1700000000, 1700000000, 'Iron Temple')",
      );
      await db.customStatement(
        "INSERT INTO members (id, version, created_at, updated_at, gym_id, member_code, full_name, phone, registered_at) "
        "VALUES ('m1', 1, 1700000000, 1700000000, 'g1', 'M0001', 'Ravi', '9876543210', 1700000000)",
      );
      await db.close();
      final raw = sql.sqlite3.open(file.path);
      for (final s in [
        'DROP TABLE whatsapp_messages',
        'DROP TABLE trainers',
        'DROP TABLE attendance_records',
        'DROP INDEX idx_members_trainer',
        'ALTER TABLE members DROP COLUMN trainer_id',
        'PRAGMA user_version = 2',
      ]) {
        raw.execute(s);
      }
      raw.close();

      db = AppDatabase(NativeDatabase(file));
      final m = await db.select(db.members).getSingle();
      expect(m.fullName, 'Ravi');
      expect(m.trainerId, isNull);
      for (final t in ['whatsapp_messages', 'trainers', 'attendance_records']) {
        final r = await db
            .customSelect(
              "SELECT count(*) c FROM sqlite_master WHERE name = '$t'",
            )
            .getSingle();
        expect(r.read<int>('c'), 1, reason: 'missing table $t');
      }
      final v = await db.customSelect('PRAGMA user_version').getSingle();
      expect(v.read<int>('user_version'), 3);
      await db.close();
    },
  );
}
