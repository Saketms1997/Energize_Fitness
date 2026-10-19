import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gym_management_system/core/clock.dart';
import 'package:gym_management_system/services/session_controller.dart';

class _Clock implements Clock {
  DateTime t = DateTime.utc(2026, 1, 1);
  @override
  DateTime now() => t;
}

void main() {
  late _Clock clock;
  late ProviderContainer container;

  setUp(() {
    clock = _Clock();
    container = ProviderContainer(
      overrides: [clockProvider.overrideWithValue(clock)],
    );
    addTearDown(container.dispose);
    container
        .read(sessionProvider.notifier)
        .signIn(ownerId: 'o', gymId: 'g', ownerName: 'Asha');
  });

  test('signed in until idle timeout elapses', () {
    expect(container.read(sessionProvider), isNotNull);
    clock.t = clock.t.add(
      SessionController.idleTimeout - const Duration(seconds: 1),
    );
    expect(container.read(sessionProvider.notifier).enforceExpiry(), isFalse);
    expect(container.read(sessionProvider), isNotNull);
  });

  test('expires after idle timeout', () {
    clock.t = clock.t.add(SessionController.idleTimeout);
    expect(container.read(sessionProvider.notifier).enforceExpiry(), isTrue);
    expect(container.read(sessionProvider), isNull);
  });

  test('activity slides the window', () {
    clock.t = clock.t.add(const Duration(minutes: 20));
    container.read(sessionProvider.notifier).touch();
    clock.t = clock.t.add(const Duration(minutes: 20));
    expect(container.read(sessionProvider.notifier).enforceExpiry(), isFalse);
  });

  test('touch after expiry signs out instead of reviving the session', () {
    clock.t = clock.t.add(const Duration(minutes: 31));
    container.read(sessionProvider.notifier).touch();
    expect(container.read(sessionProvider), isNull);
  });

  test('logout clears the session', () {
    container.read(sessionProvider.notifier).signOut();
    expect(container.read(sessionProvider), isNull);
  });
}
