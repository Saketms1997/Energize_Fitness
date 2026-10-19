import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import '../../core/validators.dart';
import '../../repositories/gym_repository.dart';
import '../../shared/gym_profile_form.dart';

/// First-run provisioning: gym profile plus the single owner account.
/// Only reachable while no owner exists.
class SetupScreen extends ConsumerStatefulWidget {
  const SetupScreen({super.key});

  @override
  ConsumerState<SetupScreen> createState() => _SetupScreenState();
}

class _SetupScreenState extends ConsumerState<SetupScreen> {
  final _formKey = GlobalKey<FormState>();
  final _gym = GymProfileFormControllers();
  final _ownerName = TextEditingController();
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _confirm = TextEditingController();
  bool _busy = false;
  String? _error;
  String? _recoveryCode;

  @override
  void dispose() {
    _gym.dispose();
    for (final c in [_ownerName, _email, _password, _confirm]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final result = await ref
          .read(authRepositoryProvider)
          .provisionOwner(
            gym: _gym.toInput(),
            ownerName: _ownerName.text,
            email: _email.text,
            password: _password.text,
            deviceName: Platform.localHostname,
            platform: Platform.operatingSystem,
          );
      setState(() => _recoveryCode = result.recoveryCode);
    } on ValidationException catch (e) {
      setState(() => _error = e.errors.values.join('\n'));
    } catch (_) {
      setState(() => _error = 'Setup failed. Please try again.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _finish() => ref.invalidate(setupCompleteProvider);

  @override
  Widget build(BuildContext context) {
    if (_recoveryCode != null) {
      return _RecoveryCodeView(_recoveryCode!, _finish);
    }
    return Scaffold(
      appBar: AppBar(title: const Text('Set up your gym')),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 520),
          child: Form(
            key: _formKey,
            child: ListView(
              padding: const EdgeInsets.all(24),
              children: [
                Text(
                  'Gym details',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                const SizedBox(height: 12),
                GymProfileFields(_gym),
                const SizedBox(height: 24),
                Text(
                  'Owner account',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                const SizedBox(height: 12),
                TextFormField(
                  key: const Key('ownerName'),
                  controller: _ownerName,
                  decoration: const InputDecoration(labelText: 'Owner name'),
                  validator: (v) => Validators.required(v, field: 'Owner name'),
                ),
                const SizedBox(height: 12),
                TextFormField(
                  key: const Key('ownerEmail'),
                  controller: _email,
                  decoration: const InputDecoration(labelText: 'Email'),
                  keyboardType: TextInputType.emailAddress,
                  validator: Validators.email,
                ),
                const SizedBox(height: 12),
                TextFormField(
                  key: const Key('ownerPassword'),
                  controller: _password,
                  obscureText: true,
                  decoration: const InputDecoration(labelText: 'Password'),
                  validator: Validators.password,
                ),
                const SizedBox(height: 12),
                TextFormField(
                  key: const Key('ownerConfirm'),
                  controller: _confirm,
                  obscureText: true,
                  decoration: const InputDecoration(
                    labelText: 'Confirm password',
                  ),
                  validator: (v) =>
                      v == _password.text ? null : 'Passwords do not match',
                ),
                if (_error != null) ...[
                  const SizedBox(height: 12),
                  Text(
                    _error!,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
                ],
                const SizedBox(height: 24),
                FilledButton(
                  key: const Key('setupSubmit'),
                  onPressed: _busy ? null : _submit,
                  child: _busy
                      ? const SizedBox(
                          height: 18,
                          width: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Text('Create owner account'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _RecoveryCodeView extends StatelessWidget {
  const _RecoveryCodeView(this.code, this.onDone);
  final String code;
  final VoidCallback onDone;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Save your recovery code')),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 520),
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Text(
                  'This code is the only way to reset your password if you forget it. '
                  'It is shown once and is not stored in readable form. '
                  'Write it down and keep it somewhere safe.',
                ),
                const SizedBox(height: 16),
                SelectableText(
                  code,
                  key: const Key('recoveryCode'),
                  style: Theme.of(context).textTheme.headlineSmall
                      ?.copyWith(fontFamily: 'monospace'),
                ),
                const SizedBox(height: 24),
                FilledButton(
                  key: const Key('recoverySaved'),
                  onPressed: onDone,
                  child: const Text("I've saved it — continue"),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
