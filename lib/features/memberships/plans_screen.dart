import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/money.dart';
import '../../core/providers.dart';
import '../../core/validators.dart';
import '../../database/app_database.dart';
import '../../repositories/plan_repository.dart';
import '../../services/session_controller.dart';
import '../../shared/dialogs.dart';

class PlansScreen extends ConsumerWidget {
  const PlansScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final plans = ref.watch(plansProvider);
    return Scaffold(
      floatingActionButton: FloatingActionButton.extended(
        key: const Key('addPlan'),
        onPressed: () => _edit(context, ref, null),
        icon: const Icon(Icons.add),
        label: const Text('Add plan'),
      ),
      body: plans.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (_, _) => const Center(child: Text('Could not load plans.')),
        data: (list) => list.isEmpty
            ? const Center(
                child: Text(
                  'No plans yet. Add Monthly, Quarterly, Half-yearly, Annual or a custom plan.',
                ),
              )
            : ListView(
                padding: const EdgeInsets.only(bottom: 88),
                children: [
                  for (final p in list)
                    ListTile(
                      key: Key('plan-${p.name}'),
                      title: Text(p.name),
                      subtitle: Text(
                        '${p.durationValue} ${p.durationUnit} · ${Money.formatPaise(p.pricePaise)}'
                        '${p.isActive ? '' : ' · inactive'}',
                      ),
                      trailing: IconButton(
                        tooltip: 'Edit',
                        icon: const Icon(Icons.edit),
                        onPressed: () => _edit(context, ref, p),
                      ),
                    ),
                ],
              ),
      ),
    );
  }

  Future<void> _edit(
    BuildContext context,
    WidgetRef ref,
    MembershipPlan? existing,
  ) async {
    final session = ref.read(sessionProvider);
    if (session == null) return;
    final result = await showDialog<_PlanForm>(
      context: context,
      builder: (_) => _PlanDialog(existing),
    );
    if (result == null || !context.mounted) return;
    final repo = ref.read(planRepositoryProvider);
    await runGuarded(context, () async {
      if (existing == null) {
        await repo.createPlan(
          gymId: session.gymId,
          actorId: session.ownerId,
          input: result.input,
        );
      } else {
        await repo.updatePlan(
          planId: existing.id,
          expectedVersion: existing.version,
          actorId: session.ownerId,
          input: result.input,
          isActive: result.isActive,
        );
      }
    }, success: existing == null ? 'Plan added' : 'Plan updated');
  }
}

class _PlanForm {
  const _PlanForm(this.input, this.isActive);
  final PlanInput input;
  final bool isActive;
}

class _PlanDialog extends StatefulWidget {
  const _PlanDialog(this.existing);
  final MembershipPlan? existing;

  @override
  State<_PlanDialog> createState() => _PlanDialogState();
}

class _PlanDialogState extends State<_PlanDialog> {
  final _formKey = GlobalKey<FormState>();
  late final _name = TextEditingController(text: widget.existing?.name);
  late final _value = TextEditingController(
    text: widget.existing?.durationValue.toString() ?? '1',
  );
  late final _price = TextEditingController(
    text: widget.existing == null
        ? ''
        : (widget.existing!.pricePaise / 100).toStringAsFixed(2),
  );
  late String _unit = widget.existing?.durationUnit ?? 'months';
  late bool _active = widget.existing?.isActive ?? true;

  static const _presets = <(String, String, int)>[
    ('Monthly', 'months', 1),
    ('Quarterly', 'months', 3),
    ('Half-yearly', 'months', 6),
    ('Annual', 'months', 12),
  ];

  @override
  void dispose() {
    _name.dispose();
    _value.dispose();
    _price.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text(widget.existing == null ? 'Add plan' : 'Edit plan'),
      content: SizedBox(
        width: 380,
        child: Form(
          key: _formKey,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (widget.existing == null)
                  Wrap(
                    spacing: 8,
                    children: [
                      for (final p in _presets)
                        ActionChip(
                          label: Text(p.$1),
                          onPressed: () => setState(() {
                            _name.text = p.$1;
                            _unit = p.$2;
                            _value.text = '${p.$3}';
                          }),
                        ),
                    ],
                  ),
                TextFormField(
                  key: const Key('planName'),
                  controller: _name,
                  decoration: const InputDecoration(labelText: 'Plan name'),
                  validator: (v) => Validators.required(v, field: 'Plan name'),
                ),
                const SizedBox(height: 12),
                Row(
                  children: [
                    Expanded(
                      child: TextFormField(
                        key: const Key('planDuration'),
                        controller: _value,
                        decoration: const InputDecoration(
                          labelText: 'Duration',
                        ),
                        keyboardType: TextInputType.number,
                        validator: (v) {
                          final n = int.tryParse(v ?? '');
                          return (n == null || n < 1) ? 'Enter a number' : null;
                        },
                      ),
                    ),
                    const SizedBox(width: 12),
                    DropdownButton<String>(
                      value: _unit,
                      items: const [
                        DropdownMenuItem(value: 'days', child: Text('days')),
                        DropdownMenuItem(
                          value: 'months',
                          child: Text('months'),
                        ),
                      ],
                      onChanged: (v) => setState(() => _unit = v ?? _unit),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                TextFormField(
                  key: const Key('planPrice'),
                  controller: _price,
                  decoration: const InputDecoration(labelText: 'Price (₹)'),
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                  ),
                  validator: (v) => Money.parseRupeesToPaise(v ?? '') == null
                      ? 'Enter a valid amount'
                      : null,
                ),
                if (widget.existing != null)
                  SwitchListTile(
                    contentPadding: EdgeInsets.zero,
                    title: const Text('Available for new members'),
                    value: _active,
                    onChanged: (v) => setState(() => _active = v),
                  ),
              ],
            ),
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Cancel'),
        ),
        FilledButton(
          key: const Key('planSave'),
          onPressed: () {
            if (!_formKey.currentState!.validate()) return;
            Navigator.pop(
              context,
              _PlanForm(
                PlanInput(
                  name: _name.text,
                  durationUnit: _unit,
                  durationValue: int.parse(_value.text),
                  pricePaise: Money.parseRupeesToPaise(_price.text)!,
                ),
                _active,
              ),
            );
          },
          child: const Text('Save'),
        ),
      ],
    );
  }
}
