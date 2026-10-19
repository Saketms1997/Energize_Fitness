import 'package:drift/drift.dart';

import '../database/app_database.dart';

/// Writes server records into the local database.
class RecordApplier {
  RecordApplier(this._db);

  final AppDatabase _db;

  /// Tables that take part in sync. Anything else is refused on both sides.
  static const syncable = {
    'gyms',
    'membership_plans',
    'members',
    'memberships',
    'payments',
    'membership_freezes',
    'trainers',
    'attendance_records',
    'whatsapp_messages',
  };

  Future<int?> localVersion(String table, String id) async {
    if (!syncable.contains(table)) return null;
    final row = await _db
        .customSelect(
          'SELECT version FROM $table WHERE id = ?',
          variables: [Variable<String>(id)],
        )
        .getSingleOrNull();
    return row?.read<int>('version');
  }

  /// Inserts or replaces the row. Throws on constraint violations.
  Future<void> upsert(String table, Map<String, dynamic> json) async {
    switch (table) {
      case 'gyms':
        await _db.into(_db.gyms).insertOnConflictUpdate(Gym.fromJson(json));
      case 'membership_plans':
        await _db
            .into(_db.membershipPlans)
            .insertOnConflictUpdate(MembershipPlan.fromJson(json));
      case 'members':
        await _db
            .into(_db.members)
            .insertOnConflictUpdate(Member.fromJson(json));
      case 'memberships':
        await _db
            .into(_db.memberships)
            .insertOnConflictUpdate(Membership.fromJson(json));
      case 'payments':
        await _db
            .into(_db.payments)
            .insertOnConflictUpdate(Payment.fromJson(json));
      case 'membership_freezes':
        await _db
            .into(_db.membershipFreezes)
            .insertOnConflictUpdate(MembershipFreeze.fromJson(json));
      case 'trainers':
        await _db
            .into(_db.trainers)
            .insertOnConflictUpdate(Trainer.fromJson(json));
      case 'attendance_records':
        await _db
            .into(_db.attendanceRecords)
            .insertOnConflictUpdate(AttendanceRecord.fromJson(json));
      case 'whatsapp_messages':
        await _db
            .into(_db.whatsappMessages)
            .insertOnConflictUpdate(WhatsappMessage.fromJson(json));
      default:
        throw ArgumentError('Table $table is not synchronised');
    }
  }
}
