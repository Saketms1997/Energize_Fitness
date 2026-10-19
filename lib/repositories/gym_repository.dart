import 'package:drift/drift.dart';

import '../core/clock.dart';
import '../core/validators.dart';
import '../database/app_database.dart';
import 'outbox.dart';

class ValidationException implements Exception {
  ValidationException(this.errors);
  final Map<String, String> errors;
  @override
  String toString() => 'ValidationException($errors)';
}

class GymProfileInput {
  const GymProfileInput({
    required this.name,
    this.address = '',
    this.contactNumber = '',
    this.timezone = 'Asia/Kolkata',
    this.currency = 'INR',
    this.upiId,
    this.upiPayeeName,
    this.logoPath,
  });

  final String name;
  final String address;
  final String contactNumber;
  final String timezone;
  final String currency;
  final String? upiId;
  final String? upiPayeeName;
  final String? logoPath;

  Map<String, String> validate() {
    final errors = <String, String>{};
    final nameError = Validators.required(name, field: 'Gym name');
    if (nameError != null) errors['name'] = nameError;
    if (name.trim().length > 120) errors['name'] = 'Gym name is too long';
    final phoneError = Validators.indianMobile(contactNumber, optional: true);
    if (phoneError != null) errors['contactNumber'] = phoneError;
    final upiError = Validators.upiId(upiId);
    if (upiError != null) errors['upiId'] = upiError;
    if ((upiId?.trim().isNotEmpty ?? false) &&
        (upiPayeeName?.trim().isEmpty ?? true)) {
      errors['upiPayeeName'] = 'Payee name is required with a UPI ID';
    }
    return errors;
  }
}

/// Reads and edits the gym profile. The gym name is data, never a constant.
class GymRepository {
  GymRepository(this._db, this._clock) : _outbox = OutboxWriter(_db, _clock);

  final AppDatabase _db;
  final Clock _clock;
  final OutboxWriter _outbox;

  Stream<Gym?> watchGym() =>
      (_db.select(_db.gyms)
            ..where((g) => g.deletedAt.isNull())
            ..limit(1))
          .watchSingleOrNull();

  Future<Gym?> currentGym() =>
      (_db.select(_db.gyms)
            ..where((g) => g.deletedAt.isNull())
            ..limit(1))
          .getSingleOrNull();

  Future<Gym> updateProfile({
    required String gymId,
    required String actorId,
    required GymProfileInput input,
  }) async {
    final errors = input.validate();
    if (errors.isNotEmpty) throw ValidationException(errors);

    return _db.transaction(() async {
      final existing = await (_db.select(
        _db.gyms,
      )..where((g) => g.id.equals(gymId))).getSingle();
      final now = _clock.now();
      final contact = input.contactNumber.trim().isEmpty
          ? ''
          : Validators.normalizeIndianMobile(input.contactNumber)!;
      final upi = input.upiId?.trim();
      await (_db.update(_db.gyms)..where((g) => g.id.equals(gymId))).write(
        GymsCompanion(
          name: Value(input.name.trim()),
          address: Value(input.address.trim()),
          contactNumber: Value(contact),
          timezone: Value(input.timezone),
          currency: Value(input.currency),
          upiId: Value(upi == null || upi.isEmpty ? null : upi),
          upiPayeeName: Value(
            upi == null || upi.isEmpty ? null : input.upiPayeeName?.trim(),
          ),
          logoPath: Value(input.logoPath),
          version: Value(existing.version + 1),
          updatedAt: Value(now),
        ),
      );
      final updated = await (_db.select(
        _db.gyms,
      )..where((g) => g.id.equals(gymId))).getSingle();
      await _outbox.enqueue(
        gymId: gymId,
        entityTable: 'gyms',
        entityId: gymId,
        operation: 'update',
        baseVersion: existing.version,
        payload: updated.toJson(),
      );
      await _outbox.audit(
        gymId: gymId,
        actorId: actorId,
        action: 'gym.profile_updated',
        entityTable: 'gyms',
        entityId: gymId,
        details: {
          'fromVersion': existing.version,
          'toVersion': updated.version,
        },
      );
      return updated;
    });
  }
}
