import 'dart:io';

import 'package:drift/drift.dart';
import 'package:drift/native.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';

import 'tables.dart';

part 'app_database.g.dart';

@DriftDatabase(
  tables: [
    Gyms,
    GymOwners,
    AppDevices,
    AppSettings,
    SyncOperations,
    AuditLogs,
    MembershipPlans,
    Members,
    Memberships,
    Payments,
    PaymentEvents,
    MembershipFreezes,
    WhatsappMessages,
    Trainers,
    AttendanceRecords,
  ],
)
class AppDatabase extends _$AppDatabase {
  AppDatabase(super.e);

  /// Location of the on-disk database: the per-user application-data
  /// directory (never the install directory, so upgrades preserve data).
  static Future<File> databaseFile() async {
    final dir = await getApplicationSupportDirectory();
    return File(p.join(dir.path, 'gym_management.sqlite'));
  }

  factory AppDatabase.persistent() => AppDatabase(
    LazyDatabase(() async {
      return NativeDatabase.createInBackground(await databaseFile());
    }),
  );

  factory AppDatabase.inMemory() => AppDatabase(NativeDatabase.memory());

  static const currentSchemaVersion = 3;

  Future<void> _createV2Indexes() async {
    for (final sql in const [
      'CREATE INDEX IF NOT EXISTS idx_members_gym_phone ON members (gym_id, phone)',
      'CREATE INDEX IF NOT EXISTS idx_members_gym_name ON members (gym_id, full_name)',
      'CREATE INDEX IF NOT EXISTS idx_memberships_member ON memberships (member_id, start_date)',
      'CREATE INDEX IF NOT EXISTS idx_memberships_status_end ON memberships (gym_id, status, end_date)',
      'CREATE INDEX IF NOT EXISTS idx_payments_membership ON payments (membership_id)',
      'CREATE INDEX IF NOT EXISTS idx_payments_status_verified ON payments (gym_id, status, verified_at)',
      'CREATE INDEX IF NOT EXISTS idx_payment_events_payment ON payment_events (payment_id, created_at)',
    ]) {
      await customStatement(sql);
    }
  }

  Future<void> _createV3Indexes() async {
    for (final sql in const [
      'CREATE INDEX IF NOT EXISTS idx_wa_status ON whatsapp_messages (gym_id, status, created_at)',
      'CREATE INDEX IF NOT EXISTS idx_wa_member ON whatsapp_messages (member_id, created_at)',
      'CREATE INDEX IF NOT EXISTS idx_attendance_gym_date ON attendance_records (gym_id, attendance_date)',
      'CREATE INDEX IF NOT EXISTS idx_members_trainer ON members (trainer_id)',
    ]) {
      await customStatement(sql);
    }
  }

  @override
  int get schemaVersion => currentSchemaVersion;

  @override
  MigrationStrategy get migration => MigrationStrategy(
    onCreate: (m) async {
      await m.createAll();
      await customStatement(
        'CREATE INDEX IF NOT EXISTS idx_sync_ops_status ON sync_operations (status, created_at)',
      );
      await customStatement(
        'CREATE INDEX IF NOT EXISTS idx_audit_gym_created ON audit_logs (gym_id, created_at)',
      );
      await _createV2Indexes();
      await _createV3Indexes();
    },
    onUpgrade: (m, from, to) async {
      // Additive steps only; destructive changes need a documented recovery
      // plan. Every step runs inside Drift's migration transaction.
      if (from < 2) {
        await m.createTable(membershipPlans);
        await m.createTable(members);
        await m.createTable(memberships);
        await m.createTable(payments);
        await m.createTable(paymentEvents);
        await m.createTable(membershipFreezes);
        await _createV2Indexes();
      }
      if (from < 3) {
        // From v1 the members table was just created in its current shape.
        if (from >= 2) await m.addColumn(members, members.trainerId);
        await m.createTable(whatsappMessages);
        await m.createTable(trainers);
        await m.createTable(attendanceRecords);
        await _createV3Indexes();
      }
    },
    beforeOpen: (details) async {
      await customStatement('PRAGMA foreign_keys = ON');
    },
  );
}
