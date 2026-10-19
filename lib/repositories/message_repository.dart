import 'package:drift/drift.dart';

import '../core/clock.dart';
import '../core/dates.dart';
import '../database/app_database.dart';
import '../models/membership_logic.dart';
import '../services/message_templates.dart';
import 'ids.dart';
import 'outbox.dart';
import 'support.dart';

class ReminderCounts {
  const ReminderCounts({
    this.expiring = 0,
    this.expired = 0,
    this.dueBalance = 0,
    this.skippedNoConsent = 0,
  });
  final int expiring;
  final int expired;
  final int dueBalance;
  final int skippedNoConsent;
  int get total => expiring + expired + dueBalance;
}

class MessageRepository {
  MessageRepository(this._db, this._clock)
    : _outbox = OutboxWriter(_db, _clock);

  static const maxAttempts = 5;

  /// Reminder is queued once the end date is this many days away or closer.
  static const expiryLeadDays = 3;

  final AppDatabase _db;
  final Clock _clock;
  final OutboxWriter _outbox;

  Stream<List<WhatsappMessage>> watchMessages(
    String gymId, {
    int limit = 200,
  }) => watchQuery(
    _db,
    [_db.whatsappMessages],
    () =>
        (_db.select(_db.whatsappMessages)
              ..where((m) => m.gymId.equals(gymId))
              ..orderBy([(m) => OrderingTerm.desc(m.createdAt)])
              ..limit(limit))
            .get(),
  );

  Future<List<WhatsappMessage>> queued(String gymId) =>
      (_db.select(_db.whatsappMessages)
            ..where(
              (m) =>
                  m.gymId.equals(gymId) &
                  m.status.equals('queued') &
                  m.attemptCount.isSmallerThanValue(maxAttempts),
            )
            ..orderBy([(m) => OrderingTerm.asc(m.createdAt)]))
          .get();

  Future<WhatsappMessage> _get(String id) => (_db.select(
    _db.whatsappMessages,
  )..where((m) => m.id.equals(id))).getSingle();

  /// Queues [message] for [memberId]. Requires recorded WhatsApp consent.
  /// Returns null when a message with the same [dedupeKey] already exists, so
  /// automatic reminders can be generated repeatedly without duplicates.
  Future<WhatsappMessage?> queue({
    required String memberId,
    required String actorId,
    required RenderedMessage message,
    required String dedupeKey,
  }) {
    return _db.transaction(() async {
      final member = await (_db.select(
        _db.members,
      )..where((m) => m.id.equals(memberId))).getSingle();
      if (!member.whatsappConsent) {
        throw DomainException(
          '${member.fullName} has not agreed to WhatsApp messages',
        );
      }
      final dup =
          await (_db.select(_db.whatsappMessages)..where(
                (m) =>
                    m.gymId.equals(member.gymId) &
                    m.dedupeKey.equals(dedupeKey),
              ))
              .getSingleOrNull();
      if (dup != null) return null;

      final now = _clock.now();
      final id = newId();
      await _db
          .into(_db.whatsappMessages)
          .insert(
            WhatsappMessagesCompanion.insert(
              id: id,
              gymId: member.gymId,
              memberId: member.id,
              toPhone: '91${member.phone}',
              templateKey: message.template.name,
              bodyText: message.body,
              paramsJson: Value(message.paramsJson),
              status: 'queued',
              dedupeKey: dedupeKey,
              createdAt: now,
              updatedAt: now,
            ),
          );
      final row = await _get(id);
      await _enqueueSync(row, 'insert', 0);
      await _outbox.audit(
        gymId: member.gymId,
        actorId: actorId,
        action: 'whatsapp.queued',
        entityTable: 'whatsapp_messages',
        entityId: id,
        details: {'template': message.template.name},
      );
      return row;
    });
  }

  Future<void> _enqueueSync(WhatsappMessage row, String op, int base) =>
      _outbox.enqueue(
        gymId: row.gymId,
        entityTable: 'whatsapp_messages',
        entityId: row.id,
        operation: op,
        baseVersion: base,
        payload: row.toJson(),
      );

  Future<WhatsappMessage> _move(
    String id,
    Set<String> allowedFrom,
    WhatsappMessagesCompanion changes, {
    String? actorId,
    String? auditAction,
  }) {
    return _db.transaction(() async {
      final m = await _get(id);
      if (!allowedFrom.contains(m.status)) {
        throw DomainException('Message is ${m.status}; cannot change it');
      }
      await (_db.update(
        _db.whatsappMessages,
      )..where((x) => x.id.equals(id))).write(
        changes.copyWith(
          version: Value(m.version + 1),
          updatedAt: Value(_clock.now()),
        ),
      );
      final row = await _get(id);
      await _enqueueSync(row, 'update', m.version);
      if (auditAction != null) {
        await _outbox.audit(
          gymId: m.gymId,
          actorId: actorId,
          action: auditAction,
          entityTable: 'whatsapp_messages',
          entityId: id,
        );
      }
      return row;
    });
  }

  /// [manual] = the owner sent it from their own WhatsApp (wa.me link).
  Future<WhatsappMessage> markSent(
    String id, {
    String? providerMessageId,
    String? actorId,
    bool manual = false,
  }) async {
    final m = await _get(id);
    return _move(
      id,
      {'queued', 'failed'},
      WhatsappMessagesCompanion(
        status: const Value('sent'),
        providerMessageId: Value(providerMessageId),
        sentAt: Value(_clock.now()),
        errorMessage: const Value(null),
        attemptCount: Value(m.attemptCount + (manual ? 0 : 1)),
      ),
      actorId: actorId,
      auditAction: manual ? 'whatsapp.marked_sent_manually' : null,
    );
  }

  /// Records a failed attempt. Retryable failures stay queued until
  /// [maxAttempts] is reached; permanent ones fail immediately.
  Future<WhatsappMessage> recordFailure(
    String id,
    String error, {
    bool retryable = true,
  }) async {
    final m = await _get(id);
    final attempts = m.attemptCount + 1;
    final giveUp = !retryable || attempts >= maxAttempts;
    return _move(
      id,
      {'queued'},
      WhatsappMessagesCompanion(
        status: Value(giveUp ? 'failed' : 'queued'),
        attemptCount: Value(attempts),
        errorMessage: Value(
          error.length > 300 ? error.substring(0, 300) : error,
        ),
      ),
    );
  }

  Future<WhatsappMessage> retry(String id, String actorId) => _move(
    id,
    {'failed'},
    const WhatsappMessagesCompanion(
      status: Value('queued'),
      attemptCount: Value(0),
      errorMessage: Value(null),
    ),
    actorId: actorId,
    auditAction: 'whatsapp.retried',
  );

  Future<WhatsappMessage> cancel(String id, String actorId) => _move(
    id,
    {'queued', 'failed'},
    const WhatsappMessagesCompanion(status: Value('cancelled')),
    actorId: actorId,
    auditAction: 'whatsapp.cancelled',
  );

  /// Scans memberships and queues reminders that are due and not yet queued:
  ///  * active, ending within [expiryLeadDays] days
  ///  * ended within the last 7 days and not renewed (one "expired" notice)
  ///  * pending_payment since at least a day ago with a balance (one reminder)
  /// Safe to run as often as you like.
  Future<ReminderCounts> generateReminders(String gymId, String actorId) async {
    final gym = await (_db.select(
      _db.gyms,
    )..where((g) => g.id.equals(gymId))).getSingle();
    final today = GymDates.today(_clock.now(), gym.timezone);
    final members = {
      for (final m in await (_db.select(
        _db.members,
      )..where((m) => m.gymId.equals(gymId) & m.deletedAt.isNull())).get())
        m.id: m,
    };
    final memberships = await (_db.select(
      _db.memberships,
    )..where((m) => m.gymId.equals(gymId) & m.deletedAt.isNull())).get();
    final byMember = <String, List<Membership>>{};
    for (final ms in memberships) {
      byMember.putIfAbsent(ms.memberId, () => []).add(ms);
    }
    final paid = <String, int>{};
    final payments = await (_db.select(
      _db.payments,
    )..where((p) => p.gymId.equals(gymId) & p.status.equals('verified'))).get();
    for (final p in payments) {
      paid.update(
        p.membershipId,
        (v) => v + p.amountPaise,
        ifAbsent: () => p.amountPaise,
      );
    }

    var expiring = 0, expired = 0, due = 0, noConsent = 0;
    for (final ms in memberships) {
      final member = members[ms.memberId];
      if (member == null || ms.status == 'cancelled') continue;
      final left = GymDates.daysBetween(today, ms.endDate);
      final all = byMember[ms.memberId]!;

      RenderedMessage? msg;
      String? key;
      if (ms.status == 'active' && left >= 0 && left <= expiryLeadDays) {
        msg = MessageComposer.expiryReminder(
          name: member.fullName,
          plan: ms.planName,
          gym: gym.name,
          endDate: ms.endDate,
        );
        key = 'expiry:${ms.id}';
      } else if (effectiveMembershipStatus(ms, today) == 'expired' &&
          left < 0 &&
          left >= -7 &&
          !all.any((o) => o.id != ms.id && o.startDate.isAfter(ms.startDate))) {
        msg = MessageComposer.expired(
          name: member.fullName,
          plan: ms.planName,
          gym: gym.name,
          endDate: ms.endDate,
        );
        key = 'expired:${ms.id}';
      } else if (ms.status == 'pending_payment' &&
          GymDates.daysBetween(
                GymDates.today(ms.createdAt, gym.timezone),
                today,
              ) >=
              1) {
        final balance = ms.pricePaise - (paid[ms.id] ?? 0);
        if (balance > 0) {
          msg = MessageComposer.paymentDue(
            name: member.fullName,
            balancePaise: balance,
            plan: ms.planName,
            gym: gym.name,
          );
          key = 'due:${ms.id}';
        }
      }
      if (msg == null || key == null) continue;
      if (!member.whatsappConsent) {
        noConsent++;
        continue;
      }
      final queued = await queue(
        memberId: member.id,
        actorId: actorId,
        message: msg,
        dedupeKey: key,
      );
      if (queued == null) continue;
      switch (msg.template) {
        case MessageTemplate.expiryReminder:
          expiring++;
        case MessageTemplate.expired:
          expired++;
        default:
          due++;
      }
    }
    return ReminderCounts(
      expiring: expiring,
      expired: expired,
      dueBalance: due,
      skippedNoConsent: noConsent,
    );
  }

  /// Receipt for a verified payment (idempotent per payment).
  Future<WhatsappMessage?> queueReceipt(
    String paymentId,
    String actorId,
  ) async {
    final p = await (_db.select(
      _db.payments,
    )..where((x) => x.id.equals(paymentId))).getSingle();
    if (p.status != 'verified') return null;
    final member = await (_db.select(
      _db.members,
    )..where((m) => m.id.equals(p.memberId))).getSingle();
    if (!member.whatsappConsent) return null;
    final ms = await (_db.select(
      _db.memberships,
    )..where((m) => m.id.equals(p.membershipId))).getSingle();
    final gym = await (_db.select(
      _db.gyms,
    )..where((g) => g.id.equals(p.gymId))).getSingle();
    return queue(
      memberId: member.id,
      actorId: actorId,
      message: MessageComposer.paymentReceived(
        name: member.fullName,
        amountPaise: p.amountPaise,
        gym: gym.name,
        plan: ms.planName,
        endDate: ms.endDate,
      ),
      dedupeKey: 'receipt:${p.id}',
    );
  }

  Future<WhatsappMessage?> queueWelcome(String memberId, String actorId) async {
    final member = await (_db.select(
      _db.members,
    )..where((m) => m.id.equals(memberId))).getSingle();
    if (!member.whatsappConsent) return null;
    final gym = await (_db.select(
      _db.gyms,
    )..where((g) => g.id.equals(member.gymId))).getSingle();
    return queue(
      memberId: memberId,
      actorId: actorId,
      message: MessageComposer.welcome(
        name: member.fullName,
        gym: gym.name,
        memberCode: member.memberCode,
      ),
      dedupeKey: 'welcome:$memberId',
    );
  }
}
