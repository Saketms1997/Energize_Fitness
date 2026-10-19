import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/dates.dart';
import '../../core/money.dart';
import '../../core/providers.dart';
import '../../core/router.dart';
import '../../core/validators.dart';
import '../../database/app_database.dart';
import '../../repositories/member_repository.dart';
import '../../services/session_controller.dart';
import '../../shared/dialogs.dart';
import '../../shared/formatters.dart';

class RegistrationScreen extends ConsumerStatefulWidget {
  const RegistrationScreen({super.key});

  @override
  ConsumerState<RegistrationScreen> createState() => _RegistrationScreenState();
}

class _RegistrationScreenState extends ConsumerState<RegistrationScreen> {
  final _formKey = GlobalKey<FormState>();
  final _name = TextEditingController();
  final _phone = TextEditingController();
  final _alt = TextEditingController();
  final _age = TextEditingController();
  final _address = TextEditingController();
  final _emName = TextEditingController();
  final _emPhone = TextEditingController();
  final _medical = TextEditingController();
  final _amount = TextEditingController();
  DateTime? _dob;
  DateTime _start = GymDates.dateOnly(DateTime.now());
  MembershipPlan? _plan;
  bool _consent = false;
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    for (final c in [
      _name,
      _phone,
      _alt,
      _age,
      _address,
      _emName,
      _emPhone,
      _medical,
      _amount,
    ]) {
      c.dispose();
    }
    super.dispose();
  }

  MemberInput _input() => MemberInput(
    fullName: _name.text,
    phone: _phone.text,
    altPhone: _alt.text,
    dateOfBirth: _dob,
    ageYears: int.tryParse(_age.text.trim()),
    address: _address.text,
    emergencyName: _emName.text,
    emergencyPhone: _emPhone.text,
    medicalNotes: _medical.text,
    whatsappConsent: _consent,
  );

  Future<void> _pickDate({required bool dob}) async {
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: dob ? (_dob ?? DateTime(now.year - 25)) : _start,
      firstDate: dob ? DateTime(1900) : DateTime(now.year - 1),
      lastDate: dob ? now : DateTime(now.year + 2),
    );
    if (picked == null) return;
    setState(() {
      if (dob) {
        _dob = pickedToDateOnly(picked);
      } else {
        _start = pickedToDateOnly(picked);
      }
    });
  }

  Future<void> _submit({String? overrideReason}) async {
    if (!_formKey.currentState!.validate()) return;
    final plan = _plan;
    final session = ref.read(sessionProvider);
    if (plan == null || session == null) return;
    final price = Money.parseRupeesToPaise(_amount.text);
    if (price == null) {
      setState(() => _error = 'Enter a valid amount');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final result = await ref
          .read(memberRepositoryProvider)
          .registerMember(
            gymId: session.gymId,
            actorId: session.ownerId,
            input: _input(),
            planId: plan.id,
            startDate: _start,
            pricePaise: price,
            duplicateOverrideReason: overrideReason,
          );
      try {
        await ref
            .read(messageRepositoryProvider)
            .queueWelcome(result.member.id, session.ownerId);
      } catch (_) {}
      if (!mounted) return;
      showMessage(
        context,
        'Registered ${result.member.memberCode}. Membership starts once the payment is verified.',
      );
      context.go(Routes.member(result.member.id));
    } on DuplicateMemberException catch (e) {
      if (mounted) await _resolveDuplicates(e.candidates);
    } catch (e) {
      if (mounted) setState(() => _error = describeError(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _resolveDuplicates(List<DuplicateCandidate> candidates) async {
    final action = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Possible duplicate member'),
        content: SizedBox(
          width: 420,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'Someone matching these details is already registered:',
              ),
              const SizedBox(height: 8),
              for (final c in candidates)
                ListTile(
                  dense: true,
                  title: Text('${c.member.fullName} (${c.member.memberCode})'),
                  subtitle: Text(switch (c.reason) {
                    'same_phone' => 'Same phone number: ${c.member.phone}',
                    'same_name_and_dob' => 'Same name and date of birth',
                    _ => 'Same name',
                  }),
                  trailing: TextButton(
                    onPressed: () => Navigator.pop(ctx, 'open:${c.member.id}'),
                    child: const Text('Open'),
                  ),
                ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Cancel'),
          ),
          FilledButton(
            key: const Key('registerAnyway'),
            onPressed: () => Navigator.pop(ctx, 'anyway'),
            child: const Text('This is a different person'),
          ),
        ],
      ),
    );
    if (!mounted || action == null) return;
    if (action.startsWith('open:')) {
      context.go(Routes.member(action.substring(5)));
    } else if (action == 'anyway') {
      final reason = await askReason(
        context,
        title: 'Why is this a different person?',
        label: 'Reason (recorded in the audit log)',
        confirmLabel: 'Register member',
        minLength: 5,
      );
      if (reason != null) await _submit(overrideReason: reason);
    }
  }

  @override
  Widget build(BuildContext context) {
    final plans =
        ref.watch(plansProvider).value?.where((p) => p.isActive).toList() ?? [];
    return Align(
      alignment: Alignment.topCenter,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 720),
        child: Form(
          key: _formKey,
          child: ListView(
            padding: const EdgeInsets.all(24),
            children: [
              Text(
                'Register member',
                style: Theme.of(context).textTheme.headlineSmall,
              ),
              const SizedBox(height: 16),
              _field(
                _name,
                'Full name',
                key: 'regName',
                validator: (v) => Validators.required(v, field: 'Full name'),
              ),
              _field(
                _phone,
                'WhatsApp / mobile number',
                key: 'regPhone',
                type: TextInputType.phone,
                validator: Validators.indianMobile,
              ),
              _field(
                _alt,
                'Alternate contact number (optional)',
                type: TextInputType.phone,
                validator: (v) => Validators.indianMobile(v, optional: true),
              ),
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: Row(
                  children: [
                    Expanded(
                      child: OutlinedButton.icon(
                        key: const Key('regDob'),
                        onPressed: () => _pickDate(dob: true),
                        icon: const Icon(Icons.cake_outlined),
                        label: Text(
                          _dob == null ? 'Date of birth' : formatDate(_dob!),
                        ),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: TextFormField(
                        key: const Key('regAge'),
                        controller: _age,
                        decoration: const InputDecoration(labelText: 'or Age'),
                        keyboardType: TextInputType.number,
                      ),
                    ),
                  ],
                ),
              ),
              _field(_address, 'Address', lines: 2),
              _field(_emName, 'Emergency contact name'),
              _field(
                _emPhone,
                'Emergency contact number',
                type: TextInputType.phone,
                validator: (v) => Validators.indianMobile(v, optional: true),
              ),
              _field(
                _medical,
                'Medical notes (optional, hidden behind password)',
                lines: 2,
              ),
              const Divider(height: 32),
              DropdownButtonFormField<MembershipPlan>(
                key: const Key('regPlan'),
                initialValue: _plan,
                decoration: const InputDecoration(labelText: 'Membership plan'),
                items: [
                  for (final p in plans)
                    DropdownMenuItem(
                      value: p,
                      child: Text(
                        '${p.name} · ${Money.formatPaise(p.pricePaise)}',
                      ),
                    ),
                ],
                onChanged: (p) => setState(() {
                  _plan = p;
                  if (p != null) {
                    _amount.text = (p.pricePaise / 100).toStringAsFixed(2);
                  }
                }),
                validator: (p) => p == null
                    ? (plans.isEmpty
                          ? 'Create a plan first (Plans tab)'
                          : 'Choose a plan')
                    : null,
              ),
              const SizedBox(height: 12),
              _field(
                _amount,
                'Subscription amount (₹)',
                key: 'regAmount',
                type: const TextInputType.numberWithOptions(decimal: true),
                validator: (v) => Money.parseRupeesToPaise(v ?? '') == null
                    ? 'Enter a valid amount'
                    : null,
              ),
              OutlinedButton.icon(
                onPressed: () => _pickDate(dob: false),
                icon: const Icon(Icons.event),
                label: Text('Start date: ${formatDate(_start)}'),
              ),
              CheckboxListTile(
                key: const Key('regConsent'),
                value: _consent,
                onChanged: (v) => setState(() => _consent = v ?? false),
                contentPadding: EdgeInsets.zero,
                controlAffinity: ListTileControlAffinity.leading,
                title: const Text(
                  'Member agrees to receive WhatsApp messages from the gym',
                ),
              ),
              if (_error != null)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 8),
                  child: Text(
                    _error!,
                    key: const Key('regError'),
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
                ),
              const SizedBox(height: 12),
              FilledButton(
                key: const Key('regSubmit'),
                onPressed: _busy ? null : () => _submit(),
                child: const Text('Register member'),
              ),
              const SizedBox(height: 8),
              const Text(
                'The membership stays "Awaiting payment" until you verify a payment on the next screen.',
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _field(
    TextEditingController c,
    String label, {
    String? key,
    TextInputType? type,
    int lines = 1,
    String? Function(String?)? validator,
  }) => Padding(
    padding: const EdgeInsets.only(bottom: 12),
    child: TextFormField(
      key: key == null ? null : Key(key),
      controller: c,
      decoration: InputDecoration(labelText: label),
      keyboardType: type,
      maxLines: lines,
      validator: validator,
    ),
  );
}
