import 'package:flutter/material.dart';

import '../repositories/auth_repository.dart';
import '../repositories/gym_repository.dart';
import '../repositories/support.dart';

/// Turns repository exceptions into short owner-facing messages.
String describeError(Object e) => switch (e) {
  ValidationException(:final errors) => errors.values.join('\n'),
  DomainException(:final message) => message,
  ConcurrentModificationException() =>
    'This record was changed on another device. Reopen it and try again.',
  AuthException(:final failure) =>
    failure == AuthFailure.lockedOut
        ? 'Too many attempts. Try again later.'
        : 'Incorrect password.',
  _ => 'Something went wrong. Please try again.',
};

void showMessage(BuildContext context, String message) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(message)));
}

/// Runs [action], reporting failures as a snackbar. Returns true on success.
Future<bool> runGuarded(
  BuildContext context,
  Future<void> Function() action, {
  String? success,
}) async {
  try {
    await action();
    if (context.mounted && success != null) showMessage(context, success);
    return true;
  } catch (e) {
    if (context.mounted) showMessage(context, describeError(e));
    return false;
  }
}

/// Asks for a free-text reason of at least [minLength] characters.
Future<String?> askReason(
  BuildContext context, {
  required String title,
  String label = 'Reason',
  String confirmLabel = 'Confirm',
  int minLength = 3,
}) {
  final controller = TextEditingController();
  return showDialog<String>(
    context: context,
    builder: (ctx) {
      String? error;
      return StatefulBuilder(
        builder: (ctx, setState) => AlertDialog(
          title: Text(title),
          content: TextField(
            key: const Key('reasonField'),
            controller: controller,
            autofocus: true,
            decoration: InputDecoration(labelText: label, errorText: error),
            maxLines: 2,
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('Cancel'),
            ),
            FilledButton(
              key: const Key('reasonConfirm'),
              onPressed: () {
                final v = controller.text.trim();
                if (v.length < minLength) {
                  setState(
                    () => error = 'Enter at least $minLength characters',
                  );
                } else {
                  Navigator.pop(ctx, v);
                }
              },
              child: Text(confirmLabel),
            ),
          ],
        ),
      );
    },
  );
}

Future<bool> confirm(
  BuildContext context, {
  required String title,
  required String message,
  String confirmLabel = 'Confirm',
}) async =>
    await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(title),
        content: Text(message),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(confirmLabel),
          ),
        ],
      ),
    ) ??
    false;
