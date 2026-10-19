import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gym_management_system/core/providers.dart';
import 'package:gym_management_system/services/session_controller.dart';
import 'package:gym_management_system/sync/cloud_auth.dart';
import 'package:gym_management_system/sync/cloud_controller.dart';

import '../support/fake_gateway.dart';
import '../support/fake_supabase_http.dart';
import '../support/test_env.dart';

void main() {
  late TestEnv env;
  late FakeServer server;
  late FakeSupabase supa;
  late MemorySecretStore secrets;

  setUp(() async {
    env = await TestEnv.create();
    server = FakeServer();
    supa = FakeSupabase(server);
    secrets = MemorySecretStore();
  });
  tearDown(() => env.dispose());

  CloudAuth auth() => CloudAuth(
    client: supa.client,
    projectUrl: 'https://p.supabase.co',
    anonKey: FakeSupabase.anonKey,
    store: secrets,
    clock: env.clock,
  );

  group('CloudAuth', () {
    test('sign in stores tokens; wrong password is a friendly error', () async {
      final a = auth();
      await expectLater(
        a.signIn('owner@example.com', 'nope'),
        throwsA(
          isA<CloudAuthException>().having(
            (e) => e.message,
            'm',
            'Wrong email or password.',
          ),
        ),
      );
      expect(await a.isSignedIn, isFalse);
      await a.signIn('owner@example.com', 'cloud-pass-123');
      expect(await a.isSignedIn, isTrue);
      expect(await a.email, 'owner@example.com');
      expect(await a.accessToken(), 'access-1');
    });

    test(
      'refreshes an expiring token, and returns null when refresh is rejected',
      () async {
        final a = auth();
        await a.signIn('owner@example.com', 'cloud-pass-123');
        env.clock.advance(const Duration(minutes: 59, seconds: 30));
        expect(await a.accessToken(), 'access-2');
        expect(secrets.data['cloud.refreshToken'], 'refresh-2');

        env.clock.advance(const Duration(hours: 2));
        supa.validRefresh.clear();
        expect(await a.accessToken(), isNull);
      },
    );

    test(
      'offline refresh keeps the old token instead of signing out',
      () async {
        final a = auth();
        await a.signIn('owner@example.com', 'cloud-pass-123');
        env.clock.advance(const Duration(hours: 2));
        server.offline = true;
        expect(await a.accessToken(), 'access-1');
        expect(await a.isSignedIn, isTrue);
      },
    );

    test('sign out wipes every stored secret', () async {
      final a = auth();
      await a.signIn('owner@example.com', 'cloud-pass-123');
      await a.signOut();
      expect(secrets.data, isEmpty);
      expect(await a.accessToken(), isNull);
    });
  });

  group('CloudController end to end (real gateway, fake Supabase HTTP)', () {
    late ProviderContainer c;

    setUp(() async {
      c = ProviderContainer(
        overrides: [
          databaseProvider.overrideWithValue(env.db),
          clockProvider.overrideWithValue(env.clock),
          httpClientProvider.overrideWithValue(supa.client),
          secretStoreProvider.overrideWithValue(secrets),
        ],
      );
      addTearDown(c.dispose);
      c
          .read(sessionProvider.notifier)
          .signIn(ownerId: env.ownerId, gymId: env.gymId, ownerName: 'Asha');
      c.read(cloudControllerProvider); // build + listen
      await c.read(cloudControllerProvider.notifier).refresh();
    });

    test(
      'rejects a non-https project URL without touching the network',
      () async {
        await c
            .read(cloudControllerProvider.notifier)
            .connect(
              projectUrl: 'http://p.supabase.co',
              anonKey: FakeSupabase.anonKey,
              email: 'owner@example.com',
              password: 'cloud-pass-123',
            );
        expect(c.read(cloudControllerProvider).message, contains('https://'));
        expect(supa.requests, isEmpty);
      },
    );

    test('connect signs in, claims the gym, and uploads data', () async {
      final r = await env.register();
      await env.pay(r.membership.id, 100000);
      final ctl = c.read(cloudControllerProvider.notifier);
      await ctl.connect(
        projectUrl: 'https://p.supabase.co/',
        anonKey: FakeSupabase.anonKey,
        email: 'owner@example.com',
        password: 'cloud-pass-123',
      );
      final s = c.read(cloudControllerProvider);
      expect(s.signedIn, isTrue);
      expect(s.pending, 0);
      expect(s.lastSync, isNotNull);
      expect(s.message, startsWith('Synced: sent '));
      expect(server.records.keys, contains('members/${r.member.id}'));
      expect(supa.claimedBy, 'owner@example.com');
      // every authenticated request carried the anon key and a bearer token
      final rest = supa.requests.where((q) => q.url.path.startsWith('/rest/'));
      expect(rest, isNotEmpty);
      expect(
        rest.every(
          (q) =>
              q.headers['apikey'] == FakeSupabase.anonKey &&
              q.headers['Authorization']!.startsWith('Bearer access-'),
        ),
        isTrue,
      );
      // password never persisted in the app database
      final stored = await env.db.select(env.db.appSettings).get();
      expect(
        stored.map((e) => e.key),
        containsAll(['cloud.url', 'cloud.anonKey']),
      );
      expect(stored.any((e) => e.value.contains('cloud-pass-123')), isFalse);
      expect(
        stored.firstWhere((e) => e.key == 'cloud.url').value,
        'https://p.supabase.co',
      );
    });

    test(
      'state is restored after restart without asking for the password again',
      () async {
        await c
            .read(cloudControllerProvider.notifier)
            .connect(
              projectUrl: 'https://p.supabase.co',
              anonKey: FakeSupabase.anonKey,
              email: 'owner@example.com',
              password: 'cloud-pass-123',
            );
        final c2 = ProviderContainer(
          overrides: [
            databaseProvider.overrideWithValue(env.db),
            clockProvider.overrideWithValue(env.clock),
            httpClientProvider.overrideWithValue(supa.client),
            secretStoreProvider.overrideWithValue(secrets),
          ],
        );
        addTearDown(c2.dispose);
        c2
            .read(sessionProvider.notifier)
            .signIn(ownerId: env.ownerId, gymId: env.gymId, ownerName: 'Asha');
        c2.read(cloudControllerProvider);
        await c2.read(cloudControllerProvider.notifier).refresh();
        final s = c2.read(cloudControllerProvider);
        expect(s.signedIn, isTrue);
        expect(s.email, 'owner@example.com');
        expect(s.lastSync, isNotNull);
      },
    );

    test('signing out of the app clears in-memory cloud state', () async {
      await c
          .read(cloudControllerProvider.notifier)
          .connect(
            projectUrl: 'https://p.supabase.co',
            anonKey: FakeSupabase.anonKey,
            email: 'owner@example.com',
            password: 'cloud-pass-123',
          );
      c.read(sessionProvider.notifier).signOut();
      expect(c.read(cloudControllerProvider).signedIn, isFalse);
      expect(c.read(cloudControllerProvider).projectUrl, isNull);
    });

    test('offline sync reports a problem and keeps pending count', () async {
      await c
          .read(cloudControllerProvider.notifier)
          .connect(
            projectUrl: 'https://p.supabase.co',
            anonKey: FakeSupabase.anonKey,
            email: 'owner@example.com',
            password: 'cloud-pass-123',
          );
      await env.register();
      server.offline = true;
      await c.read(cloudControllerProvider.notifier).syncNow();
      final s = c.read(cloudControllerProvider);
      expect(s.message, startsWith('Sync problem'));
      expect(s.pending, greaterThan(0));
      server.offline = false;
      await c.read(cloudControllerProvider.notifier).syncNow();
      expect(c.read(cloudControllerProvider).pending, 0);
    });

    test('disconnect keeps local data but stops syncing', () async {
      await c
          .read(cloudControllerProvider.notifier)
          .connect(
            projectUrl: 'https://p.supabase.co',
            anonKey: FakeSupabase.anonKey,
            email: 'owner@example.com',
            password: 'cloud-pass-123',
          );
      await c.read(cloudControllerProvider.notifier).disconnect();
      expect(c.read(cloudControllerProvider).signedIn, isFalse);
      expect(secrets.data, isEmpty);
      expect(await c.read(cloudControllerProvider.notifier).syncNow(), isNull);
    });
  });
}
