import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import '../../repositories/gym_repository.dart';
import '../../services/session_controller.dart';
import '../../shared/gym_profile_form.dart';
import 'backup_card.dart';
import 'cloud_card.dart';

/// Edit gym profile (name, contact, UPI). Backup and WhatsApp settings are
/// added in later phases.
class SettingsScreen extends ConsumerStatefulWidget {
  const SettingsScreen({super.key});

  @override
  ConsumerState<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends ConsumerState<SettingsScreen> {
  final _formKey = GlobalKey<FormState>();
  GymProfileFormControllers? _c;
  String? _logoPath;
  String? _message;

  @override
  void dispose() {
    _c?.dispose();
    super.dispose();
  }

  Future<void> _save(String gymId) async {
    if (!_formKey.currentState!.validate()) return;
    final session = ref.read(sessionProvider);
    if (session == null) return;
    try {
      await ref
          .read(gymRepositoryProvider)
          .updateProfile(
            gymId: gymId,
            actorId: session.ownerId,
            input: _c!.toInput(logoPath: _logoPath),
          );
      setState(() => _message = 'Saved on this device. Waiting to sync.');
    } on ValidationException catch (e) {
      setState(() => _message = e.errors.values.join('\n'));
    }
  }

  @override
  Widget build(BuildContext context) {
    final gym = ref.watch(currentGymProvider).value;
    if (gym == null) {
      return const Center(child: CircularProgressIndicator());
    }
    _c ??= GymProfileFormControllers(gym);
    _logoPath ??= gym.logoPath;
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 520),
        child: Form(
          key: _formKey,
          child: ListView(
            padding: const EdgeInsets.all(24),
            children: [
              GymProfileFields(_c!),
              if (_message != null) ...[
                const SizedBox(height: 12),
                Text(_message!, key: const Key('settingsMessage')),
              ],
              const SizedBox(height: 20),
              FilledButton(
                key: const Key('settingsSave'),
                onPressed: () => _save(gym.id),
                child: const Text('Save'),
              ),
              const SizedBox(height: 32),
              const BackupCard(),
              const SizedBox(height: 16),
              const CloudCard(),
            ],
          ),
        ),
      ),
    );
  }
}
