import 'package:drift/drift.dart';

import '../core/clock.dart';
import '../core/security/password_hasher.dart';
import '../core/validators.dart';
import '../database/app_database.dart';
import 'gym_repository.dart';
import 'ids.dart';
import 'outbox.dart';

enum AuthFailure { invalidCredentials, lockedOut, invalidRecoveryCode }

class AuthException implements Exception {
  AuthException(this.failure, {this.retryAfter});
  final AuthFailure failure;
  final Duration? retryAfter;
  @override
  String toString() => 'AuthException($failure)';
}

/// Result of first-run setup. [recoveryCode] is shown once and never stored
/// in plaintext.
class SetupResult {
  const SetupResult({
    required this.gymId,
    required this.ownerId,
    required this.recoveryCode,
  });
  final String gymId;
  final String ownerId;
  final String recoveryCode;
}

/// Local owner credential store (Phase 1). Cloud authentication via Supabase
/// replaces credential verification in Phase 5; this class keeps the same
/// surface so screens are unaffected.
class AuthRepository {
  AuthRepository(
    this._db,
    this._clock,
    this._hasher, {
    this.maxFailedAttempts = 5,
    this.lockDuration = const Duration(minutes: 15),
  }) : _outbox = OutboxWriter(_db, _clock);

  final AppDatabase _db;
  final Clock _clock;
  final PasswordHasher _hasher;
  final OutboxWriter _outbox;
  final int maxFailedAttempts;
  final Duration lockDuration;

  Future<bool> isSetupComplete() async =>
      (await _db.select(_db.gymOwners).get()).isNotEmpty;

  /// First-run provisioning. Only possible while no owner exists, so it cannot
  /// be used to add a second owner or take over an installed gym.
  Future<SetupResult> provisionOwner({
    required GymProfileInput gym,
    required String ownerName,
    required String email,
    required String password,
    required String deviceName,
    required String platform,
  }) async {
    final errors = <String, String>{...gym.validate()};
    final nameError = Validators.required(ownerName, field: 'Owner name');
    if (nameError != null) errors['ownerName'] = nameError;
    final emailError = Validators.email(email);
    if (emailError != null) errors['email'] = emailError;
    final pwError = Validators.password(password);
    if (pwError != null) errors['password'] = pwError;
    if (errors.isNotEmpty) throw ValidationException(errors);

    final recoveryCode = _hasher.generateRecoveryCode();
    final passwordHash = _hasher.hash(password);
    final recoveryHash = _hasher.hash(recoveryCode);

    return _db.transaction(() async {
      if (await isSetupComplete()) {
        throw StateError('Owner already provisioned');
      }
      final now = _clock.now();
      final gymId = newId();
      final ownerId = newId();
      final deviceId = newId();
      final contact = gym.contactNumber.trim().isEmpty
          ? ''
          : Validators.normalizeIndianMobile(gym.contactNumber)!;
      final upi = gym.upiId?.trim();

      await _db
          .into(_db.gyms)
          .insert(
            GymsCompanion.insert(
              id: gymId,
              name: gym.name.trim(),
              address: Value(gym.address.trim()),
              contactNumber: Value(contact),
              timezone: Value(gym.timezone),
              currency: Value(gym.currency),
              upiId: Value(upi == null || upi.isEmpty ? null : upi),
              upiPayeeName: Value(
                upi == null || upi.isEmpty ? null : gym.upiPayeeName?.trim(),
              ),
              logoPath: Value(gym.logoPath),
              createdAt: now,
              updatedAt: now,
            ),
          );
      await _db
          .into(_db.gymOwners)
          .insert(
            GymOwnersCompanion.insert(
              id: ownerId,
              gymId: gymId,
              displayName: ownerName.trim(),
              email: email.trim().toLowerCase(),
              passwordHash: passwordHash,
              recoveryHash: recoveryHash,
              createdAt: now,
              updatedAt: now,
            ),
          );
      await _db
          .into(_db.appDevices)
          .insert(
            AppDevicesCompanion.insert(
              id: deviceId,
              gymId: gymId,
              deviceName: deviceName,
              platform: platform,
              lastSeenAt: Value(now),
              createdAt: now,
              updatedAt: now,
            ),
          );

      // Credentials never enter the outbox; Supabase Auth owns them (Phase 5).
      final gymRow = await (_db.select(
        _db.gyms,
      )..where((g) => g.id.equals(gymId))).getSingle();
      await _outbox.enqueue(
        gymId: gymId,
        entityTable: 'gyms',
        entityId: gymId,
        operation: 'insert',
        payload: gymRow.toJson(),
      );
      await _outbox.audit(
        gymId: gymId,
        actorId: ownerId,
        action: 'owner.provisioned',
        entityTable: 'gym_owners',
        entityId: ownerId,
      );
      return SetupResult(
        gymId: gymId,
        ownerId: ownerId,
        recoveryCode: recoveryCode,
      );
    });
  }

  /// Verifies credentials with lockout. Failure messages are intentionally
  /// identical for unknown email and wrong password.
  Future<GymOwner> login(String email, String password) async {
    final owner =
        await (_db.select(_db.gymOwners)..where(
              (o) =>
                  o.email.equals(email.trim().toLowerCase()) &
                  o.deletedAt.isNull(),
            ))
            .getSingleOrNull();
    if (owner == null) {
      // Burn comparable time so unknown emails are not distinguishable.
      _hasher.verify(password, _hasher.hash('decoy-password'));
      throw AuthException(AuthFailure.invalidCredentials);
    }
    final now = _clock.now();
    final lockedUntil = owner.lockedUntil;
    if (lockedUntil != null && lockedUntil.isAfter(now)) {
      throw AuthException(
        AuthFailure.lockedOut,
        retryAfter: lockedUntil.difference(now),
      );
    }
    if (_hasher.verify(password, owner.passwordHash)) {
      await (_db.update(
        _db.gymOwners,
      )..where((o) => o.id.equals(owner.id))).write(
        const GymOwnersCompanion(
          failedAttempts: Value(0),
          lockedUntil: Value(null),
        ),
      );
      await _outbox.audit(
        gymId: owner.gymId,
        actorId: owner.id,
        action: 'owner.login',
      );
      return owner;
    }
    final attempts = owner.failedAttempts + 1;
    final lock = attempts >= maxFailedAttempts;
    await (_db.update(
      _db.gymOwners,
    )..where((o) => o.id.equals(owner.id))).write(
      GymOwnersCompanion(
        failedAttempts: Value(lock ? 0 : attempts),
        lockedUntil: Value(lock ? now.add(lockDuration) : null),
      ),
    );
    await _outbox.audit(
      gymId: owner.gymId,
      actorId: owner.id,
      action: lock ? 'owner.locked_out' : 'owner.login_failed',
    );
    throw lock
        ? AuthException(AuthFailure.lockedOut, retryAfter: lockDuration)
        : AuthException(AuthFailure.invalidCredentials);
  }

  /// Re-confirms the signed-in owner's password before a sensitive action.
  /// Shares the login lockout counter, so it cannot be used to brute-force
  /// the password. Returns false on a wrong password.
  Future<bool> verifyPassword(String ownerId, String password) async {
    final owner = await (_db.select(
      _db.gymOwners,
    )..where((o) => o.id.equals(ownerId))).getSingle();
    final now = _clock.now();
    final lockedUntil = owner.lockedUntil;
    if (lockedUntil != null && lockedUntil.isAfter(now)) {
      throw AuthException(
        AuthFailure.lockedOut,
        retryAfter: lockedUntil.difference(now),
      );
    }
    if (_hasher.verify(password, owner.passwordHash)) {
      await (_db.update(
        _db.gymOwners,
      )..where((o) => o.id.equals(ownerId))).write(
        const GymOwnersCompanion(
          failedAttempts: Value(0),
          lockedUntil: Value(null),
        ),
      );
      return true;
    }
    final attempts = owner.failedAttempts + 1;
    final lock = attempts >= maxFailedAttempts;
    await (_db.update(_db.gymOwners)..where((o) => o.id.equals(ownerId))).write(
      GymOwnersCompanion(
        failedAttempts: Value(lock ? 0 : attempts),
        lockedUntil: Value(lock ? now.add(lockDuration) : null),
      ),
    );
    await _outbox.audit(
      gymId: owner.gymId,
      actorId: owner.id,
      action: lock ? 'owner.locked_out' : 'owner.reauth_failed',
    );
    return false;
  }

  /// Resets the password using the one-time recovery code, then issues a new
  /// recovery code (the old one is invalidated). Returns the new code.
  Future<String> recoverPassword({
    required String email,
    required String recoveryCode,
    required String newPassword,
  }) async {
    final pwError = Validators.password(newPassword);
    if (pwError != null) throw ValidationException({'password': pwError});
    final owner =
        await (_db.select(_db.gymOwners)
              ..where((o) => o.email.equals(email.trim().toLowerCase())))
            .getSingleOrNull();
    final now = _clock.now();
    if (owner != null &&
        owner.lockedUntil != null &&
        owner.lockedUntil!.isAfter(now)) {
      throw AuthException(
        AuthFailure.lockedOut,
        retryAfter: owner.lockedUntil!.difference(now),
      );
    }
    final normalised = recoveryCode.trim().toUpperCase();
    if (owner == null || !_hasher.verify(normalised, owner.recoveryHash)) {
      if (owner != null) {
        final attempts = owner.failedAttempts + 1;
        final lock = attempts >= maxFailedAttempts;
        await (_db.update(
          _db.gymOwners,
        )..where((o) => o.id.equals(owner.id))).write(
          GymOwnersCompanion(
            failedAttempts: Value(lock ? 0 : attempts),
            lockedUntil: Value(lock ? now.add(lockDuration) : null),
          ),
        );
      }
      throw AuthException(AuthFailure.invalidRecoveryCode);
    }
    final newCode = _hasher.generateRecoveryCode();
    final pwHash = _hasher.hash(newPassword);
    final codeHash = _hasher.hash(newCode);
    await _db.transaction(() async {
      await (_db.update(
        _db.gymOwners,
      )..where((o) => o.id.equals(owner.id))).write(
        GymOwnersCompanion(
          passwordHash: Value(pwHash),
          recoveryHash: Value(codeHash),
          failedAttempts: const Value(0),
          lockedUntil: const Value(null),
          version: Value(owner.version + 1),
          updatedAt: Value(now),
        ),
      );
      await _outbox.audit(
        gymId: owner.gymId,
        actorId: owner.id,
        action: 'owner.password_recovered',
      );
    });
    return newCode;
  }
}
