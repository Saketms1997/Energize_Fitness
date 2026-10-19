import 'package:drift/drift.dart';

import '../core/clock.dart';
import '../core/validators.dart';
import '../database/app_database.dart';
import 'gym_repository.dart' show ValidationException;
import 'ids.dart';
import 'outbox.dart';
import 'support.dart';

class TrainerInput {
  const TrainerInput({required this.name, this.phone, this.specialty});
  final String name;
  final String? phone;
  final String? specialty;

  Map<String, String> validate() {
    final errors = <String, String>{};
    final n = Validators.required(name, field: 'Name');
    if (n != null) errors['name'] = n;
    if (name.trim().length > 120) errors['name'] = 'Name is too long';
    final p = Validators.indianMobile(phone, optional: true);
    if (p != null) errors['phone'] = p;
    return errors;
  }
}

class TrainerRepository {
  TrainerRepository(this._db, this._clock)
    : _outbox = OutboxWriter(_db, _clock);

  final AppDatabase _db;
  final Clock _clock;
  final OutboxWriter _outbox;

  Stream<List<Trainer>> watchTrainers(String gymId) =>
      watchQuery(_db, [_db.trainers, _db.members], () => listTrainers(gymId));

  Future<List<Trainer>> listTrainers(String gymId, {bool activeOnly = false}) =>
      (_db.select(_db.trainers)
            ..where(
              (t) =>
                  t.gymId.equals(gymId) &
                  t.deletedAt.isNull() &
                  (activeOnly ? t.isActive.equals(true) : const Constant(true)),
            )
            ..orderBy([(t) => OrderingTerm.asc(t.name)]))
          .get();

  Future<int> memberCount(String trainerId) async {
    final n = _db.members.id.count();
    final row =
        await (_db.selectOnly(_db.members)
              ..addColumns([n])
              ..where(
                _db.members.trainerId.equals(trainerId) &
                    _db.members.deletedAt.isNull(),
              ))
            .getSingle();
    return row.read(n) ?? 0;
  }

  Future<Trainer> _get(String id) =>
      (_db.select(_db.trainers)..where((t) => t.id.equals(id))).getSingle();

  String? _trim(String? v) {
    final t = v?.trim();
    return (t == null || t.isEmpty) ? null : t;
  }

  Future<Trainer> create({
    required String gymId,
    required String actorId,
    required TrainerInput input,
  }) async {
    final errors = input.validate();
    if (errors.isNotEmpty) throw ValidationException(errors);
    return _db.transaction(() async {
      final now = _clock.now();
      final id = newId();
      await _db
          .into(_db.trainers)
          .insert(
            TrainersCompanion.insert(
              id: id,
              gymId: gymId,
              name: input.name.trim(),
              phone: Value(Validators.normalizeIndianMobile(input.phone)),
              specialty: Value(_trim(input.specialty)),
              createdAt: now,
              updatedAt: now,
            ),
          );
      final row = await _get(id);
      await _outbox.enqueue(
        gymId: gymId,
        entityTable: 'trainers',
        entityId: id,
        operation: 'insert',
        payload: row.toJson(),
      );
      await _outbox.audit(
        gymId: gymId,
        actorId: actorId,
        action: 'trainer.created',
        entityTable: 'trainers',
        entityId: id,
      );
      return row;
    });
  }

  Future<Trainer> update({
    required String trainerId,
    required int expectedVersion,
    required String actorId,
    required TrainerInput input,
    bool? isActive,
  }) async {
    final errors = input.validate();
    if (errors.isNotEmpty) throw ValidationException(errors);
    return _db.transaction(() async {
      final t = await _get(trainerId);
      if (t.version != expectedVersion) {
        throw ConcurrentModificationException('trainer', expectedVersion);
      }
      await (_db.update(
        _db.trainers,
      )..where((x) => x.id.equals(trainerId))).write(
        TrainersCompanion(
          name: Value(input.name.trim()),
          phone: Value(Validators.normalizeIndianMobile(input.phone)),
          specialty: Value(_trim(input.specialty)),
          isActive: Value(isActive ?? t.isActive),
          version: Value(t.version + 1),
          updatedAt: Value(_clock.now()),
        ),
      );
      final row = await _get(trainerId);
      await _outbox.enqueue(
        gymId: t.gymId,
        entityTable: 'trainers',
        entityId: trainerId,
        operation: 'update',
        baseVersion: t.version,
        payload: row.toJson(),
      );
      await _outbox.audit(
        gymId: t.gymId,
        actorId: actorId,
        action: isActive == false && t.isActive
            ? 'trainer.deactivated'
            : 'trainer.updated',
        entityTable: 'trainers',
        entityId: trainerId,
      );
      return row;
    });
  }

  /// Assigns (or clears, with null) a member's trainer. Inactive trainers
  /// cannot receive new members.
  Future<Member> assign({
    required String memberId,
    required String? trainerId,
    required String actorId,
  }) {
    return _db.transaction(() async {
      final m = await (_db.select(
        _db.members,
      )..where((x) => x.id.equals(memberId))).getSingle();
      if (trainerId != null) {
        final t =
            await (_db.select(_db.trainers)..where(
                  (x) => x.id.equals(trainerId) & x.gymId.equals(m.gymId),
                ))
                .getSingleOrNull();
        if (t == null || t.deletedAt != null) {
          throw DomainException('Trainer not found');
        }
        if (!t.isActive && m.trainerId != trainerId) {
          throw DomainException('${t.name} is inactive');
        }
      }
      if (m.trainerId == trainerId) return m;
      await (_db.update(
        _db.members,
      )..where((x) => x.id.equals(memberId))).write(
        MembersCompanion(
          trainerId: Value(trainerId),
          version: Value(m.version + 1),
          updatedAt: Value(_clock.now()),
        ),
      );
      final row = await (_db.select(
        _db.members,
      )..where((x) => x.id.equals(memberId))).getSingle();
      await _outbox.enqueue(
        gymId: m.gymId,
        entityTable: 'members',
        entityId: memberId,
        operation: 'update',
        baseVersion: m.version,
        payload: row.toJson(),
      );
      await _outbox.audit(
        gymId: m.gymId,
        actorId: actorId,
        action: 'member.trainer_assigned',
        entityTable: 'members',
        entityId: memberId,
        details: {'from': m.trainerId, 'to': trainerId},
      );
      return row;
    });
  }
}
