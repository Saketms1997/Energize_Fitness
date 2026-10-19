import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../core/providers.dart';
import '../../database/app_database.dart';
import '../../services/message_templates.dart';
import '../../services/session_controller.dart';
import '../../services/whatsapp_sender.dart';
import '../../shared/dialogs.dart';
import '../../shared/formatters.dart';

/// Outgoing WhatsApp messages: what is waiting, what was sent, what failed.
/// Without a cloud sender the owner sends each one from their own WhatsApp via
/// a click-to-chat link and marks it sent.
class MessagesScreen extends ConsumerWidget {
  const MessagesScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final session = ref.watch(sessionProvider);
    final messages = ref.watch(messagesProvider);
    final sender = ref.watch(whatsappSenderProvider);
    if (session == null) return const SizedBox.shrink();

    return Align(
      alignment: Alignment.topCenter,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 900),
        child: ListView(
          padding: const EdgeInsets.all(24),
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    'WhatsApp messages',
                    style: Theme.of(context).textTheme.headlineSmall,
                  ),
                ),
                OutlinedButton.icon(
                  key: const Key('generateReminders'),
                  onPressed: () async {
                    final repo = ref.read(messageRepositoryProvider);
                    final c = await repo.generateReminders(
                      session.gymId,
                      session.ownerId,
                    );
                    if (!context.mounted) return;
                    showMessage(
                      context,
                      c.total == 0 && c.skippedNoConsent == 0
                          ? 'No new reminders are due.'
                          : 'Queued ${c.total} reminder(s)'
                                '${c.skippedNoConsent == 0 ? '' : '; ${c.skippedNoConsent} skipped (no WhatsApp consent)'}.',
                    );
                  },
                  icon: const Icon(Icons.notifications_active_outlined),
                  label: const Text('Create reminders'),
                ),
                if (sender != null) ...[
                  const SizedBox(width: 8),
                  FilledButton.icon(
                    key: const Key('sendQueued'),
                    onPressed: () async {
                      final n = await WhatsAppDispatcher(
                        ref.read(messageRepositoryProvider),
                        sender,
                      ).processQueue(session.gymId);
                      if (context.mounted) {
                        showMessage(context, 'Sent $n message(s)');
                      }
                    },
                    icon: const Icon(Icons.send),
                    label: const Text('Send all queued'),
                  ),
                ],
              ],
            ),
            const SizedBox(height: 8),
            if (sender == null)
              const Text(
                'Automatic sending is off. Use "Open WhatsApp" on each message, '
                'press send there, then mark it sent here.',
              ),
            const SizedBox(height: 12),
            messages.when(
              loading: () => const Center(child: CircularProgressIndicator()),
              error: (_, _) => const Text('Could not load messages.'),
              data: (list) => list.isEmpty
                  ? const Text('Nothing here yet.')
                  : Column(
                      children: [
                        for (final m in list) _MessageTile(m, session: session),
                      ],
                    ),
            ),
          ],
        ),
      ),
    );
  }
}

class _MessageTile extends ConsumerWidget {
  const _MessageTile(this.m, {required this.session});
  final WhatsappMessage m;
  final OwnerSession session;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final repo = ref.read(messageRepositoryProvider);
    final phone10 = m.toPhone.substring(2);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    '+91 $phone10 · ${formatDateTime(m.createdAt)}',
                    style: Theme.of(context).textTheme.labelMedium,
                  ),
                ),
                StatusChip(m.status),
              ],
            ),
            const SizedBox(height: 6),
            Text(m.bodyText),
            if (m.errorMessage != null)
              Text(
                'Error: ${m.errorMessage}',
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            Wrap(
              spacing: 8,
              children: [
                if (m.status == 'queued' || m.status == 'failed')
                  TextButton.icon(
                    key: Key('open-${m.id}'),
                    icon: const Icon(Icons.open_in_new, size: 18),
                    label: const Text('Open WhatsApp'),
                    onPressed: () async {
                      final ok = await launchUrl(
                        Uri.parse(waMeLink(phone10, m.bodyText)),
                        mode: LaunchMode.externalApplication,
                      );
                      if (!ok && context.mounted) {
                        showMessage(context, 'Could not open WhatsApp.');
                      }
                    },
                  ),
                if (m.status == 'queued' || m.status == 'failed')
                  TextButton(
                    key: Key('manualSent-${m.id}'),
                    onPressed: () => runGuarded(
                      context,
                      () => repo.markSent(
                        m.id,
                        actorId: session.ownerId,
                        manual: true,
                      ),
                      success: 'Marked as sent',
                    ),
                    child: const Text('Mark sent'),
                  ),
                if (m.status == 'failed')
                  TextButton(
                    onPressed: () => runGuarded(
                      context,
                      () => repo.retry(m.id, session.ownerId),
                    ),
                    child: const Text('Retry'),
                  ),
                if (m.status == 'queued' || m.status == 'failed')
                  TextButton(
                    onPressed: () => runGuarded(
                      context,
                      () => repo.cancel(m.id, session.ownerId),
                    ),
                    child: const Text('Cancel'),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
