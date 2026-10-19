import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/providers.dart';
import '../../core/router.dart';
import '../../core/validators.dart';
import '../../repositories/auth_repository.dart';
import '../../services/session_controller.dart';

class LoginScreen extends ConsumerStatefulWidget {
  const LoginScreen({super.key});

  @override
  ConsumerState<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends ConsumerState<LoginScreen> {
  final _formKey = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _password = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final owner = await ref
          .read(authRepositoryProvider)
          .login(_email.text, _password.text);
      ref
          .read(sessionProvider.notifier)
          .signIn(
            ownerId: owner.id,
            gymId: owner.gymId,
            ownerName: owner.displayName,
          );
    } on AuthException catch (e) {
      setState(
        () => _error = switch (e.failure) {
          AuthFailure.lockedOut =>
            'Too many attempts. Try again in ${(e.retryAfter?.inMinutes ?? 15) + 1} minutes.',
          _ => 'Incorrect email or password.',
        },
      );
    } catch (_) {
      setState(() => _error = 'Could not sign in. Please try again.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final gymName = ref.watch(currentGymProvider).value?.name;
    final form = Card(
      margin: const EdgeInsets.all(16),
      color: Theme.of(context).colorScheme.surface.withValues(alpha: 0.92),
      child: Form(
        key: _formKey,
        child: ListView(
          shrinkWrap: true,
          padding: const EdgeInsets.all(24),
          children: [
            Text(
              gymName ?? 'Gym Management',
              key: const Key('loginGymName'),
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.headlineMedium,
            ),
            const SizedBox(height: 4),
            const Text('Owner sign in', textAlign: TextAlign.center),
            const SizedBox(height: 24),
            TextFormField(
              key: const Key('loginEmail'),
              controller: _email,
              decoration: const InputDecoration(labelText: 'Email'),
              keyboardType: TextInputType.emailAddress,
              validator: Validators.email,
            ),
            const SizedBox(height: 12),
            TextFormField(
              key: const Key('loginPassword'),
              controller: _password,
              obscureText: true,
              decoration: const InputDecoration(labelText: 'Password'),
              validator: (v) => Validators.required(v, field: 'Password'),
              onFieldSubmitted: (_) => _submit(),
            ),
            if (_error != null) ...[
              const SizedBox(height: 12),
              Text(
                _error!,
                key: const Key('loginError'),
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ],
            const SizedBox(height: 20),
            FilledButton(
              key: const Key('loginSubmit'),
              onPressed: _busy ? null : _submit,
              child: const Text('Sign in'),
            ),
            TextButton(
              onPressed: () => context.go(Routes.recover),
              child: const Text('Forgot password?'),
            ),
          ],
        ),
      ),
    );
    return Scaffold(
      backgroundColor: const Color(0xFF121212),
      body: LayoutBuilder(
        builder: (context, box) {
          final wide = box.maxWidth >= 800;
          final logo = Padding(
            padding: const EdgeInsets.all(24),
            child: Image.asset(
              'assets/Logo_cutout.png',
              key: const Key('loginBackground'),
              fit: BoxFit.contain,
              errorBuilder: (_, _, _) => const SizedBox.shrink(),
            ),
          );
          final formBox = Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 400),
              child: form,
            ),
          );
          if (wide) {
            return Row(
              children: [
                Expanded(child: logo),
                Expanded(child: formBox),
              ],
            );
          }
          return SingleChildScrollView(
            child: Column(
              children: [
                SizedBox(height: 220, child: logo),
                formBox,
              ],
            ),
          );
        },
      ),
    );
  }
}
