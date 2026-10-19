import 'package:drift/drift.dart';

import '../core/clock.dart';
import '../core/dates.dart';
import '../core/validators.dart';
import '../database/app_database.dart';
import '../models/membership_logic.dart';
import 'gym_repository.dart' show ValidationException;
import 'ids.dart';
import 'outbox.dart';
import 'support.dart';

class CheckInResult {
  const CheckInResult(this.record, this.member, {required this.alreadyIn});
  final AttendanceRecord record;
  final Member member;

  /// True when the member had already checked in today (nothing was added).
  final bool alreadyIn;
}

class AttendanceEntry {
  const AttendanceEntry(this.record, this.member);
  final AttendanceRecord record;
  final Member member;
}

class AttendanceRepository {
  AttendanceRepository(this._db, this._clock)
    : _outbox = OutboxWriter(_db, _clock);

  final AppDatabase _db;
  final Clock _clock;
  final OutboxWriter _outbox;

  Future<DateTime> _today(String gymId) async {
    final gym = await (_db.select(
      _db.gyms,
    )..where((g) => g.id.equals(gymId))).getSingle();
    return GymDates.today(_clock.now(), gym.timezone);
  }

  /// Finds a member by member code (exact, case-insensitive) or phone number.
  Future<Member?> findMember(String gymId, String query) async {
    final q = query.trim();
    if (q.isEmpty) return null;
    final phone = Validators.normalizeIndianMobile(q);
    final rows = await (_db.select(
      _db.members,
    )..where((m) => m.gymId.equals(gymId) & m.deletedAt.isNull())).get();
    for (final m in rows) {
      if (m.memberCode.toLowerCase() == q.toLowerCase()) return m;
      if (phone != null && (m.phone == phone || m.altPhone == phone)) return m;
    }
    return null;
  }

  Stream<List<AttendanceEntry>> watchToday(String gymId) =>
      watchQuery(_db, [_db.attendanceRecords, _db.members], () => today(gymId));

  Future<List<AttendanceEntry>> today(String gymId) async {
    final day = await _today(gymId);
    final recs =
        await (_db.select(_db.attendanceRecords)
              ..where(
                (a) =>
                    a.gymId.equals(gymId) &
                    a.attendanceDate.equals(day) &
                    a.deletedAt.isNull(),
              )
              ..orderBy([(a) => OrderingTerm.desc(a.checkedInAt)]))
            .get();
    final out = <AttendanceEntry>[];
    for (final r in recs) {
      final m = await (_db.select(
        _db.members,
      )..where((x) => x.id.equals(r.memberId))).getSingle();
      out.add(AttendanceEntry(r, m));
    }
    return out;
  }

  Future<List<AttendanceRecord>> history(String memberId, {int limit = 60}) =>
      (_db.select(_db.attendanceRecords)
            ..where((a) => a.memberId.equals(memberId) & a.deletedAt.isNull())
            ..orderBy([(a) => OrderingTerm.desc(a.attendanceDate)])
            ..limit(limit))
          .get();

  Stream<int> watchCount(String memberId) =>
      watchQuery(_db, [_db.attendanceRecords], () async {
        final n = _db.attendanceRecords.id.count();
        final row =
            await (_db.selectOnly(_db.attendanceRecords)
                  ..addColumns([n])
                  ..where(
                    _db.attendanceRecords.memberId.equals(memberId) &
                        _db.attendanceRecords.deletedAt.isNull(),
                  ))
                .getSingle();
        return row.read(n) ?? 0;
      });

  /// Checks a member in for today. Only members with an active membership are
  /// admitted; anything else throws a [DomainException] explaining why, unless
  /// the owner supplies [overrideReason] (recorded in the audit log).
  /// Calling twice on the same day is harmless and returns `alreadyIn`.
  Future<CheckInResult> checkIn({
    required String memberId,
    required String actorId,
    String method = 'manual',
    String? overrideReason,
  }) {
    return _db.transaction(() async {
      final member = await (_db.select(
        _db.members,
      )..where((m) => m.id.equals(memberId))).getSingle();
      final day = await _today(member.gymId);

      final existing =
          await (_db.select(_db.attendanceRecords)..where(
                (a) =>
                    a.memberId.equals(memberId) & a.attendanceDate.equals(day),
              ))
              .getSingleOrNull();
      if (existing != null && existing.deletedAt == null) {
        return CheckInResult(existing, member, alreadyIn: true);
      }

      final memberships =
          await (_db.select(_db.memberships)..where(
                (m) => m.memberId.equals(memberId) & m.deletedAt.isNull(),
              ))
              .get();
      final status = overallMemberStatus(memberships, day);
      final override = overrideReason?.trim() ?? '';
      if (status != 'active' && override.length < 5) {
        throw DomainException(switch (status) {
          'expired' => '${member.fullName}\'s membership has expired',
          'frozen' => '${member.fullName}\'s membership is frozen',
          'pending_payment' =>
            '${member.fullName}\'s membership is waiting for payment',
          'cancelled' => '${member.fullName}\'s membership was cancelled',
          _ => '${member.fullName} has no membership',
        });
      }
      final active = memberships
          .where((m) => effectiveMembershipStatus(m, day) == 'active')
          .toList();

      final now = _clock.now();
      final String id;
      if (existing != null) {
        // Re-check-in after an undo: revive the tombstoned row.
        id = existing.id;
        await (_db.update(
          _db.attendanceRecords,
        )..where((a) => a.id.equals(id))).write(
          AttendanceRecordsCompanion(
            deletedAt: const Value(null),
            checkedInAt: Value(now),
            checkedInBy: Value(actorId),
            method: Value(method),
            version: Value(existing.version + 1),
            updatedAt: Value(now),
          ),
        );
      } else {
        id = newId();
        await _db
            .into(_db.attendanceRecords)
            .insert(
              AttendanceRecordsCompanion.insert(
                id: id,
                gymId: member.gymId,
                memberId: memberId,
                membershipId: Value(active.isEmpty ? null : active.first.id),
                attendanceDate: day,
                checkedInAt: now,
                method: method,
                checkedInBy: Value(actorId),
                createdAt: now,
                updatedAt: now,
              ),
            );
      }
      final row = await (_db.select(
        _db.attendanceRecords,
      )..where((a) => a.id.equals(id))).getSingle();
      await _outbox.enqueue(
        gymId: member.gymId,
        entityTable: 'attendance_records',
        entityId: id,
        operation: existing == null ? 'insert' : 'update',
        baseVersion: existing?.version ?? 0,
        payload: row.toJson(),
      );
      await _outbox.audit(
        gymId: member.gymId,
        actorId: actorId,
        action: status == 'active'
            ? 'attendance.checked_in'
            : 'attendance.override_checked_in',
        entityTable: 'attendance_records',
        entityId: id,
        reason: status == 'active' ? null : override,
        details: {'membershipStatus': status},
      );
      return CheckInResult(row, member, alreadyIn: false);
    });
  }

  /// Removes today's check-in (entered by mistake). Past days are immutable.
  Future<void> undo({
    required String attendanceId,
    required String actorId,
    required String reason,
  }) async {
    final why = reason.trim();
    if (why.length < 3) {
      throw ValidationException({'reason': 'Give a reason'});
    }
    return _db.transaction(() async {
      final a = await (_db.select(
        _db.attendanceRecords,
      )..where((x) => x.id.equals(attendanceId))).getSingle();
      if (a.deletedAt != null) return;
      if (GymDates.dateOnly(a.attendanceDate) != await _today(a.gymId)) {
        throw DomainException('Only today\'s check-ins can be undone');
      }
      final now = _clock.now();
      await (_db.update(
        _db.attendanceRecords,
      )..where((x) => x.id.equals(attendanceId))).write(
        AttendanceRecordsCompanion(
          deletedAt: Value(now),
          version: Value(a.version + 1),
          updatedAt: Value(now),
        ),
      );
      final row = await (_db.select(
        _db.attendanceRecords,
      )..where((x) => x.id.equals(attendanceId))).getSingle();
      await _outbox.enqueue(
        gymId: a.gymId,
        entityTable: 'attendance_records',
        entityId: attendanceId,
        operation: 'delete',
        baseVersion: a.version,
        payload: row.toJson(),
      );
      await _outbox.audit(
        gymId: a.gymId,
        actorId: actorId,
        action: 'attendance.undone',
        entityTable: 'attendance_records',
        entityId: attendanceId,
        reason: why,
      );
    });
  }
}
