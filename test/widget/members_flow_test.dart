import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gym_management_system/core/providers.dart';
import 'package:gym_management_system/core/router.dart';
import 'package:gym_management_system/main.dart';
import 'package:gym_management_system/services/session_controller.dart';

import '../support/test_env.dart';

void main() {
  late TestEnv env;

  Future<void> settle(WidgetTester t) async {
    await t.runAsync(
      () => Future<void>.delayed(const Duration(milliseconds: 80)),
    );
    await t.pumpAndSettle();
  }

  Future<ProviderContainer> launch(WidgetTester t) async {
    await t.binding.setSurfaceSize(const Size(1200, 2200));
    await t.pumpWidget(
      ProviderScope(
        overrides: [
          databaseProvider.overrideWithValue(env.db),
          clockProvider.overrideWithValue(env.clock),
        ],
        child: const GymApp(),
      ),
    );
    await settle(t);
    final c = ProviderScope.containerOf(t.element(find.byType(GymApp)));
    c
        .read(sessionProvider.notifier)
        .signIn(ownerId: env.ownerId, gymId: env.gymId, ownerName: 'Asha');
    await settle(t);
    return c;
  }

  Future<void> go(WidgetTester t, ProviderContainer c, String path) async {
    c.read(routerProvider).go(path);
    await settle(t);
  }

  Future<void> tapKey(WidgetTester t, String key) async {
    await t.ensureVisible(find.byKey(Key(key)));
    await t.tap(find.byKey(Key(key)));
    await settle(t);
  }

  setUp(() async => env = await TestEnv.create());

  Future<void> fillRegistration(
    WidgetTester t, {
    required String name,
    required String phone,
  }) async {
    await t.enterText(find.byKey(const Key('regName')), name);
    await t.enterText(find.byKey(const Key('regPhone')), phone);
    await t.enterText(find.byKey(const Key('regAge')), '30');
    await t.ensureVisible(find.byKey(const Key('regPlan')));
    await t.tap(find.byKey(const Key('regPlan')));
    await settle(t);
    await t.tap(find.textContaining('Monthly').last);
    await settle(t);
  }

  testWidgets('register, record payment, verify -> membership active', (
    t,
  ) async {
    final c = await launch(t);
    await go(t, c, Routes.newMember);
    await fillRegistration(t, name: 'Meena Rao', phone: '9811122233');
    await tapKey(t, 'regSubmit');

    expect(find.byKey(const Key('detailName')), findsOneWidget);
    expect(find.text('Meena Rao'), findsOneWidget);
    expect(find.text('Awaiting payment'), findsWidgets);

    final membership = (await env.db.select(env.db.memberships).get()).single;
    await t.tap(find.byKey(Key('membershipMenu-${membership.id}')));
    await settle(t);
    await t.tap(find.text('Record payment'));
    await settle(t);
    await t.tap(find.byKey(const Key('payMethod')));
    await settle(t);
    await t.tap(find.text('CASH').last);
    await settle(t);
    await tapKey(t, 'payConfirm');

    final payment = (await env.db.select(env.db.payments).get()).single;
    expect(payment.status, 'pending');

    // Wrong confirmation amount is refused and the dialog stays open.
    await tapKey(t, 'verify-${payment.id}');
    await t.enterText(find.byKey(const Key('verifyAmount')), '500');
    await tapKey(t, 'verifyConfirm');
    expect(find.byKey(const Key('dialogError')), findsOneWidget);
    expect(
      (await env.db.select(env.db.payments).get()).single.status,
      'pending',
    );

    await t.enterText(find.byKey(const Key('verifyAmount')), '1000');
    await tapKey(t, 'verifyConfirm');
    expect(
      (await env.db.select(env.db.payments).get()).single.status,
      'verified',
    );
    expect(find.text('Active'), findsWidgets);
    await env.dispose();
  });

  testWidgets('duplicate phone shows dialog; override needs a reason', (
    t,
  ) async {
    await env.register(name: 'Ravi Kumar', phone: '9876543210');
    final c = await launch(t);
    await go(t, c, Routes.newMember);
    await fillRegistration(t, name: 'Someone Else', phone: '9876543210');
    await tapKey(t, 'regSubmit');

    expect(find.text('Possible duplicate member'), findsOneWidget);
    expect((await env.db.select(env.db.members).get()).length, 1);

    await tapKey(t, 'registerAnyway');
    // Too-short reason is rejected.
    await t.enterText(find.byKey(const Key('reasonField')), 'no');
    await tapKey(t, 'reasonConfirm');
    expect(find.text('Enter at least 5 characters'), findsOneWidget);

    await t.enterText(
      find.byKey(const Key('reasonField')),
      'Spouse, shares phone',
    );
    await tapKey(t, 'reasonConfirm');
    expect((await env.db.select(env.db.members).get()).length, 2);
    await env.dispose();
  });

  testWidgets('plans screen creates a plan', (t) async {
    final c = await launch(t);
    await go(t, c, Routes.plans);
    expect(find.byKey(const Key('plan-Monthly')), findsOneWidget);

    await tapKey(t, 'addPlan');
    await t.enterText(find.byKey(const Key('planName')), 'Quarterly');
    await t.enterText(find.byKey(const Key('planPrice')), '2500');
    await tapKey(t, 'planSave');

    expect(find.byKey(const Key('plan-Quarterly')), findsOneWidget);
    final plans = await env.plans.listPlans();
    expect(plans.map((p) => p.name), containsAll(['Monthly', 'Quarterly']));
    await env.dispose();
  });

  testWidgets('UPI QR appears when the gym has a UPI ID', (t) async {
    await env.db.customStatement(
      "UPDATE gyms SET upi_id = 'irontemple@okbank', upi_payee_name = 'Iron Temple'",
    );
    final r = await env.register();
    final c = await launch(t);
    await go(t, c, Routes.member(r.member.id));
    await t.tap(find.byKey(Key('membershipMenu-${r.membership.id}')));
    await settle(t);
    await t.tap(find.text('Record payment'));
    await settle(t);
    expect(find.byKey(const Key('upiQr')), findsOneWidget);
    expect(find.text('Scan to pay irontemple@okbank'), findsOneWidget);
    await env.dispose();
  });

  testWidgets('QR hint shown when UPI ID is missing', (t) async {
    final r = await env.register();
    final c = await launch(t);
    await go(t, c, Routes.member(r.member.id));
    await t.tap(find.byKey(Key('membershipMenu-${r.membership.id}')));
    await settle(t);
    await t.tap(find.text('Record payment'));
    await settle(t);
    expect(find.byKey(const Key('upiQr')), findsNothing);
    expect(find.byKey(const Key('upiMissing')), findsOneWidget);
    await env.dispose();
  });

  testWidgets('messages screen: create reminders, mark sent manually', (
    t,
  ) async {
    final r = await env.register();
    env.clock.t = DateTime.utc(2026, 3, 11, 6, 30); // unpaid > 1 day
    final c = await launch(t);
    await go(t, c, Routes.messages);
    await tapKey(t, 'generateReminders');
    expect(find.textContaining('balance of'), findsOneWidget);
    expect(find.text('Queued'), findsOneWidget);

    final msg = (await env.db.select(env.db.whatsappMessages).get()).single;
    await tapKey(t, 'manualSent-${msg.id}');
    expect(find.text('Sent'), findsOneWidget);
    expect(r.member.id, msg.memberId);
    await env.dispose();
  });

  testWidgets('attendance: check in by phone, blocked when unpaid, undo', (
    t,
  ) async {
    final paid = await env.register(name: 'Paid Pat', phone: '9811111111');
    await env.pay(paid.membership.id, 100000);
    await env.register(name: 'Unpaid Uma', phone: '9822222222');
    final c = await launch(t);
    await go(t, c, Routes.attendance);

    await t.enterText(find.byKey(const Key('checkInQuery')), '9811111111');
    await tapKey(t, 'checkInButton');
    expect(find.text('Welcome, Paid Pat!'), findsOneWidget);
    expect(find.textContaining('1 check-in'), findsOneWidget);

    await t.enterText(find.byKey(const Key('checkInQuery')), '9811111111');
    await tapKey(t, 'checkInButton');
    expect(find.text('Paid Pat already checked in today.'), findsOneWidget);

    await t.enterText(find.byKey(const Key('checkInQuery')), '9822222222');
    await tapKey(t, 'checkInButton');
    expect(find.text('Let Unpaid Uma in anyway?'), findsOneWidget);
    await t.tap(find.text('Cancel'));
    await settle(t);
    expect(find.textContaining('1 check-in'), findsOneWidget);

    await t.enterText(find.byKey(const Key('checkInQuery')), 'nobody');
    await tapKey(t, 'checkInButton');
    expect(find.textContaining('No member found'), findsOneWidget);

    await t.tap(find.text('Undo'));
    await settle(t);
    await t.enterText(find.byKey(const Key('reasonField')), 'wrong person');
    await tapKey(t, 'reasonConfirm');
    expect(find.textContaining('0 check-ins'), findsOneWidget);
    await env.dispose();
  });

  testWidgets('trainers: add one and assign to a member', (t) async {
    final r = await env.register();
    final c = await launch(t);
    await go(t, c, Routes.trainers);
    await tapKey(t, 'addTrainer');
    await t.enterText(find.byKey(const Key('trainerName')), 'Coach Dev');
    await tapKey(t, 'trainerSave');
    expect(find.byKey(const Key('trainer-Coach Dev')), findsOneWidget);

    await go(t, c, Routes.member(r.member.id));
    await tapKey(t, 'assignTrainer');
    await t.tap(find.byKey(const Key('assignPicker')));
    await settle(t);
    await t.tap(find.text('Coach Dev').last);
    await settle(t);
    await tapKey(t, 'assignConfirm');
    expect(find.text('Trainer: Coach Dev'), findsOneWidget);
    await env.dispose();
  });
}
