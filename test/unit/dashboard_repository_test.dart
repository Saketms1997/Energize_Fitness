import 'package:flutter_test/flutter_test.dart';
import 'package:gym_management_system/models/enums.dart';

import '../support/test_env.dart';

void main() {
  late TestEnv env;
  setUp(() async => env = await TestEnv.create()); // 2026-03-10 12:00 IST
  tearDown(() => env.dispose());

  test('empty gym shows zeros', () async {
    final d = await env.dashboard.load(env.gymId);
    expect(d.totalMembers, 0);
    expect(d.activeMembers, 0);
    expect(d.todayCollectionPaise, 0);
    expect(d.pendingPaise, 0);
    expect(d.upcomingExpiries, isEmpty);
  });

  test('pending money is never counted as collected revenue', () async {
    final a = await env.register();
    final d = await env.dashboard.load(env.gymId);
    expect(d.totalMembers, 1);
    expect(d.activeMembers, 0);
    expect(d.todayCollectionPaise, 0);
    expect(d.monthRevenuePaise, 0);
    expect(d.pendingPaise, 100000);

    // A recorded-but-unverified payment is still not revenue.
    await env.payments.recordPayment(
      actorId: env.ownerId,
      membershipId: a.membership.id,
      amountPaise: 100000,
      method: PaymentMethod.cash,
    );
    final d2 = await env.dashboard.load(env.gymId);
    expect(d2.todayCollectionPaise, 0);
    expect(d2.pendingPaise, 100000);
  });

  test('verified payments count; partial leaves the balance pending', () async {
    final a = await env.register();
    await env.pay(a.membership.id, 40000);
    final d = await env.dashboard.load(env.gymId);
    expect(d.todayCollectionPaise, 40000);
    expect(d.monthRevenuePaise, 40000);
    expect(d.pendingPaise, 60000);
    expect(d.activeMembers, 0);
  });

  test('full payment activates and refunds leave the totals', () async {
    final a = await env.register();
    final p = await env.pay(a.membership.id, 100000);
    var d = await env.dashboard.load(env.gymId);
    expect(d.activeMembers, 1);
    expect(d.todayCollectionPaise, 100000);
    expect(d.pendingPaise, 0);

    await env.payments.refundPayment(
      paymentId: p.id,
      actorId: env.ownerId,
      reason: 'Member moved away',
      cancelMembership: true,
    );
    d = await env.dashboard.load(env.gymId);
    expect(d.todayCollectionPaise, 0);
    expect(d.monthRevenuePaise, 0);
    expect(d.activeMembers, 0);
    expect(d.pendingPaise, 0); // cancelled memberships owe nothing
  });

  test('today and month boundaries follow IST, not UTC', () async {
    final a = await env.register();
    // 23:45 IST on 31 Mar == 18:15 UTC the same day.
    env.clock.t = DateTime.utc(2026, 3, 31, 18, 15);
    await env.pay(a.membership.id, 100000);
    // 00:10 IST on 1 Apr == 18:40 UTC on 31 Mar.
    env.clock.t = DateTime.utc(2026, 3, 31, 18, 40);
    final d = await env.dashboard.load(env.gymId);
    expect(d.todayCollectionPaise, 0, reason: 'payment was "yesterday" in IST');
    expect(d.monthRevenuePaise, 0, reason: 'payment was in March, now April');
  });

  test('expired and upcoming-expiry counts', () async {
    final a = await env.register(); // ends 9 Apr
    await env.pay(a.membership.id, 100000);
    final b = await env.register(
      name: 'Meena Rao',
      phone: '9988776655',
      start: DateTime.utc(2026, 1, 1), // ended 31 Jan
    );
    await env.pay(b.membership.id, 100000);

    env.clock.t = DateTime.utc(2026, 4, 5, 6); // 4 days before Ravi's end
    final d = await env.dashboard.load(env.gymId);
    expect(d.activeMembers, 1);
    expect(d.expiredMembers, 1);
    expect(d.upcomingExpiries, hasLength(1));
    expect(d.upcomingExpiries.single.member.fullName, 'Ravi Kumar');
    expect(d.upcomingExpiries.single.daysLeft, 4);
  });

  test('new registrations counts the current month only', () async {
    await env.register();
    env.clock.t = DateTime.utc(2026, 4, 2, 6);
    await env.register(name: 'Meena Rao', phone: '9988776655');
    final d = await env.dashboard.load(env.gymId);
    expect(d.totalMembers, 2);
    expect(d.newRegistrationsThisMonth, 1);
  });
}
