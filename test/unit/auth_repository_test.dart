import 'package:flutter_test/flutter_test.dart';
import 'package:gym_management_system/core/clock.dart';
import 'package:gym_management_system/core/security/password_hasher.dart';
import 'package:gym_management_system/database/app_database.dart';
import 'package:gym_management_system/repositories/auth_repository.dart';
import 'package:gym_management_system/repositories/gym_repository.dart';

class FakeClock implements Clock {
  FakeClock(this.t);
  DateTime t;
  @override
  DateTime now() => t;
  void advance(Duration d) => t = t.add(d);
}

void main() {
  late AppDatabase db;
  late FakeClock clock;
  late AuthRepository auth;
  late GymRepository gyms;

  const gym = GymProfileInput(
    name: 'Iron Temple',
    contactNumber: '+91 98765 43210',
    upiId: 'owner@okbank',
    upiPayeeName: 'Iron Temple',
  );

  Future<SetupResult> setup() => auth.provisionOwner(
    gym: gym,
    ownerName: 'Asha',
    email: 'Owner@Example.com',
    password: 'strongpass123',
    deviceName: 'test-pc',
    platform: 'windows',
  );

  setUp(() {
    db = AppDatabase.inMemory();
    clock = FakeClock(DateTime.utc(2026, 1, 1));
    auth = AuthRepository(db, clock, PasswordHasher(iterations: 100));
    gyms = GymRepository(db, clock);
  });
  tearDown(() => db.close());

  test('fresh database is not set up', () async {
    expect(await auth.isSetupComplete(), isFalse);
  });

  test(
    'provisioning stores gym, owner, device, outbox and audit atomically',
    () async {
      final r = await setup();
      expect(await auth.isSetupComplete(), isTrue);

      final g = await gyms.currentGym();
      expect(g!.name, 'Iron Temple');
      expect(g.timezone, 'Asia/Kolkata');
      expect(g.currency, 'INR');
      expect(g.contactNumber, '9876543210');

      final owner = await db.select(db.gymOwners).getSingle();
      expect(owner.email, 'owner@example.com');
      expect(owner.passwordHash, isNot(contains('strongpass')));
      expect(owner.recoveryHash, isNot(contains(r.recoveryCode)));

      final ops = await db.select(db.syncOperations).get();
      expect(ops.single.status, 'pending');
      expect(ops.single.payloadJson, isNot(contains('passwordHash')));
      expect(
        (await db.select(db.auditLogs).get()).single.action,
        'owner.provisioned',
      );
      expect(await db.select(db.appDevices).get(), hasLength(1));
    },
  );

  test('setup cannot run twice (no second owner / takeover)', () async {
    await setup();
    expect(setup, throwsStateError);
  });

  test('setup rejects invalid input without writing anything', () async {
    await expectLater(
      auth.provisionOwner(
        gym: const GymProfileInput(name: ''),
        ownerName: '',
        email: 'bad',
        password: 'short',
        deviceName: 'd',
        platform: 'p',
      ),
      throwsA(isA<ValidationException>()),
    );
    expect(await auth.isSetupComplete(), isFalse);
    expect(await db.select(db.gyms).get(), isEmpty);
  });

  test(
    'login succeeds with case-insensitive email, fails with wrong password',
    () async {
      await setup();
      expect(
        (await auth.login('OWNER@example.com', 'strongpass123')).displayName,
        'Asha',
      );
      expect(
        () => auth.login('owner@example.com', 'wrongpass999'),
        throwsA(
          isA<AuthException>().having(
            (e) => e.failure,
            'failure',
            AuthFailure.invalidCredentials,
          ),
        ),
      );
      expect(
        () => auth.login('nobody@example.com', 'whatever12345'),
        throwsA(
          isA<AuthException>().having(
            (e) => e.failure,
            'failure',
            AuthFailure.invalidCredentials,
          ),
        ),
      );
    },
  );

  test(
    'locks out after 5 failures and unlocks after the lock window',
    () async {
      await setup();
      for (var i = 0; i < 5; i++) {
        await expectLater(
          auth.login('owner@example.com', 'wrongpass999'),
          throwsA(isA<AuthException>()),
        );
      }
      // Even the correct password is refused while locked.
      await expectLater(
        auth.login('owner@example.com', 'strongpass123'),
        throwsA(
          isA<AuthException>().having(
            (e) => e.failure,
            'failure',
            AuthFailure.lockedOut,
          ),
        ),
      );
      clock.advance(const Duration(minutes: 16));
      expect(
        (await auth.login('owner@example.com', 'strongpass123')).email,
        'owner@example.com',
      );
    },
  );

  test('recovery code resets password once and rotates the code', () async {
    final r = await setup();
    final newCode = await auth.recoverPassword(
      email: 'owner@example.com',
      recoveryCode: r.recoveryCode.toLowerCase(),
      newPassword: 'brandnewpass99',
    );
    expect(newCode, isNot(r.recoveryCode));
    await auth.login('owner@example.com', 'brandnewpass99');
    await expectLater(
      auth.login('owner@example.com', 'strongpass123'),
      throwsA(isA<AuthException>()),
    );
    // Old code is now invalid.
    await expectLater(
      auth.recoverPassword(
        email: 'owner@example.com',
        recoveryCode: r.recoveryCode,
        newPassword: 'anotherpass99',
      ),
      throwsA(
        isA<AuthException>().having(
          (e) => e.failure,
          'failure',
          AuthFailure.invalidRecoveryCode,
        ),
      ),
    );
  });

  test(
    'gym name is editable, versioned, queued for sync and audited',
    () async {
      final r = await setup();
      final updated = await gyms.updateProfile(
        gymId: r.gymId,
        actorId: r.ownerId,
        input: const GymProfileInput(name: 'Energize Fitness'),
      );
      expect(updated.name, 'Energize Fitness');
      expect(updated.version, 2);
      final ops = await db.select(db.syncOperations).get();
      final update = ops.firstWhere((o) => o.operation == 'update');
      expect(update.baseVersion, 1);
      expect(update.status, 'pending');
      expect(
        (await db.select(db.auditLogs).get())
            .map((a) => a.action)
            .contains('gym.profile_updated'),
        isTrue,
      );
    },
  );

  test('profile update validates UPI and rejects bad input', () async {
    final r = await setup();
    await expectLater(
      gyms.updateProfile(
        gymId: r.gymId,
        actorId: r.ownerId,
        input: const GymProfileInput(name: 'X', upiId: 'bad'),
      ),
      throwsA(isA<ValidationException>()),
    );
  });

  test('idempotency keys are unique per outbox operation', () async {
    final r = await setup();
    await gyms.updateProfile(
      gymId: r.gymId,
      actorId: r.ownerId,
      input: const GymProfileInput(name: 'A'),
    );
    await gyms.updateProfile(
      gymId: r.gymId,
      actorId: r.ownerId,
      input: const GymProfileInput(name: 'B'),
    );
    final keys = (await db.select(db.syncOperations).get()).map(
      (o) => o.idempotencyKey,
    );
    expect(keys.toSet().length, keys.length);
  });
}
