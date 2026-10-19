import 'package:flutter_test/flutter_test.dart';
import 'package:gym_management_system/repositories/attendance_repository.dart';
import 'package:gym_management_system/repositories/gym_repository.dart';
import 'package:gym_management_system/repositories/support.dart';
import 'package:gym_management_system/repositories/trainer_repository.dart';

import '../support/test_env.dart';

void main() {
  late TestEnv env;
  late AttendanceRepository att;
  late TrainerRepository trainers;

  setUp(() async {
    env = await TestEnv.create();
    att = AttendanceRepository(env.db, env.clock);
    trainers = TrainerRepository(env.db, env.clock);
  });
  tearDown(() => env.dispose());

  Future<String> activeMember({
    String name = 'Ravi Kumar',
    String phone = '9876543210',
  }) async {
    final r = await env.register(name: name, phone: phone);
    await env.pay(r.membership.id, 100000);
    return r.member.id;
  }

  group('attendance', () {
    test(
      'active member checks in; second tap the same day is a no-op',
      () async {
        final id = await activeMember();
        final a = await att.checkIn(memberId: id, actorId: env.ownerId);
        expect(a.alreadyIn, isFalse);
        final b = await att.checkIn(memberId: id, actorId: env.ownerId);
        expect(b.alreadyIn, isTrue);
        expect(b.record.id, a.record.id);
        expect(await att.today(env.gymId), hasLength(1));
        final ops = await env.db.select(env.db.syncOperations).get();
        expect(
          ops.where((o) => o.entityTable == 'attendance_records'),
          hasLength(1),
        );
      },
    );

    test('blocked for unpaid, frozen, expired and cancelled members', () async {
      final unpaid = await env.register(name: 'Unpaid U', phone: '9000000001');
      await expectLater(
        att.checkIn(memberId: unpaid.member.id, actorId: env.ownerId),
        throwsA(
          isA<DomainException>().having(
            (e) => e.message,
            'message',
            contains('waiting for payment'),
          ),
        ),
      );

      final frozen = await activeMember(name: 'Frozen F', phone: '9000000002');
      final fm = (await env.db.select(env.db.memberships).get()).firstWhere(
        (m) => m.memberId == frozen,
      );
      await env.memberships.freeze(
        membershipId: fm.id,
        actorId: env.ownerId,
        reason: 'travel',
      );
      await expectLater(
        att.checkIn(memberId: frozen, actorId: env.ownerId),
        throwsA(
          isA<DomainException>().having(
            (e) => e.message,
            'message',
            contains('frozen'),
          ),
        ),
      );

      final old = await activeMember(name: 'Old O', phone: '9000000003');
      env.clock.t = DateTime.utc(2026, 6, 1, 6, 30);
      await expectLater(
        att.checkIn(memberId: old, actorId: env.ownerId),
        throwsA(
          isA<DomainException>().having(
            (e) => e.message,
            'message',
            contains('expired'),
          ),
        ),
      );
      expect(await att.today(env.gymId), isEmpty);
    });

    test('override needs a written reason and is audited', () async {
      final r = await env.register();
      await expectLater(
        att.checkIn(
          memberId: r.member.id,
          actorId: env.ownerId,
          overrideReason: 'ok',
        ),
        throwsA(isA<DomainException>()),
      );
      final res = await att.checkIn(
        memberId: r.member.id,
        actorId: env.ownerId,
        overrideReason: 'Paying cash tomorrow',
      );
      expect(res.alreadyIn, isFalse);
      final log = (await env.db.select(env.db.auditLogs).get()).where(
        (l) => l.action == 'attendance.override_checked_in',
      );
      expect(log.single.reason, 'Paying cash tomorrow');
    });

    test('the gym day rolls over at IST midnight, not UTC', () async {
      final id = await activeMember();
      env.clock.t = DateTime.utc(2026, 3, 10, 18, 0); // 23:30 IST on the 10th
      await att.checkIn(memberId: id, actorId: env.ownerId);
      env.clock.t = DateTime.utc(2026, 3, 10, 19, 0); // 00:30 IST on the 11th
      final next = await att.checkIn(memberId: id, actorId: env.ownerId);
      expect(next.alreadyIn, isFalse, reason: 'new local day, new check-in');
      expect(await att.history(id), hasLength(2));
    });

    test(
      'undo removes today, can re-check-in, cannot undo yesterday',
      () async {
        final id = await activeMember();
        final a = await att.checkIn(memberId: id, actorId: env.ownerId);
        await expectLater(
          att.undo(attendanceId: a.record.id, actorId: env.ownerId, reason: ''),
          throwsA(isA<ValidationException>()),
        );
        await att.undo(
          attendanceId: a.record.id,
          actorId: env.ownerId,
          reason: 'wrong person',
        );
        expect(await att.today(env.gymId), isEmpty);
        final again = await att.checkIn(memberId: id, actorId: env.ownerId);
        expect(again.alreadyIn, isFalse);
        expect(again.record.id, a.record.id, reason: 'revived, not duplicated');
        expect(await att.today(env.gymId), hasLength(1));

        env.clock.t = DateTime.utc(2026, 3, 11, 6, 30);
        await expectLater(
          att.undo(
            attendanceId: a.record.id,
            actorId: env.ownerId,
            reason: 'late fix',
          ),
          throwsA(isA<DomainException>()),
        );
      },
    );

    test('lookup by member code or any of the phone formats', () async {
      final id = await activeMember();
      final m = (await att.findMember(env.gymId, '9876543210'))!;
      expect(m.id, id);
      expect((await att.findMember(env.gymId, '+91 98765 43210'))!.id, id);
      expect(
        (await att.findMember(env.gymId, m.memberCode.toLowerCase()))!.id,
        id,
      );
      expect(await att.findMember(env.gymId, 'zzz'), isNull);
      expect(await att.findMember(env.gymId, ''), isNull);
    });
  });

  group('trainers', () {
    test(
      'create validates, assign/clear, inactive trainers take no new members',
      () async {
        await expectLater(
          trainers.create(
            gymId: env.gymId,
            actorId: env.ownerId,
            input: const TrainerInput(name: ' ', phone: '123'),
          ),
          throwsA(isA<ValidationException>()),
        );
        final t = await trainers.create(
          gymId: env.gymId,
          actorId: env.ownerId,
          input: const TrainerInput(
            name: 'Coach Dev',
            phone: '+91 98111 22233',
            specialty: 'Strength',
          ),
        );
        expect(t.phone, '9811122233');

        final a = await env.register(name: 'A', phone: '9000000011');
        final b = await env.register(name: 'B', phone: '9000000012');
        final m = await trainers.assign(
          memberId: a.member.id,
          trainerId: t.id,
          actorId: env.ownerId,
        );
        expect(m.trainerId, t.id);
        expect(m.version, a.member.version + 1);
        expect(await trainers.memberCount(t.id), 1);

        // same assignment again changes nothing
        final same = await trainers.assign(
          memberId: a.member.id,
          trainerId: t.id,
          actorId: env.ownerId,
        );
        expect(same.version, m.version);

        final off = await trainers.update(
          trainerId: t.id,
          expectedVersion: t.version,
          actorId: env.ownerId,
          input: const TrainerInput(name: 'Coach Dev'),
          isActive: false,
        );
        expect(off.isActive, isFalse);
        await expectLater(
          trainers.assign(
            memberId: b.member.id,
            trainerId: t.id,
            actorId: env.ownerId,
          ),
          throwsA(isA<DomainException>()),
        );
        expect(
          (await trainers.listTrainers(env.gymId, activeOnly: true)),
          isEmpty,
        );

        final cleared = await trainers.assign(
          memberId: a.member.id,
          trainerId: null,
          actorId: env.ownerId,
        );
        expect(cleared.trainerId, isNull);
      },
    );

    test(
      'stale edits are rejected; cross-gym trainer ids are not found',
      () async {
        final t = await trainers.create(
          gymId: env.gymId,
          actorId: env.ownerId,
          input: const TrainerInput(name: 'T'),
        );
        await trainers.update(
          trainerId: t.id,
          expectedVersion: 1,
          actorId: env.ownerId,
          input: const TrainerInput(name: 'T2'),
        );
        await expectLater(
          trainers.update(
            trainerId: t.id,
            expectedVersion: 1,
            actorId: env.ownerId,
            input: const TrainerInput(name: 'T3'),
          ),
          throwsA(isA<ConcurrentModificationException>()),
        );
        final r = await env.register();
        await expectLater(
          trainers.assign(
            memberId: r.member.id,
            trainerId: 'not-a-trainer',
            actorId: env.ownerId,
          ),
          throwsA(isA<DomainException>()),
        );
      },
    );
  });
}
