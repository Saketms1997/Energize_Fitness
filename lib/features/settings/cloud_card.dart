import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../shared/formatters.dart';
import '../../sync/cloud_controller.dart';

/// Cloud sync / online backup connection. Optional: the app is fully usable
/// offline without it.
class CloudCard extends ConsumerStatefulWidget {
  const CloudCard({super.key});

  @override
  ConsumerState<CloudCard> createState() => _CloudCardState();
}

class _CloudCardState extends ConsumerState<CloudCard> {
  final _url = TextEditingController(
    text: const String.fromEnvironment('SUPABASE_URL'),
  );
  final _key = TextEditingController(
    text: const String.fromEnvironment('SUPABASE_ANON_KEY'),
  );
  final _email = TextEditingController();
  final _password = TextEditingController();
  bool _create = false;

  @override
  void initState() {
    super.initState();
    Future.microtask(() async {
      await ref.read(cloudControllerProvider.notifier).refresh();
      final s = ref.read(cloudControllerProvider);
      if (!mounted) return;
      if (s.projectUrl != null) _url.text = s.projectUrl!;
      if (s.anonKey != null) _key.text = s.anonKey!;
      if (s.email != null) _email.text = s.email!;
    });
  }

  @override
  void dispose() {
    for (final c in [_url, _key, _email, _password]) {
      c.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final s = ref.watch(cloudControllerProvider);
    final ctl = ref.read(cloudControllerProvider.notifier);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Cloud sync & online backup',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 4),
            Text(
              s.signedIn
                  ? 'Connected as ${s.email}. ${s.pending == 0 ? 'Everything is uploaded.' : '${s.pending} change(s) waiting to upload.'}'
                  : 'Not connected. Your data is only on this device.',
              key: const Key('cloudStatus'),
            ),
            if (s.lastSync != null)
              Text('Last successful sync: ${formatDateTime(s.lastSync!)}'),
            const SizedBox(height: 12),
            if (!s.signedIn) ...[
              TextField(
                key: const Key('cloudUrl'),
                controller: _url,
                decoration: const InputDecoration(
                  labelText: 'Project URL (https://xxxx.supabase.co)',
                ),
              ),
              TextField(
                key: const Key('cloudKey'),
                controller: _key,
                decoration: const InputDecoration(
                  labelText: 'Public (anon) key',
                ),
              ),
              TextField(
                key: const Key('cloudEmail'),
                controller: _email,
                decoration: const InputDecoration(labelText: 'Cloud email'),
              ),
              TextField(
                key: const Key('cloudPassword'),
                controller: _password,
                obscureText: true,
                decoration: const InputDecoration(labelText: 'Cloud password'),
              ),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                controlAffinity: ListTileControlAffinity.leading,
                value: _create,
                onChanged: (v) => setState(() => _create = v ?? false),
                title: const Text('Create this cloud account (first time)'),
              ),
              FilledButton(
                key: const Key('cloudConnect'),
                onPressed: s.busy
                    ? null
                    : () => ctl.connect(
                        projectUrl: _url.text,
                        anonKey: _key.text,
                        email: _email.text,
                        password: _password.text,
                        createAccount: _create,
                      ),
                child: Text(s.busy ? 'Connecting…' : 'Connect'),
              ),
            ] else ...[
              Wrap(
                spacing: 8,
                children: [
                  FilledButton.icon(
                    key: const Key('cloudSyncNow'),
                    onPressed: s.busy ? null : () => ctl.syncNow(),
                    icon: const Icon(Icons.sync),
                    label: Text(s.busy ? 'Syncing…' : 'Sync now'),
                  ),
                  OutlinedButton(
                    key: const Key('cloudDisconnect'),
                    onPressed: s.busy ? null : ctl.disconnect,
                    child: const Text('Disconnect'),
                  ),
                ],
              ),
              SwitchListTile(
                key: const Key('whatsappAuto'),
                contentPadding: EdgeInsets.zero,
                title: const Text('Send WhatsApp messages automatically'),
                subtitle: const Text(
                  'Needs the WhatsApp functions deployed (see docs/SETUP_GUIDE.md).',
                ),
                value: s.whatsappAuto,
                onChanged: ctl.setWhatsappAuto,
              ),
            ],
            if (s.message != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(s.message!, key: const Key('cloudMessage')),
              ),
          ],
        ),
      ),
    );
  }
}
