import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gym_management_system/core/providers.dart';
import 'package:gym_management_system/core/security/password_hasher.dart';
import 'package:gym_management_system/database/app_database.dart';
import 'package:gym_management_system/main.dart';

void main() {
  late AppDatabase db;

  setUp(() => db = AppDatabase.inMemory());

  Widget app() => ProviderScope(
    overrides: [
      databaseProvider.overrideWithValue(db),
      passwordHasherProvider.overrideWithValue(PasswordHasher(iterations: 100)),
    ],
    child: const GymApp(),
  );

  Future<void> settle(WidgetTester t) async {
    // Drift streams use real timers; run async work outside fake time.
    await t.runAsync(
      () => Future<void>.delayed(const Duration(milliseconds: 50)),
    );
    await t.pumpAndSettle();
  }

  testWidgets(
    'first run: setup -> recovery code -> login -> dashboard -> logout',
    (t) async {
      await t.binding.setSurfaceSize(const Size(900, 1600));
      await t.pumpWidget(app());
      await settle(t);

      // No owner yet, so the router forces setup.
      expect(find.text('Set up your gym'), findsOneWidget);

      await t.enterText(find.byKey(const Key('gymName')), 'Energize Fitness');
      await t.enterText(find.byKey(const Key('ownerName')), 'Asha');
      await t.enterText(
        find.byKey(const Key('ownerEmail')),
        'asha@example.com',
      );
      await t.enterText(
        find.byKey(const Key('ownerPassword')),
        'strongpass123',
      );
      await t.enterText(find.byKey(const Key('ownerConfirm')), 'strongpass123');
      await t.ensureVisible(find.byKey(const Key('setupSubmit')));
      await t.tap(find.byKey(const Key('setupSubmit')));
      await t.runAsync(
        () => Future<void>.delayed(const Duration(milliseconds: 300)),
      );
      await t.pump();

      expect(find.byKey(const Key('recoveryCode')), findsOneWidget);
      await t.tap(find.byKey(const Key('recoverySaved')));
      await settle(t);

      // Owner exists, not signed in -> login screen showing the configured name.
      expect(find.byKey(const Key('loginSubmit')), findsOneWidget);
      expect(find.text('Energize Fitness'), findsOneWidget);

      // Wrong password is rejected with a generic message.
      await t.enterText(
        find.byKey(const Key('loginEmail')),
        'asha@example.com',
      );
      await t.enterText(find.byKey(const Key('loginPassword')), 'wrongpass999');
      await t.tap(find.byKey(const Key('loginSubmit')));
      await t.runAsync(
        () => Future<void>.delayed(const Duration(milliseconds: 300)),
      );
      await t.pump();
      expect(find.text('Incorrect email or password.'), findsOneWidget);

      await t.enterText(
        find.byKey(const Key('loginPassword')),
        'strongpass123',
      );
      await t.tap(find.byKey(const Key('loginSubmit')));
      await t.runAsync(
        () => Future<void>.delayed(const Duration(milliseconds: 300)),
      );
      await settle(t);

      expect(find.text('Welcome, Asha'), findsOneWidget);
      expect(find.byKey(const Key('dashboardGymName')), findsOneWidget);

      await t.tap(find.byKey(const Key('logout')));
      await settle(t);
      expect(find.byKey(const Key('loginSubmit')), findsOneWidget);

      await db.close();
    },
  );
}
