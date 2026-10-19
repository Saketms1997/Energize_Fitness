import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../features/attendance/attendance_screen.dart';
import '../features/dashboard/dashboard_screen.dart';
import '../features/login/login_screen.dart';
import '../features/login/recovery_screen.dart';
import '../features/members/member_detail_screen.dart';
import '../features/members/members_screen.dart';
import '../features/messages/messages_screen.dart';
import '../features/memberships/plans_screen.dart';
import '../features/registration/registration_screen.dart';
import '../features/settings/settings_screen.dart';
import '../features/trainers/trainers_screen.dart';
import '../features/setup/setup_screen.dart';
import '../features/shell/app_shell.dart';
import '../services/session_controller.dart';
import 'providers.dart';

abstract final class Routes {
  static const setup = '/setup';
  static const login = '/login';
  static const recover = '/recover';
  static const dashboard = '/';
  static const members = '/members';
  static const newMember = '/members/new';
  static const plans = '/plans';
  static const messages = '/messages';
  static const attendance = '/attendance';
  static const trainers = '/trainers';
  static const settings = '/settings';
  static String member(String id) => '/members/$id';
}

final routerProvider = Provider<GoRouter>((ref) {
  // Re-evaluate redirects whenever session or setup state changes.
  final refresh = ValueNotifier<int>(0);
  ref.listen(sessionProvider, (_, _) => refresh.value++);
  ref.listen(setupCompleteProvider, (_, _) => refresh.value++);
  ref.onDispose(refresh.dispose);

  return GoRouter(
    initialLocation: Routes.dashboard,
    refreshListenable: refresh,
    redirect: (context, state) {
      final setup = ref.read(setupCompleteProvider);
      if (setup.isLoading || setup.hasError) return null;
      final loc = state.matchedLocation;
      if (!setup.requireValue) {
        return loc == Routes.setup ? null : Routes.setup;
      }
      if (loc == Routes.setup) return Routes.login;
      final signedIn = ref.read(sessionProvider) != null;
      if (!signedIn) {
        return (loc == Routes.login || loc == Routes.recover)
            ? null
            : Routes.login;
      }
      if (loc == Routes.login || loc == Routes.recover) return Routes.dashboard;
      return null;
    },
    routes: [
      GoRoute(path: Routes.setup, builder: (_, _) => const SetupScreen()),
      GoRoute(path: Routes.login, builder: (_, _) => const LoginScreen()),
      GoRoute(path: Routes.recover, builder: (_, _) => const RecoveryScreen()),
      ShellRoute(
        builder: (_, state, child) =>
            AppShell(location: state.matchedLocation, child: child),
        routes: [
          GoRoute(
            path: Routes.dashboard,
            builder: (_, _) => const DashboardScreen(),
          ),
          GoRoute(
            path: Routes.members,
            builder: (_, _) => const MembersScreen(),
          ),
          // Must come before the ':id' route.
          GoRoute(
            path: Routes.newMember,
            builder: (_, _) => const RegistrationScreen(),
          ),
          GoRoute(
            path: '/members/:id',
            builder: (_, state) =>
                MemberDetailScreen(memberId: state.pathParameters['id']!),
          ),
          GoRoute(path: Routes.plans, builder: (_, _) => const PlansScreen()),
          GoRoute(
            path: Routes.attendance,
            builder: (_, _) => const AttendanceScreen(),
          ),
          GoRoute(
            path: Routes.trainers,
            builder: (_, _) => const TrainersScreen(),
          ),
          GoRoute(
            path: Routes.messages,
            builder: (_, _) => const MessagesScreen(),
          ),
          GoRoute(
            path: Routes.settings,
            builder: (_, _) => const SettingsScreen(),
          ),
        ],
      ),
    ],
  );
});
