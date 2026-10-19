import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:path/path.dart' as p;

import '../../services/backup_controller.dart';
import '../../shared/formatters.dart';

/// Encrypted backups to a folder (USB drive, OneDrive folder, ...) and restore.
class BackupCard extends ConsumerStatefulWidget {
  const BackupCard({super.key});

  @override
  ConsumerState<BackupCard> createState() => _BackupCardState();
}

class _BackupCardState extends ConsumerState<BackupCard> {
  final _folder = TextEditingController();
  final _password = TextEditingController();
  final _restorePath = TextEditingController();
  final _restorePass = TextEditingController();
  bool _remember = false;

  @override
  void initState() {
    super.initState();
    Future.microtask(() async {
      await ref.read(backupControllerProvider.notifier).refresh();
      if (mounted) {
        _folder.text = ref.read(backupControllerProvider).folder ?? '';
      }
    });
  }

  @override
  void dispose() {
    for (final c in [_folder, _password, _restorePath, _restorePass]) {
      c.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final s = ref.watch(backupControllerProvider);
    final ctl = ref.read(backupControllerProvider.notifier);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Backup & restore',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 4),
            Text(
              s.lastBackup == null
                  ? 'No backup yet.'
                  : 'Last backup: ${formatDateTime(s.lastBackup!)}',
              key: const Key('backupStatus'),
            ),
            if (s.restoreStaged)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  'A restore is ready. Close and reopen the app to apply it.',
                  key: const Key('restoreStaged'),
                  style: TextStyle(
                    color: Theme.of(context).colorScheme.error,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('backupFolder'),
              controller: _folder,
              decoration: const InputDecoration(
                labelText: 'Backup folder',
                helperText: 'Use a USB drive or a cloud-synced folder',
              ),
              onSubmitted: ctl.setFolder,
            ),
            TextField(
              key: const Key('backupPassword'),
              controller: _password,
              obscureText: true,
              decoration: const InputDecoration(
                labelText: 'Backup password (min 8 characters)',
                helperText: 'Needed to restore. If you lose it the backup cannot be opened.',
              ),
            ),
            CheckboxListTile(
              contentPadding: EdgeInsets.zero,
              controlAffinity: ListTileControlAffinity.leading,
              value: _remember || s.passwordRemembered,
              onChanged: (v) async {
                if (s.passwordRemembered && v == false) {
                  await ctl.forgetPassword();
                }
                setState(() => _remember = v ?? false);
              },
              title: const Text('Back up automatically every day'),
              subtitle: const Text('Remembers the password on this PC.'),
            ),
            FilledButton.icon(
              key: const Key('backupNow'),
              onPressed: s.busy
                  ? null
                  : () async {
                      await ctl.setFolder(_folder.text);
                      await ctl.backupNow(_password.text, remember: _remember);
                    },
              icon: const Icon(Icons.backup),
              label: Text(s.busy ? 'Working…' : 'Back up now'),
            ),
            if (s.recent.isNotEmpty) ...[
              const SizedBox(height: 12),
              Text(
                'Recent backups',
                style: Theme.of(context).textTheme.labelLarge,
              ),
              for (final f in s.recent.take(5))
                ListTile(
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                  title: Text(p.basename(f.path)),
                  onTap: () => _restorePath.text = f.path,
                ),
            ],
            const Divider(height: 32),
            Text('Restore', style: Theme.of(context).textTheme.titleSmall),
            TextField(
              key: const Key('restorePath'),
              controller: _restorePath,
              decoration: const InputDecoration(
                labelText: 'Backup file (.efbak)',
              ),
            ),
            TextField(
              key: const Key('restorePassword'),
              controller: _restorePass,
              obscureText: true,
              decoration: const InputDecoration(labelText: 'Backup password'),
            ),
            const SizedBox(height: 8),
            OutlinedButton(
              key: const Key('restoreVerify'),
              onPressed: s.busy
                  ? null
                  : () =>
                        ctl.stageRestore(_restorePath.text, _restorePass.text),
              child: const Text('Verify and prepare restore'),
            ),
            if (s.message != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(s.message!, key: const Key('backupMessage')),
              ),
          ],
        ),
      ),
    );
  }
}
