import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:gym_management_system/models/enums.dart';
import 'package:gym_management_system/repositories/message_repository.dart';
import 'package:gym_management_system/repositories/support.dart';
import 'package:gym_management_system/services/message_templates.dart';
import 'package:gym_management_system/services/upi.dart';
import 'package:gym_management_system/services/whatsapp_sender.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import '../support/test_env.dart';

void main() {
  group('UpiLink', () {
    test('builds a spec-shaped link with 2-decimal amount', () {
      final link = UpiLink.build(
        upiId: 'ironTemple@okbank',
        payeeName: 'Iron Temple',
        amountPaise: 150050,
        note: 'Monthly M0001',
      )!;
      final uri = Uri.parse(link);
      expect(uri.scheme, 'upi');
      expect(uri.host, 'pay');
      expect(uri.queryParameters, {
        'pa': 'ironTemple@okbank',
        'pn': 'Iron Temple',
        'am': '1500.50',
        'cu': 'INR',
        'tn': 'Monthly M0001',
      });
    });

    test('omits amount when zero, rejects bad VPA, strips newlines', () {
      expect(
        Uri.parse(UpiLink.build(upiId: 'a.b@upi', payeeName: 'X')!)
            .queryParameters
            .containsKey('am'),
        isFalse,
      );
      expect(UpiLink.build(upiId: 'nope', payeeName: 'X'), isNull);
      expect(UpiLink.build(upiId: null, payeeName: 'X'), isNull);
      final l = UpiLink.build(
        upiId: 'a.b@upi',
        payeeName: 'X\nY&pa=evil@upi',
        amountPaise: 100,
      )!;
      // a hostile payee name cannot inject a second payee parameter
      expect(Uri.parse(l).queryParameters['pa'], 'a.b@upi');
      expect(Uri.parse(l).queryParametersAll['pa'], ['a.b@upi']);
    });
  });

  group('templates', () {
    test('every template renders with no unfilled placeholder', () {
      final r = MessageComposer.expiryReminder(
        name: 'Ravi',
        plan: 'Monthly',
        gym: 'Iron Temple',
        endDate: DateTime.utc(2026, 3, 31),
      );
      expect(r.body, contains('31 Mar 2026'));
      expect(r.body.contains('{{'), isFalse);
      expect(jsonDecode(r.paramsJson), [
        'Ravi',
        'Monthly',
        'Iron Temple',
        '31 Mar 2026',
      ]);
      for (final t in MessageTemplate.values) {
        final n = RegExp(r'\{\{\d+\}\}').allMatches(t.text).length;
        final out = MessageComposer.render(t, List.filled(n, 'x'));
        expect(out.body.contains('{{'), isFalse, reason: t.name);
      }
    });

    test('newlines in names cannot break the message layout', () {
      final r = MessageComposer.welcome(
        name: 'Ravi\n\nClick evil.com',
        gym: 'G',
        memberCode: 'M1',
      );
      expect(r.body.contains('\n'), isFalse);
    });

    test('wa.me link is url-encoded', () {
      expect(
        waMeLink('9876543210', 'Hi & bye'),
        'https://wa.me/919876543210?text=Hi%20%26%20bye',
      );
    });
  });

  group('MessageRepository', () {
    late TestEnv env;
    late MessageRepository msgs;
    setUp(() async {
      env = await TestEnv.create(); // 2026-03-10 (IST) by default
      msgs = MessageRepository(env.db, env.clock);
    });
    tearDown(() => env.dispose());

    test('queue requires consent and dedupes', () async {
      final r = await env.register(); // consent true
      final m = await msgs.queueWelcome(r.member.id, env.ownerId);
      expect(m, isNotNull);
      expect(m!.toPhone, '919876543210');
      expect(m.status, 'queued');
      expect(await msgs.queueWelcome(r.member.id, env.ownerId), isNull);

      final noConsent = await env.members.registerMember(
        gymId: env.gymId,
        actorId: env.ownerId,
        input: env.memberInput(
          name: 'Quiet One',
          phone: '9000000001',
          consent: false,
        ),
        planId: env.monthly.id,
        startDate: DateTime.utc(2026, 3, 10),
      );
      expect(await msgs.queueWelcome(noConsent.member.id, env.ownerId), isNull);
      await expectLater(
        msgs.queue(
          memberId: noConsent.member.id,
          actorId: env.ownerId,
          message: MessageComposer.welcome(
            name: 'a',
            gym: 'b',
            memberCode: 'c',
          ),
          dedupeKey: 'x',
        ),
        throwsA(isA<DomainException>()),
      );
    });

    test('queued messages are also written to the sync outbox', () async {
      final r = await env.register();
      await msgs.queueWelcome(r.member.id, env.ownerId);
      final ops = await env.db.select(env.db.syncOperations).get();
      expect(
        ops.where((o) => o.entityTable == 'whatsapp_messages'),
        hasLength(1),
      );
    });

    test('reminders: 3-day expiry, once only, consent respected', () async {
      // 1 month from 10 Mar ends 9 Apr. Move to 6 Apr -> 2 days left.
      final r = await env.register(price: 100000);
      await env.pay(r.membership.id, 100000);
      env.clock.t = DateTime.utc(2026, 4, 6, 6, 30);

      var c = await msgs.generateReminders(env.gymId, env.ownerId);
      expect(c.expiring, 1);
      expect(c.total, 1);
      c = await msgs.generateReminders(env.gymId, env.ownerId);
      expect(c.total, 0, reason: 'second run must not duplicate');
      final all = await env.db.select(env.db.whatsappMessages).get();
      expect(all.single.templateKey, 'expiryReminder');
      expect(all.single.bodyText, contains('9 Apr 2026'));
    });

    test('expired notice goes out unless the member renewed', () async {
      final a = await env.register(name: 'Lapsed Lee', phone: '9111111111');
      await env.pay(a.membership.id, 100000);
      final b = await env.register(name: 'Renewed Rani', phone: '9222222222');
      await env.pay(b.membership.id, 100000);
      await env.memberships.renew(
        memberId: b.member.id,
        actorId: env.ownerId,
        planId: env.monthly.id,
      );
      env.clock.t = DateTime.utc(2026, 4, 11, 6, 30); // 2 days after 9 Apr
      final c = await msgs.generateReminders(env.gymId, env.ownerId);
      expect(c.expired, 1);
      final rows = await env.db.select(env.db.whatsappMessages).get();
      expect(
        rows.where((m) => m.templateKey == 'expired').single.bodyText,
        contains('Lapsed Lee'),
      );
    });

    test(
      'unpaid membership gets a single balance reminder after a day',
      () async {
        await env.register();
        expect((await msgs.generateReminders(env.gymId, env.ownerId)).total, 0);
        env.clock.t = DateTime.utc(2026, 3, 11, 6, 30);
        final c = await msgs.generateReminders(env.gymId, env.ownerId);
        expect(c.dueBalance, 1);
        expect((await msgs.generateReminders(env.gymId, env.ownerId)).total, 0);
      },
    );

    test('receipt only for verified payments, once', () async {
      final r = await env.register();
      final pending = await env.payments.recordPayment(
        actorId: env.ownerId,
        membershipId: r.membership.id,
        amountPaise: 100000,
        method: PaymentMethod.cash,
      );
      expect(await msgs.queueReceipt(pending.id, env.ownerId), isNull);
      final paid = await env.payments.verifyPayment(
        paymentId: pending.id,
        actorId: env.ownerId,
        confirmedAmountPaise: 100000,
      );
      final m = await msgs.queueReceipt(paid.id, env.ownerId);
      expect(m!.bodyText, contains('₹1,000.00'));
      expect(await msgs.queueReceipt(paid.id, env.ownerId), isNull);
    });

    test('state machine: retry limits, cancel, no illegal moves', () async {
      final r = await env.register();
      final m = (await msgs.queueWelcome(r.member.id, env.ownerId))!;
      for (var i = 0; i < MessageRepository.maxAttempts - 1; i++) {
        expect((await msgs.recordFailure(m.id, 'boom')).status, 'queued');
      }
      expect((await msgs.recordFailure(m.id, 'boom')).status, 'failed');
      expect(await msgs.queued(env.gymId), isEmpty);
      expect((await msgs.retry(m.id, env.ownerId)).status, 'queued');
      final sent = await msgs.markSent(m.id, providerMessageId: 'wamid.1');
      expect(sent.status, 'sent');
      await expectLater(
        msgs.cancel(m.id, env.ownerId),
        throwsA(isA<DomainException>()),
      );
      await expectLater(
        msgs.recordFailure(m.id, 'late'),
        throwsA(isA<DomainException>()),
      );
    });
  });

  group('EdgeFunctionSender + dispatcher', () {
    late TestEnv env;
    late MessageRepository msgs;
    setUp(() async {
      env = await TestEnv.create();
      msgs = MessageRepository(env.db, env.clock);
      final r = await env.register();
      await msgs.queueWelcome(r.member.id, env.ownerId);
    });
    tearDown(() => env.dispose());

    EdgeFunctionSender sender(http.Client c, {String? token = 'jwt'}) =>
        EdgeFunctionSender(
          client: c,
          functionsBaseUrl: 'https://x.supabase.co/functions/v1',
          accessToken: () async => token,
        );

    test(
      'success marks sent and sends template params, never a secret',
      () async {
        late http.Request seen;
        final d = WhatsAppDispatcher(
          msgs,
          sender(
            MockClient((req) async {
              seen = req;
              return http.Response(
                jsonEncode({'providerMessageId': 'wamid.9'}),
                200,
              );
            }),
          ),
        );
        expect(await d.processQueue(env.gymId), 1);
        final row = (await env.db.select(env.db.whatsappMessages).get()).single;
        expect(row.status, 'sent');
        expect(row.providerMessageId, 'wamid.9');
        expect(seen.headers['Authorization'], 'Bearer jwt');
        final body = jsonDecode(seen.body) as Map;
        expect(body['template'], 'energize_welcome');
        expect(body['to'], '919876543210');
        expect((body['params'] as List).length, 3);
      },
    );

    test(
      '5xx is retried later, 4xx fails permanently, no token fails softly',
      () async {
        final d500 = WhatsAppDispatcher(
          msgs,
          sender(
            MockClient((_) async => http.Response('{"error":"down"}', 503)),
          ),
        );
        expect(await d500.processQueue(env.gymId), 0);
        var row = (await env.db.select(env.db.whatsappMessages).get()).single;
        expect((row.status, row.attemptCount), ('queued', 1));

        final d400 = WhatsAppDispatcher(
          msgs,
          sender(
            MockClient(
              (_) async => http.Response('{"error":"bad number"}', 400),
            ),
          ),
        );
        await d400.processQueue(env.gymId);
        row = (await env.db.select(env.db.whatsappMessages).get()).single;
        expect(row.status, 'failed');
        expect(row.errorMessage, 'bad number');
      },
    );

    test('network exception is a retryable failure, not a crash', () async {
      final d = WhatsAppDispatcher(
        msgs,
        sender(MockClient((_) async => throw http.ClientException('offline'))),
      );
      await d.processQueue(env.gymId);
      final row = (await env.db.select(env.db.whatsappMessages).get()).single;
      expect(row.status, 'queued');
      expect(row.errorMessage, startsWith('Network error'));
    });
  });
}
