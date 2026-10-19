import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/clock.dart';

class OwnerSession {
  const OwnerSession({
    required this.ownerId,
    required this.gymId,
    required this.ownerName,
    required this.expiresAt,
  });

  final String ownerId;
  final String gymId;
  final String ownerName;
  final DateTime expiresAt;

  OwnerSession withExpiry(DateTime t) => OwnerSession(
    ownerId: ownerId,
    gymId: gymId,
    ownerName: ownerName,
    expiresAt: t,
  );
}

/// Holds the signed-in owner. Sessions expire after [idleTimeout] without
/// activity; [touch] slides the window. State is memory-only, so closing the
/// app always requires signing in again.
class SessionController extends Notifier<OwnerSession?> {
  static const idleTimeout = Duration(minutes: 30);
  Timer? _timer;

  @override
  OwnerSession? build() {
    ref.onDispose(() => _timer?.cancel());
    return null;
  }

  Clock get _clock => ref.read(clockProvider);

  void signIn({
    required String ownerId,
    required String gymId,
    required String ownerName,
  }) {
    state = OwnerSession(
      ownerId: ownerId,
      gymId: gymId,
      ownerName: ownerName,
      expiresAt: _clock.now().add(idleTimeout),
    );
    _schedule();
  }

  /// Slides the expiry on owner activity. No-op once expired.
  void touch() {
    final s = state;
    if (s == null) return;
    if (!s.expiresAt.isAfter(_clock.now())) {
      signOut();
      return;
    }
    state = s.withExpiry(_clock.now().add(idleTimeout));
    _schedule();
  }

  /// Signs out if the session has lapsed. Returns true when it expired.
  bool enforceExpiry() {
    final s = state;
    if (s != null && !s.expiresAt.isAfter(_clock.now())) {
      signOut();
      return true;
    }
    return false;
  }

  void signOut() {
    _timer?.cancel();
    state = null;
  }

  void _schedule() {
    _timer?.cancel();
    final s = state;
    if (s == null) return;
    final wait = s.expiresAt.difference(_clock.now());
    _timer = Timer(wait.isNegative ? Duration.zero : wait, enforceExpiry);
  }
}

final clockProvider = Provider<Clock>((_) => const SystemClock());

final sessionProvider = NotifierProvider<SessionController, OwnerSession?>(
  SessionController.new,
);
