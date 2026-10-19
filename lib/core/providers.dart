import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../database/app_database.dart';
import '../repositories/auth_repository.dart';
import '../repositories/attendance_repository.dart';
import '../repositories/dashboard_repository.dart';
import '../repositories/gym_repository.dart';
import '../repositories/member_repository.dart';
import '../repositories/message_repository.dart';
import '../repositories/membership_repository.dart';
import '../repositories/payment_repository.dart';
import '../repositories/plan_repository.dart';
import '../repositories/trainer_repository.dart';
import '../services/session_controller.dart';
import '../services/whatsapp_sender.dart';
import '../sync/cloud_controller.dart';
import 'security/password_hasher.dart';

/// Overridden in main() / tests with a concrete database.
final databaseProvider = Provider<AppDatabase>(
  (ref) => throw UnimplementedError('databaseProvider must be overridden'),
);

final passwordHasherProvider = Provider<PasswordHasher>(
  (_) => PasswordHasher(),
);

final authRepositoryProvider = Provider<AuthRepository>(
  (ref) => AuthRepository(
    ref.watch(databaseProvider),
    ref.watch(clockProvider),
    ref.watch(passwordHasherProvider),
  ),
);

final gymRepositoryProvider = Provider<GymRepository>(
  (ref) => GymRepository(ref.watch(databaseProvider), ref.watch(clockProvider)),
);

final planRepositoryProvider = Provider<PlanRepository>(
  (ref) =>
      PlanRepository(ref.watch(databaseProvider), ref.watch(clockProvider)),
);

final memberRepositoryProvider = Provider<MemberRepository>(
  (ref) =>
      MemberRepository(ref.watch(databaseProvider), ref.watch(clockProvider)),
);

final membershipRepositoryProvider = Provider<MembershipRepository>(
  (ref) => MembershipRepository(
    ref.watch(databaseProvider),
    ref.watch(clockProvider),
  ),
);

final paymentRepositoryProvider = Provider<PaymentRepository>(
  (ref) =>
      PaymentRepository(ref.watch(databaseProvider), ref.watch(clockProvider)),
);

final dashboardRepositoryProvider = Provider<DashboardRepository>(
  (ref) => DashboardRepository(
    ref.watch(databaseProvider),
    ref.watch(clockProvider),
  ),
);

final dashboardProvider = StreamProvider.autoDispose<DashboardData>((ref) {
  final gymId = ref.watch(sessionProvider.select((s) => s?.gymId));
  if (gymId == null) return const Stream.empty();
  return ref.watch(dashboardRepositoryProvider).watch(gymId);
});

typedef MemberFilter = ({String query, String? status});

final memberSummariesProvider = StreamProvider.autoDispose
    .family<List<MemberSummary>, MemberFilter>((ref, filter) {
      final gymId = ref.watch(sessionProvider.select((s) => s?.gymId));
      if (gymId == null) return const Stream.empty();
      return ref
          .watch(memberRepositoryProvider)
          .watchSummaries(gymId, query: filter.query, status: filter.status);
    });

final memberDetailProvider = StreamProvider.autoDispose
    .family<MemberDetail?, String>(
      (ref, id) => ref.watch(memberRepositoryProvider).watchDetail(id),
    );

final plansProvider = StreamProvider(
  (ref) => ref.watch(planRepositoryProvider).watchPlans(),
);

final currentGymProvider = StreamProvider(
  (ref) => ref.watch(gymRepositoryProvider).watchGym(),
);

/// True once first-run owner setup has completed. Invalidate after setup.
final setupCompleteProvider = FutureProvider<bool>(
  (ref) => ref.watch(authRepositoryProvider).isSetupComplete(),
);

final messageRepositoryProvider = Provider<MessageRepository>(
  (ref) =>
      MessageRepository(ref.watch(databaseProvider), ref.watch(clockProvider)),
);

final messagesProvider = StreamProvider.autoDispose<List<WhatsappMessage>>((
  ref,
) {
  final gymId = ref.watch(sessionProvider.select((s) => s?.gymId));
  if (gymId == null) return const Stream.empty();
  return ref.watch(messageRepositoryProvider).watchMessages(gymId);
});

/// Automatic sender through the cloud. Null until cloud sync is connected and
/// automatic WhatsApp is switched on;
/// the Messages screen then falls back to click-to-chat (wa.me).
final whatsappSenderProvider = Provider<WhatsAppSender?>((ref) {
  final cloud = ref.watch(cloudControllerProvider);
  if (!cloud.signedIn || !cloud.whatsappAuto || !cloud.configured) return null;
  return EdgeFunctionSender(
    client: ref.watch(httpClientProvider),
    functionsBaseUrl: '${cloud.projectUrl}/functions/v1',
    accessToken: ref.read(cloudControllerProvider.notifier).accessToken,
  );
});

final trainerRepositoryProvider = Provider<TrainerRepository>(
  (ref) =>
      TrainerRepository(ref.watch(databaseProvider), ref.watch(clockProvider)),
);

final attendanceRepositoryProvider = Provider<AttendanceRepository>(
  (ref) => AttendanceRepository(
    ref.watch(databaseProvider),
    ref.watch(clockProvider),
  ),
);

final trainersProvider = StreamProvider.autoDispose<List<Trainer>>((ref) {
  final gymId = ref.watch(sessionProvider.select((s) => s?.gymId));
  if (gymId == null) return const Stream.empty();
  return ref.watch(trainerRepositoryProvider).watchTrainers(gymId);
});

final todayAttendanceProvider =
    StreamProvider.autoDispose<List<AttendanceEntry>>((ref) {
      final gymId = ref.watch(sessionProvider.select((s) => s?.gymId));
      if (gymId == null) return const Stream.empty();
      return ref.watch(attendanceRepositoryProvider).watchToday(gymId);
    });

final memberAttendanceCountProvider = StreamProvider.autoDispose
    .family<int, String>(
      (ref, id) => ref.watch(attendanceRepositoryProvider).watchCount(id),
    );
