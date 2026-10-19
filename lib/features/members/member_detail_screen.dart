import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/dates.dart';
import '../../core/money.dart';
import '../../core/providers.dart';
import '../../database/app_database.dart';
import '../../models/enums.dart';
import '../../models/membership_logic.dart';
import '../../repositories/member_repository.dart';
import '../../services/session_controller.dart';
import '../../shared/dialogs.dart';
import '../../shared/formatters.dart';
import 'member_dialogs.dart';

class MemberDetailScreen extends ConsumerWidget {
  const MemberDetailScreen({required this.memberId, super.key});
  final String memberId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final detail = ref.watch(memberDetailProvider(memberId));
    return detail.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (_, _) => const Center(child: Text('Could not load member.')),
      data: (d) =>
          d == null ? const Center(child: Text('Member not found.')) : _Body(d),
    );
  }
}

class _Body extends ConsumerWidget {
  const _Body(this.d);
  final MemberDetail d;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final session = ref.watch(sessionProvider);
    if (session == null) return const SizedBox.shrink();
    final gym = ref.watch(currentGymProvider).value;
    final today = GymDates.today(
      ref.watch(clockProvider).now(),
      gym?.timezone ?? 'Asia/Kolkata',
    );
    final paidBy = <String, int>{};
    for (final p in d.payments.where((p) => p.status == 'verified')) {
      paidBy.update(
        p.membershipId,
        (v) => v + p.amountPaise,
        ifAbsent: () => p.amountPaise,
      );
    }
    final status = overallMemberStatus(d.memberships, today);

    return Align(
      alignment: Alignment.topCenter,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 900),
        child: ListView(
          padding: const EdgeInsets.all(24),
          children: [
            _ProfileCard(d.member, status, session),
            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(
                  child: Text(
                    'Memberships',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                FilledButton.icon(
                  key: const Key('renew'),
                  onPressed: () => renewDialog(context, ref, d.member, session),
                  icon: const Icon(Icons.autorenew),
                  label: const Text('Renew / new plan'),
                ),
              ],
            ),
            const SizedBox(height: 8),
            for (final m in d.memberships)
              _MembershipCard(
                m,
                today: today,
                paid: paidBy[m.id] ?? 0,
                session: session,
              ),
            const SizedBox(height: 16),
            Text('Payments', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            if (d.payments.isEmpty)
              const Text('No payments recorded yet.')
            else
              for (final p in d.payments) _PaymentTile(p, session),
          ],
        ),
      ),
    );
  }
}

class _ProfileCard extends ConsumerWidget {
  const _ProfileCard(this.m, this.status, this.session);
  final Member m;
  final String status;
  final OwnerSession session;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = Theme.of(context).textTheme;
    Widget line(String label, String? value) => value == null || value.isEmpty
        ? const SizedBox.shrink()
        : Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text('$label: $value'),
          );
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    m.fullName,
                    key: const Key('detailName'),
                    style: t.headlineSmall,
                  ),
                ),
                StatusChip(status),
                IconButton(
                  key: const Key('editMember'),
                  tooltip: 'Edit',
                  icon: const Icon(Icons.edit),
                  onPressed: () => editMemberDialog(context, ref, m, session),
                ),
              ],
            ),
            Text('Member ID ${m.memberCode}', style: t.bodySmall),
            line('Phone', m.phone),
            line('Alternate', m.altPhone),
            line(
              'Born',
              m.dateOfBirth == null
                  ? (m.ageYears == null ? null : 'Age ${m.ageYears}')
                  : formatDate(m.dateOfBirth!),
            ),
            line('Address', m.address),
            line(
              'Emergency contact',
              m.emergencyName == null
                  ? null
                  : '${m.emergencyName} (${m.emergencyPhone ?? '-'})',
            ),
            line('Registered', formatDateTime(m.registeredAt)),
            line(
              'Trainer',
              (ref.watch(trainersProvider).value ?? const <Trainer>[])
                  .where((t) => t.id == m.trainerId)
                  .map((t) => t.name)
                  .firstOrNull,
            ),
            line(
              'Gym visits',
              '${ref.watch(memberAttendanceCountProvider(m.id)).value ?? 0}',
            ),
            line(
              'WhatsApp consent',
              m.whatsappConsent
                  ? 'Yes, since ${formatDateTime(m.whatsappConsentAt ?? m.registeredAt)}'
                  : 'No',
            ),
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: OutlinedButton.icon(
                key: const Key('assignTrainer'),
                onPressed: () => assignTrainerDialog(context, ref, m, session),
                icon: const Icon(Icons.sports_gymnastics),
                label: const Text('Assign trainer'),
              ),
            ),
            if (m.medicalNotes != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: OutlinedButton.icon(
                  key: const Key('revealMedical'),
                  onPressed: () => revealMedicalNotes(context, ref, m, session),
                  icon: const Icon(Icons.lock_outline),
                  label: const Text('Show medical notes'),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _MembershipCard extends ConsumerWidget {
  const _MembershipCard(
    this.m, {
    required this.today,
    required this.paid,
    required this.session,
  });
  final Membership m;
  final DateTime today;
  final int paid;
  final OwnerSession session;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final status = effectiveMembershipStatus(m, today);
    final balance = m.pricePaise - paid;
    final repo = ref.read(membershipRepositoryProvider);
    final id = session.ownerId;

    Future<void> act(Future<Object?> Function() f, {required String ok}) async {
      await runGuarded(context, () async => f(), success: ok);
    }

    final canPay = m.status != 'cancelled' && balance > 0;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    '${m.planName} · ${formatDate(m.startDate)} to ${formatDate(m.endDate)}',
                    style: Theme.of(context).textTheme.titleSmall,
                  ),
                ),
                StatusChip(status),
                PopupMenuButton<String>(
                  key: Key('membershipMenu-${m.id}'),
                  onSelected: (v) async {
                    switch (v) {
                      case 'pay':
                        await recordPaymentDialog(
                          context,
                          ref,
                          m,
                          balance,
                          session,
                        );
                      case 'freeze':
                        final r = await askReason(
                          context,
                          title: 'Freeze membership',
                          confirmLabel: 'Freeze',
                        );
                        if (r != null && context.mounted) {
                          await act(
                            () => repo.freeze(
                              membershipId: m.id,
                              actorId: id,
                              reason: r,
                            ),
                            ok: 'Membership frozen',
                          );
                        }
                      case 'unfreeze':
                        await act(
                          () => repo.unfreeze(membershipId: m.id, actorId: id),
                          ok: 'Membership resumed; end date extended',
                        );
                      case 'extend':
                        await extendDialog(context, ref, m, session);
                      case 'cancel':
                        final r = await askReason(
                          context,
                          title: 'Cancel membership',
                          confirmLabel: 'Cancel membership',
                        );
                        if (r != null && context.mounted) {
                          await act(
                            () => repo.cancel(
                              membershipId: m.id,
                              actorId: id,
                              reason: r,
                            ),
                            ok: 'Membership cancelled',
                          );
                        }
                      case 'override':
                        final r = await askReason(
                          context,
                          title: 'Start with balance due',
                          label: 'Reason (recorded in the audit log)',
                          confirmLabel: 'Start membership',
                          minLength: 5,
                        );
                        if (r != null && context.mounted) {
                          await act(
                            () => repo.activateWithBalance(
                              membershipId: m.id,
                              actorId: id,
                              reason: r,
                            ),
                            ok: 'Membership started with balance due',
                          );
                        }
                    }
                  },
                  itemBuilder: (_) => [
                    if (canPay)
                      const PopupMenuItem(
                        value: 'pay',
                        child: Text('Record payment'),
                      ),
                    if (m.status == 'pending_payment')
                      const PopupMenuItem(
                        value: 'override',
                        child: Text('Start with balance due'),
                      ),
                    if (status == 'active')
                      const PopupMenuItem(
                        value: 'freeze',
                        child: Text('Freeze'),
                      ),
                    if (m.status == 'frozen')
                      const PopupMenuItem(
                        value: 'unfreeze',
                        child: Text('Unfreeze'),
                      ),
                    if (m.status == 'active' || m.status == 'frozen')
                      const PopupMenuItem(
                        value: 'extend',
                        child: Text('Extend'),
                      ),
                    if (m.status != 'cancelled')
                      const PopupMenuItem(
                        value: 'cancel',
                        child: Text('Cancel membership'),
                      ),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 4),
            Text(
              'Price ${Money.formatPaise(m.pricePaise)} · '
              'Paid ${Money.formatPaise(paid)}'
              '${m.status == 'cancelled' || balance <= 0 ? '' : ' · Balance due ${Money.formatPaise(balance)}'}',
            ),
            if (m.statusReason != null) Text('Reason: ${m.statusReason}'),
          ],
        ),
      ),
    );
  }
}

class _PaymentTile extends ConsumerWidget {
  const _PaymentTile(this.p, this.session);
  final Payment p;
  final OwnerSession session;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final repo = ref.read(paymentRepositoryProvider);
    return Card(
      child: ListTile(
        key: Key('payment-${p.id}'),
        title: Text(
          '${Money.formatPaise(p.amountPaise)} · ${p.method.toUpperCase()}',
        ),
        subtitle: Text(
          '${formatDateTime(p.createdAt)}'
          '${p.reference == null ? '' : ' · Ref ${p.reference}'}'
          '${p.note == null ? '' : '\n${p.note}'}',
        ),
        isThreeLine: p.note != null,
        trailing: Wrap(
          spacing: 4,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            StatusChip(p.status),
            if (p.status == PaymentStatus.pending.name) ...[
              TextButton(
                key: Key('verify-${p.id}'),
                onPressed: () => verifyPaymentDialog(context, ref, p, session),
                child: const Text('Verify'),
              ),
              TextButton(
                onPressed: () async {
                  final r = await askReason(
                    context,
                    title: 'Reject payment',
                    label: 'Why was it not received?',
                    confirmLabel: 'Reject',
                  );
                  if (r != null && context.mounted) {
                    await runGuarded(
                      context,
                      () => repo.rejectPayment(
                        paymentId: p.id,
                        actorId: session.ownerId,
                        reason: r,
                      ),
                      success: 'Payment rejected',
                    );
                  }
                },
                child: const Text('Reject'),
              ),
            ],
            if (p.status == PaymentStatus.verified.name)
              TextButton(
                onPressed: () => refundDialog(context, ref, p, session),
                child: const Text('Refund'),
              ),
          ],
        ),
      ),
    );
  }
}
