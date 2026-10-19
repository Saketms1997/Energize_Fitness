import 'package:drift/drift.dart';

import '../core/clock.dart';
import '../core/dates.dart';
import '../database/app_database.dart';
import '../models/membership_logic.dart';
import 'gym_repository.dart' show ValidationException;
import 'ids.dart';
import 'outbox.dart';
import 'support.dart';

/// Renewals, extensions, freezes and cancellation. Previous memberships and
/// their payments are never modified by a renewal.
class MembershipRepository {
  MembershipRepository(this._db, Clock clock)
    : _clock = clock,
      _outbox = OutboxWriter(_db, clock);

  final AppDatabase _db;
  final Clock _clock;
  final OutboxWriter _outbox;

  Future<Membership> _get(String id) =>
      (_db.select(_db.memberships)..where((m) => m.id.equals(id))).getSingle();

  Future<DateTime> _today(String gymId) async {
    final gym = await (_db.select(
      _db.gyms,
    )..where((g) => g.id.equals(gymId))).getSingle();
    return GymDates.today(_clock.now(), gym.timezone);
  }

  /// Creates the next subscription as `pending_payment`. It starts the day
  /// after the current one ends (or today if lapsed) unless [startDate] is
  /// given. Only one unpaid membership may exist per member, which also stops
  /// double-click duplicates.
  Future<Membership> renew({
    required String memberId,
    required String actorId,
    required String planId,
    int? pricePaise,
    DateTime? startDate,
  }) async {
    if (pricePaise != null && pricePaise < 0) {
      throw ValidationException({'pricePaise': 'Amount cannot be negative'});
    }
    return _db.transaction(() async {
      final member = await (_db.select(
        _db.members,
      )..where((m) => m.id.equals(memberId))).getSingle();
      final plan =
          await (_db.select(_db.membershipPlans)..where(
                (p) => p.id.equals(planId) & p.gymId.equals(member.gymId),
              ))
              .getSingleOrNull();
      if (plan == null || !plan.isActive || plan.deletedAt != null) {
        throw DomainException('Selected plan is not available');
      }
      final all = await (_db.select(
        _db.memberships,
      )..where((m) => m.memberId.equals(memberId))).get();
      if (all.any((m) => m.status == 'pending_payment')) {
        throw DomainException(
          'A membership is already waiting for payment. Settle or cancel it first.',
        );
      }
      final today = await _today(member.gymId);
      final previous = currentMembership(all);
      DateTime start = today;
      if (previous != null &&
          (previous.status == 'active' || previous.status == 'frozen') &&
          !previous.endDate.isBefore(today)) {
        start = GymDates.dateOnly(previous.endDate)
            .add(const Duration(days: 1));
      }
      if (startDate != null) start = GymDates.dateOnly(startDate);

      final now = _clock.now();
      final id = newId();
      await _db
          .into(_db.memberships)
          .insert(
            MembershipsCompanion.insert(
              id: id,
              gymId: member.gymId,
              memberId: memberId,
              planId: plan.id,
              planName: plan.name,
              pricePaise: pricePaise ?? plan.pricePaise,
              startDate: start,
              endDate: GymDates.membershipEnd(
                start,
                plan.durationUnit,
                plan.durationValue,
              ),
              status: 'pending_payment',
              previousMembershipId: Value(previous?.id),
              createdAt: now,
              updatedAt: now,
            ),
          );
      final row = await _get(id);
      await _outbox.enqueue(
        gymId: member.gymId,
        entityTable: 'memberships',
        entityId: id,
        operation: 'insert',
        payload: row.toJson(),
      );
      await _outbox.audit(
        gymId: member.gymId,
        actorId: actorId,
        action: 'membership.renewed',
        entityTable: 'memberships',
        entityId: id,
        details: {'previousMembershipId': previous?.id, 'planId': plan.id},
      );
      return row;
    });
  }

  /// Adds [days] to an active/frozen membership (e.g. goodwill extension).
  Future<Membership> extend({
    required String membershipId,
    required String actorId,
    required int days,
    required String reason,
  }) async {
    final why = reason.trim();
    if (days < 1 || days > 365) {
      throw ValidationException({'days': 'Enter between 1 and 365 days'});
    }
    if (why.length < 3) {
      throw ValidationException({'reason': 'Give a reason'});
    }
    return _db.transaction(() async {
      final ms = await _get(membershipId);
      if (ms.status != 'active' && ms.status != 'frozen') {
        throw DomainException(
          'Only active or frozen memberships can be extended',
        );
      }
      return _save(
        ms,
        MembershipsCompanion(
          endDate: Value(
            GymDates.dateOnly(ms.endDate).add(Duration(days: days)),
          ),
        ),
        actorId,
        'membership.extended',
        reason: why,
        details: {'days': days},
      );
    });
  }

  Future<Membership> freeze({
    required String membershipId,
    required String actorId,
    required String reason,
  }) async {
    final why = reason.trim();
    if (why.length < 3) {
      throw ValidationException({'reason': 'Give a reason'});
    }
    return _db.transaction(() async {
      final ms = await _get(membershipId);
      final today = await _today(ms.gymId);
      if (effectiveMembershipStatus(ms, today) != 'active') {
        throw DomainException('Only an active membership can be frozen');
      }
      final now = _clock.now();
      await _db
          .into(_db.membershipFreezes)
          .insert(
            MembershipFreezesCompanion.insert(
              id: newId(),
              gymId: ms.gymId,
              membershipId: ms.id,
              startDate: today,
              reason: why,
              createdAt: now,
              updatedAt: now,
            ),
          );
      return _save(
        ms,
        const MembershipsCompanion(status: Value('frozen')),
        actorId,
        'membership.frozen',
        reason: why,
      );
    });
  }

  /// Ends the freeze and pushes the end date out by the days actually frozen.
  Future<Membership> unfreeze({
    required String membershipId,
    required String actorId,
  }) async {
    return _db.transaction(() async {
      final ms = await _get(membershipId);
      if (ms.status != 'frozen') {
        throw DomainException('Membership is not frozen');
      }
      final today = await _today(ms.gymId);
      final freeze =
          await (_db.select(_db.membershipFreezes)..where(
                (f) => f.membershipId.equals(ms.id) & f.endDate.isNull(),
              ))
              .getSingle();
      final raw = GymDates.daysBetween(freeze.startDate, today);
      final days = raw < 0 ? 0 : raw;
      await (_db.update(
        _db.membershipFreezes,
      )..where((f) => f.id.equals(freeze.id))).write(
        MembershipFreezesCompanion(
          endDate: Value(today),
          daysFrozen: Value(days),
          version: Value(freeze.version + 1),
          updatedAt: Value(_clock.now()),
        ),
      );
      final updatedFreeze = await (_db.select(
        _db.membershipFreezes,
      )..where((f) => f.id.equals(freeze.id))).getSingle();
      await _outbox.enqueue(
        gymId: ms.gymId,
        entityTable: 'membership_freezes',
        entityId: freeze.id,
        operation: 'update',
        baseVersion: freeze.version,
        payload: updatedFreeze.toJson(),
      );
      return _save(
        ms,
        MembershipsCompanion(
          status: const Value('active'),
          endDate: Value(
            GymDates.dateOnly(ms.endDate).add(Duration(days: days)),
          ),
        ),
        actorId,
        'membership.unfrozen',
        details: {'daysFrozen': days},
      );
    });
  }

  Future<Membership> cancel({
    required String membershipId,
    required String actorId,
    required String reason,
  }) async {
    final why = reason.trim();
    if (why.length < 3) {
      throw ValidationException({'reason': 'Give a reason'});
    }
    return _db.transaction(() async {
      final ms = await _get(membershipId);
      if (ms.status == 'cancelled') return ms;
      return _save(
        ms,
        MembershipsCompanion(
          status: const Value('cancelled'),
          statusReason: Value(why),
        ),
        actorId,
        'membership.cancelled',
        reason: why,
      );
    });
  }

  /// Owner override: start a membership that still has an unpaid balance
  /// (partial payment, or a free membership). The reason is audited and the
  /// balance stays visible as pending money.
  Future<Membership> activateWithBalance({
    required String membershipId,
    required String actorId,
    required String reason,
  }) async {
    final why = reason.trim();
    if (why.length < 5) {
      throw ValidationException({'reason': 'Give a reason for the override'});
    }
    return _db.transaction(() async {
      final ms = await _get(membershipId);
      if (ms.status != 'pending_payment') {
        throw DomainException('Membership is not waiting for payment');
      }
      return _save(
        ms,
        const MembershipsCompanion(status: Value('active')),
        actorId,
        'membership.activated_with_balance',
        reason: why,
      );
    });
  }

  Future<Membership> _save(
    Membership ms,
    MembershipsCompanion change,
    String actorId,
    String action, {
    String? reason,
    Map<String, Object?> details = const {},
  }) async {
    await (_db.update(_db.memberships)..where((m) => m.id.equals(ms.id))).write(
      change.copyWith(
        version: Value(ms.version + 1),
        updatedAt: Value(_clock.now()),
      ),
    );
    final row = await _get(ms.id);
    await _outbox.enqueue(
      gymId: ms.gymId,
      entityTable: 'memberships',
      entityId: ms.id,
      operation: 'update',
      baseVersion: ms.version,
      payload: row.toJson(),
    );
    await _outbox.audit(
      gymId: ms.gymId,
      actorId: actorId,
      action: action,
      entityTable: 'memberships',
      entityId: ms.id,
      reason: reason,
      details: details,
    );
    return row;
  }
}
