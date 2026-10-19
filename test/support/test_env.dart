import 'package:gym_management_system/core/clock.dart';
import 'package:gym_management_system/core/security/password_hasher.dart';
import 'package:gym_management_system/database/app_database.dart';
import 'package:gym_management_system/models/enums.dart';
import 'package:gym_management_system/repositories/auth_repository.dart';
import 'package:gym_management_system/repositories/dashboard_repository.dart';
import 'package:gym_management_system/repositories/gym_repository.dart';
import 'package:gym_management_system/repositories/member_repository.dart';
import 'package:gym_management_system/repositories/membership_repository.dart';
import 'package:gym_management_system/repositories/payment_repository.dart';
import 'package:gym_management_system/repositories/plan_repository.dart';

class FakeClock implements Clock {
  FakeClock(this.t);
  DateTime t;
  @override
  DateTime now() => t;
  void advance(Duration d) => t = t.add(d);
}

/// In-memory database with a provisioned gym/owner and all repositories.
class TestEnv {
  TestEnv._(this.db, this.clock);

  final AppDatabase db;
  final FakeClock clock;
  late final String gymId;
  late final String ownerId;
  late final AuthRepository auth;
  late final PlanRepository plans;
  late final MemberRepository members;
  late final MembershipRepository memberships;
  late final PaymentRepository payments;
  late final DashboardRepository dashboard;
  late final MembershipPlan monthly;

  /// 2026-03-10 12:00 IST by default.
  static Future<TestEnv> create({DateTime? now}) async {
    final env = TestEnv._(
      AppDatabase.inMemory(),
      FakeClock(now ?? DateTime.utc(2026, 3, 10, 6, 30)),
    );
    env.auth = AuthRepository(
      env.db,
      env.clock,
      PasswordHasher(iterations: 100),
    );
    final r = await env.auth.provisionOwner(
      gym: const GymProfileInput(name: 'Iron Temple'),
      ownerName: 'Asha',
      email: 'owner@example.com',
      password: 'strongpass123',
      deviceName: 'test',
      platform: 'test',
    );
    env.gymId = r.gymId;
    env.ownerId = r.ownerId;
    env.plans = PlanRepository(env.db, env.clock);
    env.members = MemberRepository(env.db, env.clock);
    env.memberships = MembershipRepository(env.db, env.clock);
    env.payments = PaymentRepository(env.db, env.clock);
    env.dashboard = DashboardRepository(env.db, env.clock);
    env.monthly = await env.plans.createPlan(
      gymId: env.gymId,
      actorId: env.ownerId,
      input: const PlanInput(
        name: 'Monthly',
        durationUnit: 'months',
        durationValue: 1,
        pricePaise: 100000,
      ),
    );
    return env;
  }

  MemberInput memberInput({
    String name = 'Ravi Kumar',
    String phone = '9876543210',
    String? alt,
    DateTime? dob,
    bool consent = true,
  }) => MemberInput(
    fullName: name,
    phone: phone,
    altPhone: alt,
    dateOfBirth: dob ?? DateTime.utc(1995, 5, 20),
    emergencyName: 'Sita',
    emergencyPhone: '9123456780',
    whatsappConsent: consent,
  );

  Future<RegistrationResult> register({
    String name = 'Ravi Kumar',
    String phone = '9876543210',
    String? override,
    int? price,
    DateTime? start,
  }) => members.registerMember(
    gymId: gymId,
    actorId: ownerId,
    input: memberInput(name: name, phone: phone),
    planId: monthly.id,
    startDate: start ?? DateTime.utc(2026, 3, 10),
    pricePaise: price,
    duplicateOverrideReason: override,
  );

  /// Records and verifies a payment in one go.
  Future<Payment> pay(
    String membershipId,
    int amount, {
    String? ref,
    PaymentMethod method = PaymentMethod.cash,
  }) async {
    final p = await payments.recordPayment(
      actorId: ownerId,
      membershipId: membershipId,
      amountPaise: amount,
      method: method,
      reference: ref,
    );
    return payments.verifyPayment(
      paymentId: p.id,
      actorId: ownerId,
      confirmedAmountPaise: amount,
    );
  }

  Future<void> dispose() => db.close();
}
