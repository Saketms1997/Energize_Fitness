import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/providers.dart';
import '../../core/router.dart';
import '../../core/validators.dart';
import '../../repositories/auth_repository.dart';
import '../../repositories/gym_repository.dart';

class RecoveryScreen extends ConsumerStatefulWidget {
  const RecoveryScreen({super.key});

  @override
  ConsumerState<RecoveryScreen> createState() => _RecoveryScreenState();
}

class _RecoveryScreenState extends ConsumerState<RecoveryScreen> {
  final _formKey = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _code = TextEditingController();
  final _password = TextEditingController();
  String? _error;
  String? _newCode;

  @override
  void dispose() {
    for (final c in [_email, _code, _password]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() => _error = null);
    try {
      final code = await ref
          .read(authRepositoryProvider)
          .recoverPassword(
            email: _email.text,
            recoveryCode: _code.text,
            newPassword: _password.text,
          );
      setState(() => _newCode = code);
    } on ValidationException catch (e) {
      setState(() => _error = e.errors.values.join('\n'));
    } on AuthException catch (e) {
      setState(
        () => _error = e.failure == AuthFailure.lockedOut
            ? 'Too many attempts. Try again later.'
            : 'Email or recovery code is incorrect.',
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_newCode != null) {
      return Scaffold(
        appBar: AppBar(title: const Text('Password reset')),
        body: Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Text(
                  'Your password was changed. Your old recovery code no longer works — '
                  'save this new one:',
                ),
                const SizedBox(height: 12),
                SelectableText(
                  _newCode!,
                  key: const Key('newRecoveryCode'),
                  style: Theme.of(context).textTheme.headlineSmall,
                ),
                const SizedBox(height: 20),
                FilledButton(
                  onPressed: () => context.go(Routes.login),
                  child: const Text('Back to sign in'),
                ),
              ],
            ),
          ),
        ),
      );
    }
    return Scaffold(
      appBar: AppBar(
        title: const Text('Recover password'),
        leading: BackButton(onPressed: () => context.go(Routes.login)),
      ),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 400),
          child: Form(
            key: _formKey,
            child: ListView(
              padding: const EdgeInsets.all(24),
              children: [
                TextFormField(
                  controller: _email,
                  decoration: const InputDecoration(labelText: 'Email'),
                  validator: Validators.email,
                ),
                const SizedBox(height: 12),
                TextFormField(
                  controller: _code,
                  decoration: const InputDecoration(labelText: 'Recovery code'),
                  validator: (v) =>
                      Validators.required(v, field: 'Recovery code'),
                ),
                const SizedBox(height: 12),
                TextFormField(
                  controller: _password,
                  obscureText: true,
                  decoration: const InputDecoration(labelText: 'New password'),
                  validator: Validators.password,
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
                const SizedBox(height: 20),
                FilledButton(
                  onPressed: _submit,
                  child: const Text('Reset password'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
