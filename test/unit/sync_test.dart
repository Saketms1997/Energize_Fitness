import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:gym_management_system/database/app_database.dart';
import 'package:gym_management_system/repositories/member_repository.dart';
import 'package:gym_management_system/sync/remote_gateway.dart';
import 'package:gym_management_system/sync/sync_service.dart';

import '../support/fake_gateway.dart';
import '../support/test_env.dart';

class _Device {
  _Device(this.db, this.clock, FakeServer server)
    : members = MemberRepository(db, clock) {
    gateway = FakeGateway(server);
    sync = SyncService(db, clock, gateway);
  }
  final AppDatabase db;
  final FakeClock clock;
  final MemberRepository members;
  late final FakeGateway gateway;
  late final SyncService sync;

  Future<List<SyncOperation>> ops([String? status]) async {
    final all = await db.select(db.syncOperations).get();
    return status == null ? all : all.where((o) => o.status == status).toList();
  }
}

void main() {
  late TestEnv env;
  late FakeServer server;
  late _Device a;

  setUp(() async {
    env = await TestEnv.create();
    server = FakeServer();
    a = _Device(env.db, env.clock, server);
  });
  tearDown(() => env.dispose());

  Future<_Device> secondDevice() async {
    final b = _Device(AppDatabase.inMemory(), FakeClock(env.clock.t), server);
    addTearDown(b.db.close);
    return b;
  }

  MemberInput edit(Member m, {String? name, String? phone}) => MemberInput(
    fullName: name ?? m.fullName,
    phone: phone ?? m.phone,
    dateOfBirth: m.dateOfBirth,
    ageYears: m.ageYears,
    address: m.address,
    emergencyName: m.emergencyName,
    emergencyPhone: m.emergencyPhone,
    whatsappConsent: m.whatsappConsent,
  );

  test(
    'push marks operations synced and the server holds the records',
    () async {
      final r = await env.register();
      await env.pay(r.membership.id, 100000);
      expect(await a.ops('pending'), isNotEmpty);
      expect(await a.sync.pendingCount(env.gymId), greaterThan(0));

      final report = await a.sync.sync(env.gymId);
      expect(report.ok, isTrue, reason: '$report');
      expect(report.pushed, greaterThan(5));
      expect(await a.ops('pending'), isEmpty);
      expect(await a.sync.pendingCount(env.gymId), 0);
      expect(
        server.records.keys,
        containsAll([
          'members/${r.member.id}',
          'memberships/${r.membership.id}',
        ]),
      );
      expect(server.records.keys.any((k) => k.startsWith('payments/')), isTrue);
      expect(a.gateway.claimed, [env.gymId]);
      expect(await a.sync.lastSuccess(env.gymId), isNotNull);
    },
  );

  test(
    'operations arrive in the order they were made, even within one second',
    () async {
      // The fake clock never moves, so every op shares one createdAt value.
      final r = await env.register();
      await env.pay(r.membership.id, 50000);
      await env.pay(r.membership.id, 30000, ref: 'UTR000111');
      await env.pay(r.membership.id, 20000, ref: 'UTR000222');
      expect(
        (await a.ops()).map((o) => o.createdAt).toSet(),
        hasLength(1),
        reason: 'precondition: identical timestamps',
      );
      final report = await a.sync.sync(env.gymId);
      expect(report.conflicts, 0, reason: '$report');
      final lastSeen = <String, int>{};
      for (final (key, version) in server.arrivals) {
        expect(
          version,
          greaterThanOrEqualTo(lastSeen[key] ?? 0),
          reason: 'v$version of $key arrived after a newer one',
        );
        lastSeen[key] = version;
      }
      expect(
        server.records['memberships/${r.membership.id}']!.version,
        greaterThan(1),
      );
    },
  );

  test('sensitive tables never leave the device', () async {
    await env.register();
    await a.sync.sync(env.gymId);
    expect(
      server.records.keys.where((k) => k.startsWith('gym_owners/')),
      isEmpty,
    );
    expect(
      jsonEncode(server.records.values.map((r) => r.payload).toList()),
      isNot(contains('passwordHash')),
    );

    // even a stray outbox row for gym_owners is refused locally, not sent
    await env.db
        .into(env.db.syncOperations)
        .insert(
          SyncOperationsCompanion.insert(
            id: 'stray',
            gymId: env.gymId,
            entityTable: 'gym_owners',
            entityId: env.ownerId,
            operation: 'update',
            payloadJson: '{"passwordHash":"x","version":9}',
            idempotencyKey: 'stray-key',
            createdAt: env.clock.t,
          ),
        );
    await a.sync.sync(env.gymId);
    final stray = (await a.ops()).firstWhere((o) => o.id == 'stray');
    expect(stray.status, 'failed');
    expect(
      server.records.keys.where((k) => k.startsWith('gym_owners/')),
      isEmpty,
    );
  });

  test(
    'offline: nothing lost, retried later, no duplicates on the server',
    () async {
      await env.register();
      server.offline = true;
      final off = await a.sync.sync(env.gymId);
      expect(off.ok, isFalse);
      expect(await a.ops('synced'), isEmpty);
      expect(await a.ops('pending'), isNotEmpty);

      server.offline = false;
      final on = await a.sync.sync(env.gymId);
      expect(on.ok, isTrue);
      expect(await a.ops('pending'), isEmpty);
      expect(
        server.opsSeen,
        (await a.ops()).length,
        reason: 'each op sent exactly once',
      );
    },
  );

  test(
    'transient server error keeps ops pending and counts the attempt',
    () async {
      await env.register();
      server.failNextPush = SyncTransportException('HTTP 503', status: 503);
      final r = await a.sync.sync(env.gymId);
      expect(r.error, 'HTTP 503');
      final pending = await a.ops('pending');
      expect(pending, isNotEmpty);
      expect(
        pending.every((o) => o.attemptCount == 1 && o.lastError == 'HTTP 503'),
        isTrue,
      );
      expect((await a.sync.sync(env.gymId)).ok, isTrue);
    },
  );

  test(
    'lost response: server applied the batch but the app never heard back',
    () async {
      await env.register();
      final ops = await a.ops();
      // simulate: server processes everything, then the connection drops
      server.apply([
        for (final o in ops)
          SyncOpDto(
            idempotencyKey: o.idempotencyKey,
            entityTable: o.entityTable,
            entityId: o.entityId,
            operation: o.operation,
            payload: jsonDecode(o.payloadJson) as Map<String, dynamic>,
          ),
      ]);
      final before = server.records.length;
      final r = await a.sync.sync(env.gymId);
      expect(r.ok, isTrue);
      expect(await a.ops('pending'), isEmpty);
      expect(
        server.records.length,
        before,
        reason: 'replay must not duplicate',
      );
    },
  );

  test(
    'a second device receives everything and keeps pulling incrementally',
    () async {
      final r = await env.register(name: 'Ravi Kumar');
      await env.pay(r.membership.id, 100000);
      await a.sync.sync(env.gymId);

      final b = await secondDevice();
      final first = await b.sync.sync(env.gymId);
      expect(first.ok, isTrue, reason: '$first');
      final m = (await b.db.select(b.db.members).get()).single;
      expect(m.fullName, 'Ravi Kumar');
      expect(
        (await b.db.select(b.db.payments).get()).single.status,
        'verified',
      );
      expect((await b.db.select(b.db.gyms).get()).single.name, 'Iron Temple');
      expect(
        await b.db.select(b.db.gymOwners).get(),
        isEmpty,
        reason: 'owner credentials stay put',
      );

      expect(
        (await b.sync.sync(env.gymId)).pulled,
        0,
        reason: 'cursor persisted',
      );

      // B edits, A receives
      await b.members.updateMember(
        memberId: m.id,
        expectedVersion: m.version,
        actorId: 'b-owner',
        input: edit(m, name: 'Ravi K. Sharma'),
      );
      expect((await b.sync.sync(env.gymId)).pushed, 1);
      final back = await a.sync.sync(env.gymId);
      expect(back.pulled, 1);
      expect(
        (await env.db.select(env.db.members).getSingle()).fullName,
        'Ravi K. Sharma',
      );
    },
  );

  test('conflict: both devices edit the same member offline; server wins, loser is audited', () async {
    final r = await env.register(name: 'Ravi Kumar');
    await a.sync.sync(env.gymId);
    final b = await secondDevice();
    await b.sync.sync(env.gymId);

    final ma = await env.db.select(env.db.members).getSingle();
    final mb = await b.db.select(b.db.members).getSingle();
    await env.members.updateMember(
      memberId: ma.id,
      expectedVersion: ma.version,
      actorId: env.ownerId,
      input: edit(ma, name: 'Edited On A'),
    );
    await b.members.updateMember(
      memberId: mb.id,
      expectedVersion: mb.version,
      actorId: 'b',
      input: edit(mb, name: 'Edited On B'),
    );

    await a.sync.sync(env.gymId); // A reaches the server first
    final rb = await b.sync.sync(env.gymId);
    expect(rb.conflicts, 1);

    final fixed = await b.db.select(b.db.members).getSingle();
    expect(fixed.fullName, 'Edited On A');
    expect(fixed.version, 2);
    final lost = (await b.db.select(b.db.auditLogs).get())
        .where((l) => l.action == 'sync.conflict_server_wins')
        .single;
    expect(jsonEncode(jsonDecode(lost.detailsJson)), contains('Edited On B'));
    expect((await b.ops('conflict')), hasLength(1));

    // B can carry on from the server version
    await b.members.updateMember(
      memberId: mb.id,
      expectedVersion: fixed.version,
      actorId: 'b',
      input: edit(fixed, name: 'Edited again on B'),
    );
    expect((await b.sync.sync(env.gymId)).conflicts, 0);
    await a.sync.sync(env.gymId);
    expect(
      (await env.db.select(env.db.members).getSingle()).fullName,
      'Edited again on B',
    );
    expect(r.member.id, ma.id);
  });

  test(
    'pull: unsynced local edit loses to a newer server version and is audited',
    () async {
      await env.register(name: 'Ravi Kumar');
      await a.sync.sync(env.gymId);
      final b = await secondDevice();
      await b.sync.sync(env.gymId);
      final mb = await b.db.select(b.db.members).getSingle();
      final ma = await env.db.select(env.db.members).getSingle();

      // A edits twice (v3 on the server); B has one unsynced edit (v2)
      await env.members.updateMember(
        memberId: ma.id,
        expectedVersion: 1,
        actorId: 'a',
        input: edit(ma, name: 'A one'),
      );
      await env.members.updateMember(
        memberId: ma.id,
        expectedVersion: 2,
        actorId: 'a',
        input: edit(ma, name: 'A two'),
      );
      await a.sync.sync(env.gymId);
      await b.members.updateMember(
        memberId: mb.id,
        expectedVersion: 1,
        actorId: 'b',
        input: edit(mb, name: 'B local'),
      );

      final rb = await b.sync.sync(env.gymId);
      expect(rb.ok, isTrue);
      expect((await b.db.select(b.db.members).getSingle()).fullName, 'A two');
      expect((await b.ops('pending')), isEmpty);
      expect(
        (await b.db.select(b.db.auditLogs).get()).where(
          (l) => l.action == 'sync.conflict_server_wins',
        ),
        isNotEmpty,
      );
    },
  );

  test('a record that cannot be stored is logged, not fatal; the rest still applies', () async {
    // Both devices independently create member code M0001 with different ids.
    final b = await secondDevice();
    final ra = await env.register(name: 'Alpha A', phone: '9000000001');
    await a.sync.sync(env.gymId);
    await b.sync.sync(env.gymId);
    // B registers its own member (gets the next code, M0002) ...
    final plan = (await b.db.select(b.db.membershipPlans).get()).single;
    final rbMember = await b.members.registerMember(
      gymId: env.gymId,
      actorId: 'b',
      input: env.memberInput(name: 'Bravo B', phone: '9000000002'),
      planId: plan.id,
      startDate: DateTime.utc(2026, 3, 10),
    );
    // ... while A concurrently registers M0002 too.
    final rc = await env.register(name: 'Charlie C', phone: '9000000003');
    expect(rc.member.memberCode, rbMember.member.memberCode);
    await a.sync.sync(env.gymId);
    final rb = await b.sync.sync(env.gymId);
    expect(rb.applyFailures, greaterThan(0), reason: '$rb');
    expect(rb.ok, isTrue);
    final audit = (await b.db.select(b.db.auditLogs).get()).where(
      (l) => l.action == 'sync.apply_failed',
    );
    expect(
      audit.any((l) => l.detailsJson.contains('Charlie C')),
      isTrue,
      reason: 'payload preserved for manual recovery',
    );
    expect(ra.member.memberCode, isNot(rc.member.memberCode));
    // B's own member is untouched and still pushable
    expect(
      (await b.db.select(b.db.members).get()).map((m) => m.fullName),
      containsAll(['Alpha A', 'Bravo B']),
    );
  });

  test('one poisoned operation does not block the rest', () async {
    await env.register(name: 'Good One', phone: '9000000001');
    final bad = SyncOperationsCompanion.insert(
      id: 'poison',
      gymId: env.gymId,
      entityTable: 'members',
      entityId: 'poison-id',
      operation: 'insert',
      payloadJson: jsonEncode({
        'version': 1,
        'gymId': env.gymId,
        'poison': true,
      }),
      idempotencyKey: 'poison-key',
      createdAt: env.clock.t.add(const Duration(seconds: 1)),
    );
    await env.db.into(env.db.syncOperations).insert(bad);

    final gw = _PoisonGateway(server);
    final svc = SyncService(env.db, env.clock, gw);
    final r = await svc.sync(env.gymId);
    expect(r.ok, isTrue, reason: '$r');
    expect((await a.ops('failed')).single.id, 'poison');
    expect(await a.ops('pending'), isEmpty);
    expect(
      server.records.keys.any(
        (k) => k.startsWith('members/') && !k.contains('poison'),
      ),
      isTrue,
    );
  });

  test('auth failure leaves everything pending for the next attempt', () async {
    await env.register();
    server.failNextPush = SyncTransportException(
      'JWT expired',
      status: 401,
      retryable: false,
    );
    final r = await a.sync.sync(env.gymId);
    expect(r.error, 'JWT expired');
    expect(await a.ops('failed'), isEmpty);
    expect((await a.sync.sync(env.gymId)).ok, isTrue);
  });
}

/// Rejects any batch containing a poisoned operation, like the SQL function's
/// "malformed operation" error aborting the whole transaction.
class _PoisonGateway extends FakeGateway {
  _PoisonGateway(super.server);
  @override
  Future<List<OpResult>> push(String gymId, List<SyncOpDto> ops) async {
    if (ops.any((o) => o.payload['poison'] == true)) {
      throw SyncTransportException(
        'malformed operation',
        status: 400,
        retryable: false,
      );
    }
    return super.push(gymId, ops);
  }
}
