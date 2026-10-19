import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/providers.dart';
import '../../core/router.dart';
import '../../database/app_database.dart';
import '../../repositories/support.dart';
import '../../services/session_controller.dart';
import '../../shared/dialogs.dart';
import '../../shared/formatters.dart';

/// Front-desk check-in: type a member ID or phone number, press Enter.
class AttendanceScreen extends ConsumerStatefulWidget {
  const AttendanceScreen({super.key});

  @override
  ConsumerState<AttendanceScreen> createState() => _AttendanceScreenState();
}

class _AttendanceScreenState extends ConsumerState<AttendanceScreen> {
  final _query = TextEditingController();
  String? _result;
  bool _resultIsError = false;

  @override
  void dispose() {
    _query.dispose();
    super.dispose();
  }

  void _say(String text, {bool error = false}) => setState(() {
    _result = text;
    _resultIsError = error;
  });

  Future<void> _checkIn({String? overrideReason, Member? known}) async {
    final session = ref.read(sessionProvider);
    if (session == null) return;
    final repo = ref.read(attendanceRepositoryProvider);
    final member = known ?? await repo.findMember(session.gymId, _query.text);
    if (member == null) {
      _say('No member found for "${_query.text.trim()}".', error: true);
      return;
    }
    try {
      final r = await repo.checkIn(
        memberId: member.id,
        actorId: session.ownerId,
        method: 'manual',
        overrideReason: overrideReason,
      );
      _say(
        r.alreadyIn
            ? '${member.fullName} already checked in today.'
            : 'Welcome, ${member.fullName}!',
      );
      _query.clear();
    } on DomainException catch (e) {
      _say(e.message, error: true);
      if (!mounted) return;
      final reason = await askReason(
        context,
        title: 'Let ${member.fullName} in anyway?',
        label: 'Reason (recorded in the audit log)',
        confirmLabel: 'Check in anyway',
        minLength: 5,
      );
      if (reason != null) await _checkIn(overrideReason: reason, known: member);
    } catch (e) {
      _say(describeError(e), error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final today = ref.watch(todayAttendanceProvider);
    final session = ref.watch(sessionProvider);
    final scheme = Theme.of(context).colorScheme;
    return Align(
      alignment: Alignment.topCenter,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 720),
        child: ListView(
          padding: const EdgeInsets.all(24),
          children: [
            Text(
              'Attendance',
              style: Theme.of(context).textTheme.headlineSmall,
            ),
            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(
                  child: TextField(
                    key: const Key('checkInQuery'),
                    controller: _query,
                    autofocus: true,
                    decoration: const InputDecoration(
                      labelText: 'Member ID or phone number',
                    ),
                    onSubmitted: (_) => _checkIn(),
                  ),
                ),
                const SizedBox(width: 12),
                FilledButton(
                  key: const Key('checkInButton'),
                  onPressed: _checkIn,
                  child: const Text('Check in'),
                ),
              ],
            ),
            if (_result != null)
              Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Text(
                  _result!,
                  key: const Key('checkInResult'),
                  style: TextStyle(
                    color: _resultIsError ? scheme.error : scheme.primary,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            const SizedBox(height: 24),
            today.when(
              loading: () => const Center(child: CircularProgressIndicator()),
              error: (_, _) => const Text('Could not load attendance.'),
              data: (list) => Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Today · ${list.length} check-in${list.length == 1 ? '' : 's'}',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  const SizedBox(height: 8),
                  for (final e in list)
                    Card(
                      child: ListTile(
                        key: Key('att-${e.record.id}'),
                        title: Text(e.member.fullName),
                        subtitle: Text(
                          '${e.member.memberCode} · ${formatDateTime(e.record.checkedInAt)}',
                        ),
                        onTap: () => context.go(Routes.member(e.member.id)),
                        trailing: TextButton(
                          onPressed: session == null
                              ? null
                              : () async {
                                  final r = await askReason(
                                    context,
                                    title: 'Undo check-in',
                                    label: 'Why?',
                                    confirmLabel: 'Undo',
                                  );
                                  if (r == null || !context.mounted) return;
                                  await runGuarded(
                                    context,
                                    () => ref
                                        .read(attendanceRepositoryProvider)
                                        .undo(
                                          attendanceId: e.record.id,
                                          actorId: session.ownerId,
                                          reason: r,
                                        ),
                                    success: 'Check-in removed',
                                  );
                                },
                          child: const Text('Undo'),
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
}
