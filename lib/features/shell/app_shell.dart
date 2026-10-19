import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/providers.dart';
import '../../core/router.dart';
import '../../services/backup_controller.dart';
import '../../services/session_controller.dart';

/// Signed-in frame: top bar (gym name, sign out) plus navigation. Any pointer
/// activity slides the idle-session timer.
class AppShell extends ConsumerWidget {
  const AppShell({required this.location, required this.child, super.key});

  final String location;
  final Widget child;

  static const _destinations = [
    (Routes.dashboard, 'Dashboard', Icons.dashboard_outlined, Icons.dashboard),
    (Routes.members, 'Members', Icons.people_outline, Icons.people),
    (
      Routes.attendance,
      'Attendance',
      Icons.how_to_reg_outlined,
      Icons.how_to_reg,
    ),
    (
      Routes.plans,
      'Plans',
      Icons.card_membership_outlined,
      Icons.card_membership,
    ),
    (
      Routes.trainers,
      'Trainers',
      Icons.sports_gymnastics_outlined,
      Icons.sports_gymnastics,
    ),
    (Routes.messages, 'Messages', Icons.chat_outlined, Icons.chat),
    (Routes.settings, 'Settings', Icons.settings_outlined, Icons.settings),
  ];

  int get _selected {
    for (var i = _destinations.length - 1; i > 0; i--) {
      if (location.startsWith(_destinations[i].$1)) return i;
    }
    return 0;
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    ref.read(backupControllerProvider); // starts the daily auto-backup check
    final gym = ref.watch(currentGymProvider).value;
    final wide = MediaQuery.sizeOf(context).width >= 800;
    void go(int i) => context.go(_destinations[i].$1);

    return Listener(
      behavior: HitTestBehavior.translucent,
      onPointerDown: (_) => ref.read(sessionProvider.notifier).touch(),
      child: Scaffold(
        appBar: AppBar(
          title: Text(gym?.name ?? '', key: const Key('dashboardGymName')),
          actions: [
            IconButton(
              key: const Key('logout'),
              tooltip: 'Sign out',
              onPressed: () => ref.read(sessionProvider.notifier).signOut(),
              icon: const Icon(Icons.logout),
            ),
          ],
        ),
        body: Row(
          children: [
            if (wide)
              NavigationRail(
                selectedIndex: _selected,
                onDestinationSelected: go,
                labelType: NavigationRailLabelType.all,
                destinations: [
                  for (final d in _destinations)
                    NavigationRailDestination(
                      icon: Icon(d.$3),
                      selectedIcon: Icon(d.$4),
                      label: Text(d.$2),
                    ),
                ],
              ),
            Expanded(child: child),
          ],
        ),
        bottomNavigationBar: wide
            ? null
            : NavigationBar(
                selectedIndex: _selected,
                onDestinationSelected: go,
                destinations: [
                  for (final d in _destinations)
                    NavigationDestination(
                      icon: Icon(d.$3),
                      selectedIcon: Icon(d.$4),
                      label: d.$2,
                    ),
                ],
              ),
      ),
    );
  }
}
