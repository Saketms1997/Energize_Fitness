import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:qr_flutter/qr_flutter.dart';

import '../../core/money.dart';
import '../../core/providers.dart';
import '../../database/app_database.dart';
import '../../models/enums.dart';
import '../../repositories/ids.dart';
import '../../repositories/member_repository.dart';
import '../../repositories/support.dart';
import '../../services/session_controller.dart';
import '../../services/upi.dart';
import '../../shared/dialogs.dart';
import '../../shared/formatters.dart';

/// Generic modal: [body] builds the fields, [onSubmit] performs the action and
/// throws on failure (the message is shown inline and the dialog stays open).
class _ActionDialog extends StatefulWidget {
  const _ActionDialog({
    required this.title,
    required this.confirmLabel,
    required this.body,
    required this.onSubmit,
    this.confirmKey = const Key('dialogConfirm'),
  });

  final String title;
  final String confirmLabel;
  final Key confirmKey;
  final List<Widget> Function(void Function(VoidCallback) setState) body;
  final Future<void> Function() onSubmit;

  @override
  State<_ActionDialog> createState() => _ActionDialogState();
}

class _ActionDialogState extends State<_ActionDialog> {
  String? _error;
  bool _busy = false;

  Future<void> _submit() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.onSubmit();
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = describeError(e);
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text(widget.title),
    content: SizedBox(
      width: 440,
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            ...widget.body(setState),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Text(
                  _error!,
                  key: const Key('dialogError'),
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ),
          ],
        ),
      ),
    ),
    actions: [
      TextButton(
        onPressed: _busy ? null : () => Navigator.pop(context, false),
        child: const Text('Cancel'),
      ),
      FilledButton(
        key: widget.confirmKey,
        onPressed: _busy ? null : _submit,
        child: Text(widget.confirmLabel),
      ),
    ],
  );
}

Future<bool> _show(BuildContext context, _ActionDialog d) async =>
    await showDialog<bool>(context: context, builder: (_) => d) ?? false;

Widget _gap(Widget w) =>
    Padding(padding: const EdgeInsets.only(top: 12), child: w);

String _rupees(int paise) => (paise / 100).toStringAsFixed(2);

// ---------------------------------------------------------------- renew

Future<void> renewDialog(
  BuildContext context,
  WidgetRef ref,
  Member member,
  OwnerSession session,
) async {
  final plans =
      ref.read(plansProvider).value?.where((p) => p.isActive).toList() ??
      <MembershipPlan>[];
  if (plans.isEmpty) {
    showMessage(context, 'Create a plan first (Plans tab).');
    return;
  }
  MembershipPlan? plan;
  final amount = TextEditingController();
  final ok = await _show(
    context,
    _ActionDialog(
      title: 'Renew / new plan',
      confirmLabel: 'Create membership',
      confirmKey: const Key('renewConfirm'),
      body: (setState) => [
        DropdownButtonFormField<MembershipPlan>(
          key: const Key('renewPlan'),
          initialValue: plan,
          decoration: const InputDecoration(labelText: 'Plan'),
          items: [
            for (final p in plans)
              DropdownMenuItem(
                value: p,
                child: Text('${p.name} · ${Money.formatPaise(p.pricePaise)}'),
              ),
          ],
          onChanged: (p) => setState(() {
            plan = p;
            if (p != null) amount.text = _rupees(p.pricePaise);
          }),
        ),
        _gap(
          TextField(
            key: const Key('renewAmount'),
            controller: amount,
            decoration: const InputDecoration(labelText: 'Amount (₹)'),
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
          ),
        ),
      ],
      onSubmit: () async {
        final p = plan;
        if (p == null) throw DomainException('Choose a plan');
        final price = Money.parseRupeesToPaise(amount.text);
        if (price == null) throw DomainException('Enter a valid amount');
        await ref
            .read(membershipRepositoryProvider)
            .renew(
              memberId: member.id,
              actorId: session.ownerId,
              planId: p.id,
              pricePaise: price,
            );
      },
    ),
  );
  if (ok && context.mounted) {
    showMessage(context, 'Membership created. Record a payment to start it.');
  }
}

// ---------------------------------------------------------------- edit

Future<void> editMemberDialog(
  BuildContext context,
  WidgetRef ref,
  Member m,
  OwnerSession session,
) async {
  final name = TextEditingController(text: m.fullName);
  final phone = TextEditingController(text: m.phone);
  final alt = TextEditingController(text: m.altPhone ?? '');
  final age = TextEditingController(text: m.ageYears?.toString() ?? '');
  final address = TextEditingController(text: m.address);
  final emName = TextEditingController(text: m.emergencyName ?? '');
  final emPhone = TextEditingController(text: m.emergencyPhone ?? '');
  final medical = TextEditingController(text: m.medicalNotes ?? '');
  DateTime? dob = m.dateOfBirth;
  var consent = m.whatsappConsent;

  Future<void> save({String? override}) => ref
      .read(memberRepositoryProvider)
      .updateMember(
        memberId: m.id,
        expectedVersion: m.version,
        actorId: session.ownerId,
        duplicateOverrideReason: override,
        input: MemberInput(
          fullName: name.text,
          phone: phone.text,
          altPhone: alt.text,
          dateOfBirth: dob,
          ageYears: int.tryParse(age.text.trim()),
          address: address.text,
          emergencyName: emName.text,
          emergencyPhone: emPhone.text,
          medicalNotes: medical.text,
          whatsappConsent: consent,
        ),
      );

  Widget field(TextEditingController c, String label, {String? key}) => _gap(
    TextField(
      key: key == null ? null : Key(key),
      controller: c,
      decoration: InputDecoration(labelText: label),
    ),
  );

  final ok = await _show(
    context,
    _ActionDialog(
      title: 'Edit member',
      confirmLabel: 'Save',
      confirmKey: const Key('editConfirm'),
      body: (setState) => [
        field(name, 'Full name', key: 'editName'),
        field(phone, 'Mobile number', key: 'editPhone'),
        field(alt, 'Alternate number'),
        _gap(
          Row(
            children: [
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: () async {
                    final now = DateTime.now();
                    final picked = await showDatePicker(
                      context: context,
                      initialDate: dob ?? DateTime(now.year - 25),
                      firstDate: DateTime(1900),
                      lastDate: now,
                    );
                    if (picked != null) {
                      setState(() => dob = pickedToDateOnly(picked));
                    }
                  },
                  icon: const Icon(Icons.cake_outlined),
                  label: Text(dob == null ? 'Date of birth' : formatDate(dob!)),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: TextField(
                  controller: age,
                  decoration: const InputDecoration(labelText: 'or Age'),
                  keyboardType: TextInputType.number,
                ),
              ),
            ],
          ),
        ),
        field(address, 'Address'),
        field(emName, 'Emergency contact name'),
        field(emPhone, 'Emergency contact number'),
        field(medical, 'Medical notes'),
        CheckboxListTile(
          value: consent,
          onChanged: (v) => setState(() => consent = v ?? false),
          contentPadding: EdgeInsets.zero,
          controlAffinity: ListTileControlAffinity.leading,
          title: const Text('Agrees to WhatsApp messages'),
        ),
      ],
      onSubmit: () async {
        try {
          await save();
        } on DuplicateMemberException catch (e) {
          if (!context.mounted) rethrow;
          final names = e.candidates.map((c) => c.member.fullName).join(', ');
          final reason = await askReason(
            context,
            title: 'Number already used by $names',
            label: 'Why is this number shared? (audit log)',
            confirmLabel: 'Save anyway',
            minLength: 5,
          );
          if (reason == null) throw DomainException('Not saved');
          await save(override: reason);
        }
      },
    ),
  );
  if (ok && context.mounted) showMessage(context, 'Member updated');
}

// ---------------------------------------------------------------- medical

Future<void> revealMedicalNotes(
  BuildContext context,
  WidgetRef ref,
  Member m,
  OwnerSession session,
) async {
  final password = TextEditingController();
  final ok = await _show(
    context,
    _ActionDialog(
      title: 'Confirm your password',
      confirmLabel: 'Show',
      confirmKey: const Key('medicalConfirm'),
      body: (_) => [
        const Text('Medical notes are private. Enter your password to view.'),
        _gap(
          TextField(
            key: const Key('medicalPassword'),
            controller: password,
            obscureText: true,
            autofocus: true,
            decoration: const InputDecoration(labelText: 'Password'),
          ),
        ),
      ],
      onSubmit: () async {
        final good = await ref
            .read(authRepositoryProvider)
            .verifyPassword(session.ownerId, password.text);
        if (!good) throw DomainException('Incorrect password.');
      },
    ),
  );
  if (!ok || !context.mounted) return;
  final notes = await ref
      .read(memberRepositoryProvider)
      .revealMedicalNotes(memberId: m.id, actorId: session.ownerId);
  if (!context.mounted) return;
  await showDialog<void>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: const Text('Medical notes'),
      content: Text(
        notes == null || notes.isEmpty ? 'None recorded.' : notes,
        key: const Key('medicalText'),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(ctx),
          child: const Text('Close'),
        ),
      ],
    ),
  );
}

// ---------------------------------------------------------------- payments

Future<void> recordPaymentDialog(
  BuildContext context,
  WidgetRef ref,
  Membership m,
  int balance,
  OwnerSession session,
) async {
  final amount = TextEditingController(text: _rupees(balance));
  final reference = TextEditingController();
  final note = TextEditingController();
  var method = PaymentMethod.upi;
  final key =
      newId(); // one key per dialog: a double click cannot double-record
  final ok = await _show(
    context,
    _ActionDialog(
      title: 'Record payment',
      confirmLabel: 'Record',
      confirmKey: const Key('payConfirm'),
      body: (setState) => [
        Text('Balance due ${Money.formatPaise(balance)}'),
        _gap(
          TextField(
            key: const Key('payAmount'),
            controller: amount,
            onChanged: (_) => setState(() {}),
            decoration: const InputDecoration(labelText: 'Amount (₹)'),
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
          ),
        ),
        _gap(
          DropdownButtonFormField<PaymentMethod>(
            key: const Key('payMethod'),
            initialValue: method,
            decoration: const InputDecoration(labelText: 'Method'),
            items: [
              for (final p in PaymentMethod.values)
                DropdownMenuItem(value: p, child: Text(p.name.toUpperCase())),
            ],
            onChanged: (v) => setState(() => method = v ?? method),
          ),
        ),
        if (method == PaymentMethod.upi) ..._upiQr(ref, m, amount.text),
        if (method == PaymentMethod.upi)
          _gap(
            TextField(
              key: const Key('payReference'),
              controller: reference,
              decoration: const InputDecoration(
                labelText: 'UPI transaction reference',
              ),
            ),
          ),
        _gap(
          TextField(
            controller: note,
            decoration: const InputDecoration(labelText: 'Note (optional)'),
          ),
        ),
      ],
      onSubmit: () async {
        final paise = Money.parseRupeesToPaise(amount.text);
        if (paise == null) throw DomainException('Enter a valid amount');
        if (method == PaymentMethod.upi && reference.text.trim().isEmpty) {
          throw DomainException('Enter the UPI transaction reference');
        }
        await ref
            .read(paymentRepositoryProvider)
            .recordPayment(
              actorId: session.ownerId,
              membershipId: m.id,
              amountPaise: paise,
              method: method,
              reference: method == PaymentMethod.upi ? reference.text : null,
              note: note.text.isEmpty ? null : note.text,
              idempotencyKey: key,
            );
      },
    ),
  );
  if (ok && context.mounted) {
    showMessage(context, 'Payment recorded. Verify it once the money arrives.');
  }
}

Future<void> verifyPaymentDialog(
  BuildContext context,
  WidgetRef ref,
  Payment p,
  OwnerSession session,
) async {
  final received = TextEditingController();
  final ok = await _show(
    context,
    _ActionDialog(
      title: 'Verify payment',
      confirmLabel: 'Verify',
      confirmKey: const Key('verifyConfirm'),
      body: (_) => [
        Text(
          'Recorded ${Money.formatPaise(p.amountPaise)} by ${p.method.toUpperCase()}'
          '${p.reference == null ? '' : ' (Ref ${p.reference})'}.',
        ),
        const SizedBox(height: 4),
        const Text(
          'Check your bank / cash drawer, then type the amount received.',
        ),
        _gap(
          TextField(
            key: const Key('verifyAmount'),
            controller: received,
            autofocus: true,
            decoration: const InputDecoration(labelText: 'Amount received (₹)'),
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
          ),
        ),
      ],
      onSubmit: () async {
        final paise = Money.parseRupeesToPaise(received.text);
        if (paise == null) throw DomainException('Enter a valid amount');
        await ref
            .read(paymentRepositoryProvider)
            .verifyPayment(
              paymentId: p.id,
              actorId: session.ownerId,
              confirmedAmountPaise: paise,
            );
        // Receipt is a courtesy: never let it fail the verification.
        try {
          await ref
              .read(messageRepositoryProvider)
              .queueReceipt(p.id, session.ownerId);
        } catch (_) {}
      },
    ),
  );
  if (ok && context.mounted) showMessage(context, 'Payment verified');
}

Future<void> refundDialog(
  BuildContext context,
  WidgetRef ref,
  Payment p,
  OwnerSession session,
) async {
  final reason = TextEditingController();
  var cancel = false;
  final ok = await _show(
    context,
    _ActionDialog(
      title: 'Refund ${Money.formatPaise(p.amountPaise)}',
      confirmLabel: 'Refund',
      confirmKey: const Key('refundConfirm'),
      body: (setState) => [
        const Text('Record this only after you have returned the money.'),
        _gap(
          TextField(
            key: const Key('refundReason'),
            controller: reason,
            decoration: const InputDecoration(labelText: 'Reason'),
          ),
        ),
        CheckboxListTile(
          value: cancel,
          onChanged: (v) => setState(() => cancel = v ?? false),
          contentPadding: EdgeInsets.zero,
          controlAffinity: ListTileControlAffinity.leading,
          title: const Text('Also cancel the membership'),
        ),
      ],
      onSubmit: () => ref
          .read(paymentRepositoryProvider)
          .refundPayment(
            paymentId: p.id,
            actorId: session.ownerId,
            reason: reason.text,
            cancelMembership: cancel,
          ),
    ),
  );
  if (ok && context.mounted) showMessage(context, 'Payment refunded');
}

// ---------------------------------------------------------------- extend

Future<void> extendDialog(
  BuildContext context,
  WidgetRef ref,
  Membership m,
  OwnerSession session,
) async {
  final days = TextEditingController();
  final reason = TextEditingController();
  final ok = await _show(
    context,
    _ActionDialog(
      title: 'Extend membership',
      confirmLabel: 'Extend',
      confirmKey: const Key('extendConfirm'),
      body: (_) => [
        TextField(
          key: const Key('extendDays'),
          controller: days,
          decoration: const InputDecoration(labelText: 'Days to add'),
          keyboardType: TextInputType.number,
        ),
        _gap(
          TextField(
            key: const Key('extendReason'),
            controller: reason,
            decoration: const InputDecoration(labelText: 'Reason'),
          ),
        ),
      ],
      onSubmit: () async {
        final n = int.tryParse(days.text.trim());
        if (n == null) throw DomainException('Enter the number of days');
        await ref
            .read(membershipRepositoryProvider)
            .extend(
              membershipId: m.id,
              actorId: session.ownerId,
              days: n,
              reason: reason.text,
            );
      },
    ),
  );
  if (ok && context.mounted) showMessage(context, 'Membership extended');
}

/// QR code the member scans with any UPI app. Shows a hint when the gym has
/// no UPI ID yet. The amount follows what is typed in the amount field.
List<Widget> _upiQr(WidgetRef ref, Membership m, String amountText) {
  final gym = ref.read(currentGymProvider).value;
  final link = UpiLink.build(
    upiId: gym?.upiId,
    payeeName: gym?.upiPayeeName ?? gym?.name ?? '',
    amountPaise: Money.parseRupeesToPaise(amountText) ?? 0,
    note: m.planName,
  );
  if (link == null) {
    return [
      _gap(
        const Text(
          'Add the gym UPI ID in Settings to show a payment QR code here.',
          key: Key('upiMissing'),
        ),
      ),
    ];
  }
  return [
    _gap(
      Center(
        child: Container(
          color: Colors.white,
          padding: const EdgeInsets.all(8),
          child: QrImageView(
            key: const Key('upiQr'),
            data: link,
            size: 180,
            backgroundColor: Colors.white,
          ),
        ),
      ),
    ),
    const SizedBox(height: 4),
    Center(child: Text('Scan to pay ${gym?.upiId}')),
  ];
}

// ---------------------------------------------------------------- trainer

Future<void> assignTrainerDialog(
  BuildContext context,
  WidgetRef ref,
  Member m,
  OwnerSession session,
) async {
  final all = ref.read(trainersProvider).value ?? const <Trainer>[];
  final choices = all.where((t) => t.isActive || t.id == m.trainerId).toList();
  String? selected = m.trainerId;
  final ok = await _show(
    context,
    _ActionDialog(
      title: 'Assign trainer',
      confirmLabel: 'Save',
      confirmKey: const Key('assignConfirm'),
      body: (setState) => [
        if (choices.isEmpty)
          const Text('Add a trainer first (Trainers tab).')
        else
          DropdownButtonFormField<String?>(
            key: const Key('assignPicker'),
            initialValue: selected,
            decoration: const InputDecoration(labelText: 'Trainer'),
            items: [
              const DropdownMenuItem<String?>(
                value: null,
                child: Text('No trainer'),
              ),
              for (final t in choices)
                DropdownMenuItem<String?>(value: t.id, child: Text(t.name)),
            ],
            onChanged: (v) => setState(() => selected = v),
          ),
      ],
      onSubmit: () => ref
          .read(trainerRepositoryProvider)
          .assign(
            memberId: m.id,
            trainerId: selected,
            actorId: session.ownerId,
          ),
    ),
  );
  if (ok && context.mounted) showMessage(context, 'Trainer updated');
}
