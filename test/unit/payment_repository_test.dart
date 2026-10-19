import 'package:flutter_test/flutter_test.dart';
import 'package:gym_management_system/models/enums.dart';
import 'package:gym_management_system/repositories/gym_repository.dart';
import 'package:gym_management_system/repositories/support.dart';

import '../support/test_env.dart';

void main() {
  late TestEnv env;
  late String membershipId;
  setUp(() async {
    env = await TestEnv.create();
    membershipId = (await env.register()).membership.id;
  });
  tearDown(() => env.dispose());

  Future<String> statusOf() async => (await (env.db.select(
    env.db.memberships,
  )..where((m) => m.id.equals(membershipId))).getSingle()).status;

  Future<int> auditCount(String action) async =>
      (await env.db.select(env.db.auditLogs).get())
          .where((a) => a.action == action)
          .length;

  test('recording a payment does not activate the membership', () async {
    final p = await env.payments.recordPayment(
      actorId: env.ownerId,
      membershipId: membershipId,
      amountPaise: 100000,
      method: PaymentMethod.upi,
      reference: 'utr123456789',
    );
    expect(p.status, 'pending');
    expect(p.reference, 'UTR123456789');
    expect(await statusOf(), 'pending_payment');
  });

  test('verifying the full amount activates the membership', () async {
    final p = await env.pay(membershipId, 100000);
    expect(p.status, 'verified');
    expect(p.verifiedBy, env.ownerId);
    expect(p.verifiedAt, isNotNull);
    expect(await statusOf(), 'active');
    expect(await auditCount('membership.activated'), 1);
  });

  test('partial verified payments keep it pending until fully paid', () async {
    await env.pay(membershipId, 40000);
    expect(await statusOf(), 'pending_payment');
    expect(await env.payments.verifiedPaidPaise(membershipId), 40000);
    await env.pay(membershipId, 60000);
    expect(await statusOf(), 'active');
  });

  test('confirmed amount must match the recorded amount', () async {
    final p = await env.payments.recordPayment(
      actorId: env.ownerId,
      membershipId: membershipId,
      amountPaise: 100000,
      method: PaymentMethod.cash,
    );
    await expectLater(
      env.payments.verifyPayment(
        paymentId: p.id,
        actorId: env.ownerId,
        confirmedAmountPaise: 90000,
      ),
      throwsA(isA<ValidationException>()),
    );
    expect(await statusOf(), 'pending_payment');
  });

  test(
    'UPI payments cannot be verified without a transaction reference',
    () async {
      final p = await env.payments.recordPayment(
        actorId: env.ownerId,
        membershipId: membershipId,
        amountPaise: 100000,
        method: PaymentMethod.upi,
      );
      await expectLater(
        env.payments.verifyPayment(
          paymentId: p.id,
          actorId: env.ownerId,
          confirmedAmountPaise: 100000,
        ),
        throwsA(isA<ValidationException>()),
      );
    },
  );

  test('duplicate transaction references are rejected', () async {
    await env.payments.recordPayment(
      actorId: env.ownerId,
      membershipId: membershipId,
      amountPaise: 50000,
      method: PaymentMethod.upi,
      reference: 'UTR-ABC-123',
    );
    await expectLater(
      env.payments.recordPayment(
        actorId: env.ownerId,
        membershipId: membershipId,
        amountPaise: 50000,
        method: PaymentMethod.upi,
        reference: ' utr-abc-123 ',
      ),
      throwsA(isA<ValidationException>()),
    );
  });

  test('the same idempotency key returns the original payment', () async {
    final a = await env.payments.recordPayment(
      actorId: env.ownerId,
      membershipId: membershipId,
      amountPaise: 50000,
      method: PaymentMethod.cash,
      idempotencyKey: 'key-1',
    );
    final b = await env.payments.recordPayment(
      actorId: env.ownerId,
      membershipId: membershipId,
      amountPaise: 50000,
      method: PaymentMethod.cash,
      idempotencyKey: 'key-1',
    );
    expect(b.id, a.id);
    expect(await env.db.select(env.db.payments).get(), hasLength(1));
  });

  test('replayed verification is a no-op (no second event or audit)', () async {
    final p = await env.pay(membershipId, 100000);
    final again = await env.payments.verifyPayment(
      paymentId: p.id,
      actorId: env.ownerId,
      confirmedAmountPaise: 100000,
    );
    expect(again.version, p.version);
    final events = await env.db.select(env.db.paymentEvents).get();
    expect(events.where((e) => e.eventType == 'verified'), hasLength(1));
    expect(await auditCount('payment.verified'), 1);
    expect(await auditCount('membership.activated'), 1);
  });

  test('concurrent verifications apply exactly once', () async {
    final p = await env.payments.recordPayment(
      actorId: env.ownerId,
      membershipId: membershipId,
      amountPaise: 100000,
      method: PaymentMethod.cash,
    );
    await Future.wait([
      for (var i = 0; i < 5; i++)
        env.payments.verifyPayment(
          paymentId: p.id,
          actorId: env.ownerId,
          confirmedAmountPaise: 100000,
        ),
    ]);
    expect(await env.payments.verifiedPaidPaise(membershipId), 100000);
    expect(await auditCount('payment.verified'), 1);
  });

  test('cannot overpay the outstanding balance', () async {
    await env.pay(membershipId, 90000);
    await expectLater(
      env.payments.recordPayment(
        actorId: env.ownerId,
        membershipId: membershipId,
        amountPaise: 20000,
        method: PaymentMethod.cash,
      ),
      throwsA(isA<ValidationException>()),
    );
  });

  test('rejected and failed payments never count as money', () async {
    final a = await env.payments.recordPayment(
      actorId: env.ownerId,
      membershipId: membershipId,
      amountPaise: 50000,
      method: PaymentMethod.upi,
      reference: 'UTR000001',
    );
    final rejected = await env.payments.rejectPayment(
      paymentId: a.id,
      actorId: env.ownerId,
      reason: 'Not in bank statement',
    );
    expect(rejected.status, 'rejected');
    final b = await env.payments.recordPayment(
      actorId: env.ownerId,
      membershipId: membershipId,
      amountPaise: 50000,
      method: PaymentMethod.cash,
    );
    final failed = await env.payments.markFailed(
      paymentId: b.id,
      actorId: env.ownerId,
      reason: 'Member left without paying',
    );
    expect(failed.status, 'failed');
    expect(await env.payments.verifiedPaidPaise(membershipId), 0);
    expect(await statusOf(), 'pending_payment');
    // A closed payment can't be verified later.
    await expectLater(
      env.payments.verifyPayment(
        paymentId: a.id,
        actorId: env.ownerId,
        confirmedAmountPaise: 50000,
      ),
      throwsA(isA<DomainException>()),
    );
  });

  test(
    'refund keeps the original row, logs events, can cancel membership',
    () async {
      final p = await env.pay(membershipId, 100000);
      await expectLater(
        env.payments.refundPayment(
          paymentId: p.id,
          actorId: env.ownerId,
          reason: 'no',
        ),
        throwsA(isA<ValidationException>()),
      );
      final r = await env.payments.refundPayment(
        paymentId: p.id,
        actorId: env.ownerId,
        reason: 'Member moved away',
        cancelMembership: true,
      );
      expect(r.status, 'refunded');
      expect(r.amountPaise, 100000);
      expect(await statusOf(), 'cancelled');
      expect(await env.payments.verifiedPaidPaise(membershipId), 0);
      final events = (await env.db.select(env.db.paymentEvents).get())
          .where((e) => e.paymentId == p.id)
          .map((e) => e.eventType)
          .toList();
      expect(events, ['created', 'verified', 'refunded']);
      // Refunding again is a no-op.
      final again = await env.payments.refundPayment(
        paymentId: p.id,
        actorId: env.ownerId,
        reason: 'Member moved away',
      );
      expect(again.version, r.version);
    },
  );

  test('payments cannot be taken for a cancelled membership', () async {
    await env.memberships.cancel(
      membershipId: membershipId,
      actorId: env.ownerId,
      reason: 'Changed mind',
    );
    await expectLater(
      env.payments.recordPayment(
        actorId: env.ownerId,
        membershipId: membershipId,
        amountPaise: 1000,
        method: PaymentMethod.cash,
      ),
      throwsA(isA<DomainException>()),
    );
  });

  test('payments are queued for sync with the payment event trail', () async {
    await env.pay(membershipId, 100000);
    final ops = await env.db.select(env.db.syncOperations).get();
    expect(ops.where((o) => o.entityTable == 'payments'), isNotEmpty);
    final keys = ops.map((o) => o.idempotencyKey).toSet();
    expect(keys.length, ops.length);
  });
}
