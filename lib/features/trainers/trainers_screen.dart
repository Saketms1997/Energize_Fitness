import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import '../../database/app_database.dart';
import '../../repositories/trainer_repository.dart';
import '../../services/session_controller.dart';
import '../../shared/dialogs.dart';

class TrainersScreen extends ConsumerWidget {
  const TrainersScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final trainers = ref.watch(trainersProvider);
    final session = ref.watch(sessionProvider);
    if (session == null) return const SizedBox.shrink();
    return Align(
      alignment: Alignment.topCenter,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 720),
        child: ListView(
          padding: const EdgeInsets.all(24),
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    'Trainers',
                    style: Theme.of(context).textTheme.headlineSmall,
                  ),
                ),
                FilledButton.icon(
                  key: const Key('addTrainer'),
                  onPressed: () => _editDialog(context, ref, session, null),
                  icon: const Icon(Icons.add),
                  label: const Text('Add trainer'),
                ),
              ],
            ),
            const SizedBox(height: 12),
            trainers.when(
              loading: () => const Center(child: CircularProgressIndicator()),
              error: (_, _) => const Text('Could not load trainers.'),
              data: (list) => list.isEmpty
                  ? const Text('No trainers yet.')
                  : Column(
                      children: [
                        for (final t in list)
                          Card(
                            child: ListTile(
                              key: Key('trainer-${t.name}'),
                              title: Text(t.name),
                              subtitle: Text(
                                [
                                  if (t.specialty != null) t.specialty!,
                                  if (t.phone != null) '+91 ${t.phone}',
                                  if (!t.isActive) 'Inactive',
                                ].join(' · '),
                              ),
                              trailing: IconButton(
                                icon: const Icon(Icons.edit),
                                onPressed: () =>
                                    _editDialog(context, ref, session, t),
                              ),
                            ),
                          ),
                      ],
                    ),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _editDialog(
    BuildContext context,
    WidgetRef ref,
    OwnerSession session,
    Trainer? t,
  ) async {
    final name = TextEditingController(text: t?.name ?? '');
    final phone = TextEditingController(text: t?.phone ?? '');
    final specialty = TextEditingController(text: t?.specialty ?? '');
    var active = t?.isActive ?? true;
    String? error;
    final repo = ref.read(trainerRepositoryProvider);
    await showDialog<void>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setState) => AlertDialog(
          title: Text(t == null ? 'Add trainer' : 'Edit trainer'),
          content: SizedBox(
            width: 400,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                TextField(
                  key: const Key('trainerName'),
                  controller: name,
                  decoration: const InputDecoration(labelText: 'Name'),
                ),
                TextField(
                  key: const Key('trainerPhone'),
                  controller: phone,
                  decoration: const InputDecoration(
                    labelText: 'Phone (optional)',
                  ),
                ),
                TextField(
                  controller: specialty,
                  decoration: const InputDecoration(
                    labelText: 'Specialty (optional)',
                  ),
                ),
                if (t != null)
                  SwitchListTile(
                    contentPadding: EdgeInsets.zero,
                    title: const Text('Active'),
                    value: active,
                    onChanged: (v) => setState(() => active = v),
                  ),
                if (error != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Text(
                      error!,
                      style: TextStyle(color: Theme.of(ctx).colorScheme.error),
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
              key: const Key('trainerSave'),
              onPressed: () async {
                final input = TrainerInput(
                  name: name.text,
                  phone: phone.text,
                  specialty: specialty.text,
                );
                try {
                  if (t == null) {
                    await repo.create(
                      gymId: session.gymId,
                      actorId: session.ownerId,
                      input: input,
                    );
                  } else {
                    await repo.update(
                      trainerId: t.id,
                      expectedVersion: t.version,
                      actorId: session.ownerId,
                      input: input,
                      isActive: active,
                    );
                  }
                  if (ctx.mounted) Navigator.pop(ctx);
                } catch (e) {
                  setState(() => error = describeError(e));
                }
              },
              child: const Text('Save'),
            ),
          ],
        ),
      ),
    );
  }
}
