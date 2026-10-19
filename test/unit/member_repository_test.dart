import 'package:flutter_test/flutter_test.dart';
import 'package:gym_management_system/repositories/gym_repository.dart';
import 'package:gym_management_system/repositories/member_repository.dart';
import 'package:gym_management_system/repositories/plan_repository.dart';
import 'package:gym_management_system/repositories/support.dart';

import '../support/test_env.dart';

void main() {
  late TestEnv env;
  setUp(() async => env = await TestEnv.create());
  tearDown(() => env.dispose());

  test('registration creates member + pending membership atomically', () async {
    final r = await env.register();
    expect(r.member.memberCode, 'M000001');
    expect(r.member.phone, '9876543210');
    expect(r.member.whatsappConsent, isTrue);
    expect(r.member.whatsappConsentAt, isNotNull);
    expect(r.membership.status, 'pending_payment');
    expect(r.membership.planName, 'Monthly');
    expect(r.membership.pricePaise, 100000);
    expect(r.membership.startDate.toUtc(), DateTime.utc(2026, 3, 10));
    expect(r.membership.endDate.toUtc(), DateTime.utc(2026, 4, 9));

    final ops = await env.db.select(env.db.syncOperations).get();
    final tables = ops.map((o) => o.entityTable);
    expect(tables, containsAll(['members', 'memberships']));
    final audits = (await env.db.select(env.db.auditLogs).get()).map(
      (a) => a.action,
    );
    expect(audits, contains('member.registered'));
  });

  test('member codes increase per registration', () async {
    await env.register();
    final second = await env.register(name: 'Meena Rao', phone: '9988776655');
    expect(second.member.memberCode, 'M000002');
  });

  test('a failed registration writes nothing', () async {
    await expectLater(
      env.members.registerMember(
        gymId: env.gymId,
        actorId: env.ownerId,
        input: env.memberInput(phone: '12345'),
        planId: env.monthly.id,
        startDate: DateTime.utc(2026, 3, 10),
      ),
      throwsA(isA<ValidationException>()),
    );
    expect(await env.db.select(env.db.members).get(), isEmpty);
    expect(await env.db.select(env.db.memberships).get(), isEmpty);
  });

  test('validation: phone, dob/age, future dob, emergency pair', () async {
    final today = DateTime.utc(2026, 3, 10);
    expect(
      const MemberInput(fullName: '', phone: 'x').validate(today).keys,
      containsAll(['fullName', 'phone', 'dateOfBirth']),
    );
    expect(
      MemberInput(
        fullName: 'A',
        phone: '9876543210',
        dateOfBirth: DateTime.utc(2027),
      ).validate(today)['dateOfBirth'],
      contains('future'),
    );
    expect(
      const MemberInput(
        fullName: 'A',
        phone: '9876543210',
        ageYears: 30,
        emergencyName: 'X',
      ).validate(today).keys,
      contains('emergencyName'),
    );
    expect(
      const MemberInput(
        fullName: 'A',
        phone: '9876543210',
        ageYears: 30,
      ).validate(today),
      isEmpty,
    );
  });

  test('an unavailable or inactive plan is rejected', () async {
    final plan = await env.plans.updatePlan(
      planId: env.monthly.id,
      expectedVersion: 1,
      actorId: env.ownerId,
      input: const PlanInput(
        name: 'Monthly',
        durationUnit: 'months',
        durationValue: 1,
        pricePaise: 100000,
      ),
      isActive: false,
    );
    expect(plan.isActive, isFalse);
    await expectLater(env.register(), throwsA(isA<DomainException>()));
  });

  group('duplicates', () {
    test('same phone blocks until the owner gives a reason', () async {
      await env.register();
      await expectLater(
        env.register(name: 'Someone Else'),
        throwsA(
          isA<DuplicateMemberException>().having(
            (e) => e.candidates.single.reason,
            'reason',
            'same_phone',
          ),
        ),
      );
      expect(await env.db.select(env.db.members).get(), hasLength(1));

      await expectLater(
        env.register(name: 'Someone Else', override: 'ok'), // too short
        throwsA(isA<DuplicateMemberException>()),
      );
      final r = await env.register(
        name: 'Someone Else',
        override: 'Brother shares the family phone',
      );
      expect(r.member.memberCode, 'M000002');
      final audit = (await env.db.select(env.db.auditLogs).get()).firstWhere(
        (a) => a.action == 'member.duplicate_override',
      );
      expect(audit.reason, 'Brother shares the family phone');
    });

    test('same name is flagged even with a different phone', () async {
      await env.register();
      await expectLater(
        env.register(name: '  ravi   kumar ', phone: '9000000001'),
        throwsA(
          isA<DuplicateMemberException>().having(
            (e) => e.candidates.single.reason,
            'reason',
            'same_name_and_dob',
          ),
        ),
      );
    });

    test('different name and phone is not a duplicate', () async {
      await env.register();
      final r = await env.register(name: 'Meena Rao', phone: '9000000001');
      expect(r.member.memberCode, 'M000002');
    });
  });

  group('search', () {
    setUp(() async {
      await env.register();
      await env.register(name: 'Meena Rao', phone: '9988776655');
    });

    test('by name, phone digits and member code', () async {
      Future<List<String>> names(String q) async =>
          (await env.members.summaries(
            env.gymId,
            query: q,
          )).map((s) => s.member.fullName).toList();
      expect(await names('meena'), ['Meena Rao']);
      expect(await names('98765'), ['Ravi Kumar']);
      expect(await names('+91 99887'), ['Meena Rao']);
      expect(await names('m000002'), ['Meena Rao']);
      expect(await names(''), hasLength(2));
      expect(await names('zzz'), isEmpty);
    });

    test('status filter', () async {
      final all = await env.members.summaries(env.gymId);
      expect(all.every((s) => s.status == 'pending_payment'), isTrue);
      expect(await env.members.summaries(env.gymId, status: 'active'), isEmpty);
    });
  });

  group('updates', () {
    test('changes phone, bumps version, queues sync, audits', () async {
      final r = await env.register();
      final updated = await env.members.updateMember(
        memberId: r.member.id,
        expectedVersion: 1,
        actorId: env.ownerId,
        input: env.memberInput(phone: '+91 91111 22222'),
      );
      expect(updated.phone, '9111122222');
      expect(updated.version, 2);
      final op = (await env.db.select(env.db.syncOperations).get()).firstWhere(
        (o) => o.entityTable == 'members' && o.operation == 'update',
      );
      expect(op.baseVersion, 1);
    });

    test('stale version is rejected (optimistic concurrency)', () async {
      final r = await env.register();
      await env.members.updateMember(
        memberId: r.member.id,
        expectedVersion: 1,
        actorId: env.ownerId,
        input: env.memberInput(phone: '9111122222'),
      );
      await expectLater(
        env.members.updateMember(
          memberId: r.member.id,
          expectedVersion: 1, // stale
          actorId: env.ownerId,
          input: env.memberInput(phone: '9333344444'),
        ),
        throwsA(isA<ConcurrentModificationException>()),
      );
      final m = (await env.members.detail(r.member.id))!.member;
      expect(m.phone, '9111122222');
    });

    test('moving a phone onto another member needs a reason', () async {
      await env.register();
      final other = await env.register(name: 'Meena Rao', phone: '9988776655');
      await expectLater(
        env.members.updateMember(
          memberId: other.member.id,
          expectedVersion: 1,
          actorId: env.ownerId,
          input: env.memberInput(name: 'Meena Rao', phone: '9876543210'),
        ),
        throwsA(isA<DuplicateMemberException>()),
      );
    });
  });

  test('viewing medical notes is audited', () async {
    final r = await env.members.registerMember(
      gymId: env.gymId,
      actorId: env.ownerId,
      input: const MemberInput(
        fullName: 'Ravi Kumar',
        phone: '9876543210',
        ageYears: 30,
        medicalNotes: ' Asthma ',
      ),
      planId: env.monthly.id,
      startDate: DateTime.utc(2026, 3, 10),
    );
    final notes = await env.members.revealMedicalNotes(
      memberId: r.member.id,
      actorId: env.ownerId,
    );
    expect(notes, 'Asthma');
    final actions = (await env.db.select(env.db.auditLogs).get()).map(
      (a) => a.action,
    );
    expect(actions, contains('member.medical_notes_viewed'));
  });
}
