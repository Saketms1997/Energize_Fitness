import 'package:flutter_test/flutter_test.dart';
import 'package:gym_management_system/repositories/gym_repository.dart';
import 'package:gym_management_system/repositories/plan_repository.dart';
import 'package:gym_management_system/repositories/support.dart';

import '../support/test_env.dart';

void main() {
  late TestEnv env;
  late String memberId;
  late String firstId;
  setUp(() async {
    env = await TestEnv.create(); // 2026-03-10 IST
    final r = await env.register();
    memberId = r.member.id;
    firstId = r.membership.id;
    await env.pay(firstId, 100000); // active 10 Mar - 9 Apr
  });
  tearDown(() => env.dispose());

  Future<List<String>> history() async =>
      (await env.members.detail(memberId))!.memberships
          .map((m) => '${m.startDate.month}/${m.startDate.day}:${m.status}')
          .toList();

  test(
    'renewal while active starts the day after the current one ends',
    () async {
      final renewal = await env.memberships.renew(
        memberId: memberId,
        actorId: env.ownerId,
        planId: env.monthly.id,
      );
      expect(renewal.startDate.toUtc(), DateTime.utc(2026, 4, 10));
      expect(renewal.endDate.toUtc(), DateTime.utc(2026, 5, 9));
      expect(renewal.status, 'pending_payment');
      expect(renewal.previousMembershipId, firstId);
    },
  );

  test('renewal after lapse starts today', () async {
    env.clock.t = DateTime.utc(2026, 6, 1, 6);
    final renewal = await env.memberships.renew(
      memberId: memberId,
      actorId: env.ownerId,
      planId: env.monthly.id,
    );
    expect(renewal.startDate.toUtc(), DateTime.utc(2026, 6, 1));
  });

  test(
    'renewing keeps previous membership and payment history intact',
    () async {
      final before = await env.payments.verifiedPaidPaise(firstId);
      final renewal = await env.memberships.renew(
        memberId: memberId,
        actorId: env.ownerId,
        planId: env.monthly.id,
      );
      await env.pay(renewal.id, 100000);
      final detail = (await env.members.detail(memberId))!;
      expect(detail.memberships, hasLength(2));
      expect(detail.payments, hasLength(2));
      expect(await env.payments.verifiedPaidPaise(firstId), before);
      expect(await history(), ['4/10:active', '3/10:active']);
    },
  );

  test('only one unpaid renewal at a time (double-click safe)', () async {
    await env.memberships.renew(
      memberId: memberId,
      actorId: env.ownerId,
      planId: env.monthly.id,
    );
    await expectLater(
      env.memberships.renew(
        memberId: memberId,
        actorId: env.ownerId,
        planId: env.monthly.id,
      ),
      throwsA(isA<DomainException>()),
    );
  });

  test('plan price changes do not rewrite sold memberships', () async {
    await env.plans.updatePlan(
      planId: env.monthly.id,
      expectedVersion: 1,
      actorId: env.ownerId,
      input: const PlanInput(
        name: 'Monthly',
        durationUnit: 'months',
        durationValue: 1,
        pricePaise: 150000,
      ),
    );
    final ms = (await env.members.detail(memberId))!.memberships.single;
    expect(ms.pricePaise, 100000);
  });

  test(
    'freeze then unfreeze extends the end date by the frozen days',
    () async {
      env.clock.t = DateTime.utc(2026, 3, 20, 6); // day 10 of membership
      final frozen = await env.memberships.freeze(
        membershipId: firstId,
        actorId: env.ownerId,
        reason: 'Travelling',
      );
      expect(frozen.status, 'frozen');
      env.clock.t = DateTime.utc(2026, 3, 30, 6); // 10 days later
      final back = await env.memberships.unfreeze(
        membershipId: firstId,
        actorId: env.ownerId,
      );
      expect(back.status, 'active');
      expect(back.endDate.toUtc(), DateTime.utc(2026, 4, 19)); // 9 Apr + 10
      final freeze = await env.db.select(env.db.membershipFreezes).getSingle();
      expect(freeze.daysFrozen, 10);
      expect(freeze.endDate!.toUtc(), DateTime.utc(2026, 3, 30));
    },
  );

  test(
    'freeze rules: not when expired, not twice, unfreeze needs a freeze',
    () async {
      await expectLater(
        env.memberships.unfreeze(membershipId: firstId, actorId: env.ownerId),
        throwsA(isA<DomainException>()),
      );
      await env.memberships.freeze(
        membershipId: firstId,
        actorId: env.ownerId,
        reason: 'Injury',
      );
      await expectLater(
        env.memberships.freeze(
          membershipId: firstId,
          actorId: env.ownerId,
          reason: 'Again',
        ),
        throwsA(isA<DomainException>()),
      );
    },
  );

  test('an expired membership cannot be frozen', () async {
    env.clock.t = DateTime.utc(2026, 7, 1);
    await expectLater(
      env.memberships.freeze(
        membershipId: firstId,
        actorId: env.ownerId,
        reason: 'Too late',
      ),
      throwsA(isA<DomainException>()),
    );
  });

  test('extension adds days with an audited reason', () async {
    final m = await env.memberships.extend(
      membershipId: firstId,
      actorId: env.ownerId,
      days: 5,
      reason: 'Gym closed for repairs',
    );
    expect(m.endDate.toUtc(), DateTime.utc(2026, 4, 14));
    await expectLater(
      env.memberships.extend(
        membershipId: firstId,
        actorId: env.ownerId,
        days: 0,
        reason: 'x',
      ),
      throwsA(isA<ValidationException>()),
    );
  });

  test('cancel records the reason and is idempotent', () async {
    final m = await env.memberships.cancel(
      membershipId: firstId,
      actorId: env.ownerId,
      reason: 'Relocated',
    );
    expect(m.status, 'cancelled');
    expect(m.statusReason, 'Relocated');
    final again = await env.memberships.cancel(
      membershipId: firstId,
      actorId: env.ownerId,
      reason: 'Relocated',
    );
    expect(again.version, m.version);
  });

  test('activate-with-balance is an audited owner override', () async {
    env.clock.t = DateTime.utc(2026, 6, 1, 6);
    final renewal = await env.memberships.renew(
      memberId: memberId,
      actorId: env.ownerId,
      planId: env.monthly.id,
    );
    await env.pay(renewal.id, 30000);
    await expectLater(
      env.memberships.activateWithBalance(
        membershipId: renewal.id,
        actorId: env.ownerId,
        reason: 'no',
      ),
      throwsA(isA<ValidationException>()),
    );
    final m = await env.memberships.activateWithBalance(
      membershipId: renewal.id,
      actorId: env.ownerId,
      reason: 'Will pay remainder on Friday',
    );
    expect(m.status, 'active');
    final audit = (await env.db.select(env.db.auditLogs).get()).firstWhere(
      (a) => a.action == 'membership.activated_with_balance',
    );
    expect(audit.reason, 'Will pay remainder on Friday');
    // The unpaid 70,000 paise remains visible as pending money.
    final dash = await env.dashboard.load(env.gymId);
    expect(dash.pendingPaise, 70000);
  });
}
