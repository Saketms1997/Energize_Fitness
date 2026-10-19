import 'package:drift/drift.dart';

import '../core/clock.dart';
import '../core/validators.dart';
import '../database/app_database.dart';
import 'gym_repository.dart' show ValidationException;
import 'ids.dart';
import 'outbox.dart';
import 'support.dart';

class PlanInput {
  const PlanInput({
    required this.name,
    required this.durationUnit,
    required this.durationValue,
    required this.pricePaise,
  });

  final String name;
  final String durationUnit; // 'days' | 'months'
  final int durationValue;
  final int pricePaise;

  Map<String, String> validate() {
    final errors = <String, String>{};
    final nameError = Validators.required(name, field: 'Plan name');
    if (nameError != null) errors['name'] = nameError;
    if (name.trim().length > 80) errors['name'] = 'Plan name is too long';
    if (!const ['days', 'months'].contains(durationUnit)) {
      errors['durationUnit'] = 'Choose days or months';
    }
    if (durationValue < 1 || durationValue > 3650) {
      errors['durationValue'] = 'Duration must be between 1 and 3650';
    }
    if (pricePaise < 0) errors['pricePaise'] = 'Price cannot be negative';
    return errors;
  }
}

class PlanRepository {
  PlanRepository(this._db, Clock clock)
    : _clock = clock,
      _outbox = OutboxWriter(_db, clock);

  final AppDatabase _db;
  final Clock _clock;
  final OutboxWriter _outbox;

  Stream<List<MembershipPlan>> watchPlans({bool activeOnly = false}) =>
      watchQuery(_db, [
        _db.membershipPlans,
      ], () => listPlans(activeOnly: activeOnly));

  Future<List<MembershipPlan>> listPlans({bool activeOnly = false}) {
    final q = _db.select(_db.membershipPlans)
      ..where((p) => p.deletedAt.isNull())
      ..orderBy([(p) => OrderingTerm.asc(p.durationValue)]);
    if (activeOnly) q.where((p) => p.isActive.equals(true));
    return q.get();
  }

  Future<MembershipPlan> createPlan({
    required String gymId,
    required String actorId,
    required PlanInput input,
  }) async {
    final errors = input.validate();
    if (errors.isNotEmpty) throw ValidationException(errors);
    return _db.transaction(() async {
      final taken =
          await (_db.select(_db.membershipPlans)..where(
                (p) => p.gymId.equals(gymId) & p.name.equals(input.name.trim()),
              ))
              .getSingleOrNull();
      if (taken != null) {
        throw ValidationException({'name': 'A plan with this name exists'});
      }
      final now = _clock.now();
      final id = newId();
      await _db
          .into(_db.membershipPlans)
          .insert(
            MembershipPlansCompanion.insert(
              id: id,
              gymId: gymId,
              name: input.name.trim(),
              durationUnit: input.durationUnit,
              durationValue: input.durationValue,
              pricePaise: input.pricePaise,
              createdAt: now,
              updatedAt: now,
            ),
          );
      final row = await (_db.select(
        _db.membershipPlans,
      )..where((p) => p.id.equals(id))).getSingle();
      await _outbox.enqueue(
        gymId: gymId,
        entityTable: 'membership_plans',
        entityId: id,
        operation: 'insert',
        payload: row.toJson(),
      );
      await _outbox.audit(
        gymId: gymId,
        actorId: actorId,
        action: 'plan.created',
        entityTable: 'membership_plans',
        entityId: id,
      );
      return row;
    });
  }

  /// Edits a plan. Existing memberships keep the price/name they were sold at.
  Future<MembershipPlan> updatePlan({
    required String planId,
    required int expectedVersion,
    required String actorId,
    required PlanInput input,
    bool? isActive,
  }) async {
    final errors = input.validate();
    if (errors.isNotEmpty) throw ValidationException(errors);
    return _db.transaction(() async {
      final existing = await (_db.select(
        _db.membershipPlans,
      )..where((p) => p.id.equals(planId))).getSingle();
      if (existing.version != expectedVersion) {
        throw ConcurrentModificationException('plan', expectedVersion);
      }
      await (_db.update(
        _db.membershipPlans,
      )..where((p) => p.id.equals(planId))).write(
        MembershipPlansCompanion(
          name: Value(input.name.trim()),
          durationUnit: Value(input.durationUnit),
          durationValue: Value(input.durationValue),
          pricePaise: Value(input.pricePaise),
          isActive: Value(isActive ?? existing.isActive),
          version: Value(existing.version + 1),
          updatedAt: Value(_clock.now()),
        ),
      );
      final row = await (_db.select(
        _db.membershipPlans,
      )..where((p) => p.id.equals(planId))).getSingle();
      await _outbox.enqueue(
        gymId: existing.gymId,
        entityTable: 'membership_plans',
        entityId: planId,
        operation: 'update',
        baseVersion: existing.version,
        payload: row.toJson(),
      );
      await _outbox.audit(
        gymId: existing.gymId,
        actorId: actorId,
        action: 'plan.updated',
        entityTable: 'membership_plans',
        entityId: planId,
        details: {'priceFrom': existing.pricePaise, 'priceTo': row.pricePaise},
      );
      return row;
    });
  }
}
