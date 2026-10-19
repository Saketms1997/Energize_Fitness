import 'dart:convert';

import 'package:drift/drift.dart';

import '../core/clock.dart';
import '../database/app_database.dart';
import '../models/enums.dart';
import 'gym_repository.dart' show ValidationException;
import 'ids.dart';
import 'outbox.dart';
import 'support.dart';

/// Payment lifecycle: pending -> verified | failed | rejected; verified ->
/// refunded. Every transition is a conditional update (`WHERE status = from`)
/// inside one transaction together with its immutable event row, so a replayed
/// or double-submitted request can never apply twice.
///
/// Verification is always a manual owner action: the owner confirms the exact
/// amount they actually received. Nothing here treats a screenshot, QR scan or
/// form submission as proof of payment.
class PaymentRepository {
  PaymentRepository(this._db, Clock clock)
    : _clock = clock,
      _outbox = OutboxWriter(_db, clock);

  final AppDatabase _db;
  final Clock _clock;
  final OutboxWriter _outbox;

  static String? normalizeReference(String? ref) {
    final t = ref?.trim().toUpperCase();
    return (t == null || t.isEmpty) ? null : t;
  }

  Future<int> verifiedPaidPaise(String membershipId) async {
    final sum = _db.payments.amountPaise.sum();
    final row =
        await (_db.selectOnly(_db.payments)
              ..addColumns([sum])
              ..where(
                _db.payments.membershipId.equals(membershipId) &
                    _db.payments.status.equals(PaymentStatus.verified.name),
              ))
            .getSingle();
    return row.read(sum) ?? 0;
  }

  Stream<List<PaymentEvent>> watchEvents(String paymentId) => watchQuery(
    _db,
    [_db.paymentEvents],
    () =>
        (_db.select(_db.paymentEvents)
              ..where((e) => e.paymentId.equals(paymentId))
              ..orderBy([(e) => OrderingTerm.asc(e.createdAt)]))
            .get(),
  );

  /// Creates a PENDING payment. Does not activate anything. [idempotencyKey]
  /// makes retries/double-clicks return the original row.
  Future<Payment> recordPayment({
    required String actorId,
    required String membershipId,
    required int amountPaise,
    required PaymentMethod method,
    String? reference,
    String? note,
    String? idempotencyKey,
  }) async {
    final ref = normalizeReference(reference);
    final errors = <String, String>{};
    if (amountPaise <= 0) errors['amount'] = 'Amount must be greater than zero';
    if (ref != null && ref.length < 6) {
      errors['reference'] = 'Reference looks too short';
    }
    if (errors.isNotEmpty) throw ValidationException(errors);
    final key = idempotencyKey ?? newId();

    return _db.transaction(() async {
      final existingByKey = await (_db.select(
        _db.payments,
      )..where((p) => p.idempotencyKey.equals(key))).getSingleOrNull();
      if (existingByKey != null) return existingByKey;

      final ms = await (_db.select(
        _db.memberships,
      )..where((m) => m.id.equals(membershipId))).getSingleOrNull();
      if (ms == null) throw DomainException('Membership not found');
      if (ms.status == 'cancelled') {
        throw DomainException('Cannot take payment for a cancelled membership');
      }
      if (ref != null) {
        final dup =
            await (_db.select(_db.payments)..where(
                  (p) => p.gymId.equals(ms.gymId) & p.reference.equals(ref),
                ))
                .getSingleOrNull();
        if (dup != null) {
          throw ValidationException({
            'reference': 'This transaction reference is already recorded',
          });
        }
      }
      final outstanding = ms.pricePaise - await verifiedPaidPaise(membershipId);
      if (amountPaise > outstanding) {
        throw ValidationException({
          'amount': 'Amount exceeds the outstanding balance',
        });
      }

      final now = _clock.now();
      final id = newId();
      await _db
          .into(_db.payments)
          .insert(
            PaymentsCompanion.insert(
              id: id,
              gymId: ms.gymId,
              membershipId: membershipId,
              memberId: ms.memberId,
              amountPaise: amountPaise,
              method: method.name,
              status: PaymentStatus.pending.name,
              reference: Value(ref),
              idempotencyKey: key,
              note: Value(note?.trim()),
              createdAt: now,
              updatedAt: now,
            ),
          );
      final row = await _get(id);
      await _event(row, 'created', null, PaymentStatus.pending, actorId, {
        'amountPaise': amountPaise,
        'method': method.name,
      });
      await _outbox.enqueue(
        gymId: ms.gymId,
        entityTable: 'payments',
        entityId: id,
        operation: 'insert',
        payload: row.toJson(),
      );
      await _outbox.audit(
        gymId: ms.gymId,
        actorId: actorId,
        action: 'payment.recorded',
        entityTable: 'payments',
        entityId: id,
        details: {'amountPaise': amountPaise, 'method': method.name},
      );
      return row;
    });
  }

  /// Manual verification. [confirmedAmountPaise] is what the owner says they
  /// actually received; it must equal the recorded amount. UPI payments need a
  /// transaction reference. Returns the payment; repeating the call on an
  /// already-verified payment is a no-op (replay-safe).
  Future<Payment> verifyPayment({
    required String paymentId,
    required String actorId,
    required int confirmedAmountPaise,
  }) async {
    return _db.transaction(() async {
      final p = await _get(paymentId);
      if (p.status == PaymentStatus.verified.name) return p;
      if (p.status != PaymentStatus.pending.name) {
        throw DomainException('Payment is ${p.status} and cannot be verified');
      }
      if (confirmedAmountPaise != p.amountPaise) {
        throw ValidationException({
          'amount': 'Received amount does not match the recorded amount',
        });
      }
      if (p.method == PaymentMethod.upi.name && p.reference == null) {
        throw ValidationException({
          'reference': 'Enter the UPI transaction reference before verifying',
        });
      }
      final now = _clock.now();
      final changed = await _transition(
        p,
        PaymentStatus.pending,
        PaymentStatus.verified,
        PaymentsCompanion(verifiedAt: Value(now), verifiedBy: Value(actorId)),
      );
      if (!changed) return _get(paymentId); // lost a race; already processed
      final row = await _get(paymentId);
      await _event(
        row,
        'verified',
        PaymentStatus.pending,
        PaymentStatus.verified,
        actorId,
        {'amountPaise': p.amountPaise},
      );
      await _afterChange(row, actorId, 'payment.verified');

      // Activate only when verified money covers the full price.
      final ms = await (_db.select(
        _db.memberships,
      )..where((m) => m.id.equals(p.membershipId))).getSingle();
      if (ms.status == 'pending_payment' &&
          await verifiedPaidPaise(ms.id) >= ms.pricePaise) {
        await _activate(ms, actorId, 'payment.verified');
      }
      return row;
    });
  }

  Future<Payment> rejectPayment({
    required String paymentId,
    required String actorId,
    required String reason,
  }) => _closePending(paymentId, actorId, reason, PaymentStatus.rejected);

  Future<Payment> markFailed({
    required String paymentId,
    required String actorId,
    required String reason,
  }) => _closePending(paymentId, actorId, reason, PaymentStatus.failed);

  /// Refunds a verified payment. The original row is kept; the refund is a
  /// status change plus an audited event. Optionally cancels the membership.
  Future<Payment> refundPayment({
    required String paymentId,
    required String actorId,
    required String reason,
    bool cancelMembership = false,
  }) async {
    final why = reason.trim();
    if (why.length < 5) {
      throw ValidationException({'reason': 'Give a reason for the refund'});
    }
    return _db.transaction(() async {
      final p = await _get(paymentId);
      if (p.status == PaymentStatus.refunded.name) return p;
      if (p.status != PaymentStatus.verified.name) {
        throw DomainException('Only verified payments can be refunded');
      }
      final changed = await _transition(
        p,
        PaymentStatus.verified,
        PaymentStatus.refunded,
        const PaymentsCompanion(),
      );
      if (!changed) return _get(paymentId);
      final row = await _get(paymentId);
      await _event(
        row,
        'refunded',
        PaymentStatus.verified,
        PaymentStatus.refunded,
        actorId,
        {
          'reason': why,
          'amountPaise': p.amountPaise,
          'cancelMembership': cancelMembership,
        },
      );
      await _afterChange(row, actorId, 'payment.refunded', reason: why);
      if (cancelMembership) {
        final ms = await (_db.select(
          _db.memberships,
        )..where((m) => m.id.equals(p.membershipId))).getSingle();
        if (ms.status != 'cancelled') {
          await (_db.update(
            _db.memberships,
          )..where((m) => m.id.equals(ms.id))).write(
            MembershipsCompanion(
              status: const Value('cancelled'),
              statusReason: Value('Refunded: $why'),
              version: Value(ms.version + 1),
              updatedAt: Value(_clock.now()),
            ),
          );
          final updated = await (_db.select(
            _db.memberships,
          )..where((m) => m.id.equals(ms.id))).getSingle();
          await _outbox.enqueue(
            gymId: ms.gymId,
            entityTable: 'memberships',
            entityId: ms.id,
            operation: 'update',
            baseVersion: ms.version,
            payload: updated.toJson(),
          );
          await _outbox.audit(
            gymId: ms.gymId,
            actorId: actorId,
            action: 'membership.cancelled',
            entityTable: 'memberships',
            entityId: ms.id,
            reason: 'Refunded: $why',
          );
        }
      }
      return row;
    });
  }

  // ---- internals ----------------------------------------------------------

  Future<Payment> _closePending(
    String paymentId,
    String actorId,
    String reason,
    PaymentStatus to,
  ) async {
    final why = reason.trim();
    if (why.length < 3) {
      throw ValidationException({'reason': 'Give a reason'});
    }
    return _db.transaction(() async {
      final p = await _get(paymentId);
      if (p.status == to.name) return p;
      if (p.status != PaymentStatus.pending.name) {
        throw DomainException(
          'Payment is ${p.status} and cannot be ${to.name}',
        );
      }
      final changed = await _transition(
        p,
        PaymentStatus.pending,
        to,
        PaymentsCompanion(note: Value(why)),
      );
      if (!changed) return _get(paymentId);
      final row = await _get(paymentId);
      await _event(row, to.name, PaymentStatus.pending, to, actorId, {
        'reason': why,
      });
      await _afterChange(row, actorId, 'payment.${to.name}', reason: why);
      return row;
    });
  }

  Future<bool> _transition(
    Payment p,
    PaymentStatus from,
    PaymentStatus to,
    PaymentsCompanion extra,
  ) async {
    final n =
        await (_db.update(
          _db.payments,
        )..where((x) => x.id.equals(p.id) & x.status.equals(from.name))).write(
          extra.copyWith(
            status: Value(to.name),
            version: Value(p.version + 1),
            updatedAt: Value(_clock.now()),
          ),
        );
    return n == 1;
  }

  Future<void> _afterChange(
    Payment row,
    String actorId,
    String action, {
    String? reason,
  }) async {
    await _outbox.enqueue(
      gymId: row.gymId,
      entityTable: 'payments',
      entityId: row.id,
      operation: 'update',
      baseVersion: row.version - 1,
      payload: row.toJson(),
    );
    await _outbox.audit(
      gymId: row.gymId,
      actorId: actorId,
      action: action,
      entityTable: 'payments',
      entityId: row.id,
      reason: reason,
      details: {'amountPaise': row.amountPaise, 'status': row.status},
    );
  }

  Future<void> _activate(Membership ms, String actorId, String cause) async {
    await (_db.update(_db.memberships)..where((m) => m.id.equals(ms.id))).write(
      MembershipsCompanion(
        status: const Value('active'),
        version: Value(ms.version + 1),
        updatedAt: Value(_clock.now()),
      ),
    );
    final updated = await (_db.select(
      _db.memberships,
    )..where((m) => m.id.equals(ms.id))).getSingle();
    await _outbox.enqueue(
      gymId: ms.gymId,
      entityTable: 'memberships',
      entityId: ms.id,
      operation: 'update',
      baseVersion: ms.version,
      payload: updated.toJson(),
    );
    await _outbox.audit(
      gymId: ms.gymId,
      actorId: actorId,
      action: 'membership.activated',
      entityTable: 'memberships',
      entityId: ms.id,
      details: {'cause': cause},
    );
  }

  Future<void> _event(
    Payment p,
    String type,
    PaymentStatus? from,
    PaymentStatus to,
    String actorId,
    Map<String, Object?> details,
  ) => _db
      .into(_db.paymentEvents)
      .insert(
        PaymentEventsCompanion.insert(
          id: newId(),
          gymId: p.gymId,
          paymentId: p.id,
          eventType: type,
          fromStatus: Value(from?.name),
          toStatus: to.name,
          actorId: Value(actorId),
          detailsJson: Value(jsonEncode(details)),
          createdAt: _clock.now(),
        ),
      );

  Future<Payment> _get(String id) =>
      (_db.select(_db.payments)..where((p) => p.id.equals(id))).getSingle();
}
