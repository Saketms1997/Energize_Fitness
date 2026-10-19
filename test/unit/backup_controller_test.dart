import 'dart:io';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gym_management_system/core/providers.dart';
import 'package:gym_management_system/services/backup_controller.dart';
import 'package:gym_management_system/services/session_controller.dart';
import 'package:gym_management_system/sync/cloud_auth.dart';
import 'package:gym_management_system/sync/cloud_controller.dart';
import 'package:path/path.dart' as p;

import '../support/test_env.dart';

void main() {
  late TestEnv env;
  late Directory dir;
  late MemorySecretStore secrets;
  late ProviderContainer c;
  const pass = 'backup-pass-123';

  Future<ProviderContainer> boot({String? folder}) async {
    final container = ProviderContainer(
      overrides: [
        databaseProvider.overrideWithValue(env.db),
        clockProvider.overrideWithValue(env.clock),
        secretStoreProvider.overrideWithValue(secrets),
        backupIterationsProvider.overrideWithValue(2000),
        liveDatabaseFileProvider.overrideWithValue(
          () async => File(p.join(dir.path, 'live.sqlite')),
        ),
        defaultBackupFolderProvider.overrideWithValue(() async => folder),
      ],
    );
    addTearDown(container.dispose);
    container.read(backupControllerProvider);
    container
        .read(sessionProvider.notifier)
        .signIn(ownerId: env.ownerId, gymId: env.gymId, ownerName: 'Asha');
    await container.read(backupControllerProvider.notifier).refresh();
    return container;
  }

  setUp(() async {
    env = await TestEnv.create();
    dir = Directory.systemTemp.createTempSync('gym_bc_');
    secrets = MemorySecretStore();
  });
  tearDown(() async {
    await env.dispose();
    dir.deleteSync(recursive: true);
  });

  test('without a folder it asks for one instead of failing', () async {
    c = await boot();
    await c.read(backupControllerProvider.notifier).backupNow(pass);
    expect(
      c.read(backupControllerProvider).message,
      'Choose a backup folder first.',
    );
  });

  test('backup now writes a file, records the time, lists it', () async {
    c = await boot(folder: p.join(dir.path, 'bk'));
    await c.read(backupControllerProvider.notifier).backupNow(pass);
    final s = c.read(backupControllerProvider);
    expect(s.message, startsWith('Backup saved: energize-backup-'));
    expect(s.lastBackup, isNotNull);
    expect(s.recent, hasLength(1));
    expect(s.busy, isFalse);
    expect(secrets.data, isEmpty, reason: 'password not stored unless asked');
  });

  test('a bad password is reported, not thrown', () async {
    c = await boot(folder: p.join(dir.path, 'bk'));
    await c.read(backupControllerProvider.notifier).backupNow('short');
    expect(c.read(backupControllerProvider).message, contains('at least 8'));
    expect(c.read(backupControllerProvider).recent, isEmpty);
  });

  test('auto backup runs once a day using the remembered password', () async {
    c = await boot(folder: p.join(dir.path, 'bk'));
    final ctl = c.read(backupControllerProvider.notifier);
    await ctl.backupNow(pass, remember: true);
    expect(secrets.data['backup.passphrase'], pass);
    expect(c.read(backupControllerProvider).passwordRemembered, isTrue);

    env.clock.advance(const Duration(hours: 5));
    await ctl.autoBackupIfDue();
    expect(
      c.read(backupControllerProvider).recent,
      hasLength(1),
      reason: 'not due yet',
    );

    env.clock.advance(const Duration(hours: 20));
    await ctl.autoBackupIfDue();
    expect(c.read(backupControllerProvider).recent, hasLength(2));

    await ctl.forgetPassword();
    env.clock.advance(const Duration(days: 2));
    await ctl.autoBackupIfDue();
    expect(
      c.read(backupControllerProvider).recent,
      hasLength(2),
      reason: 'no password, no silent backup',
    );
  });

  test('restore: wrong password rejected; right password stages it', () async {
    c = await boot(folder: p.join(dir.path, 'bk'));
    final ctl = c.read(backupControllerProvider.notifier);
    await ctl.backupNow(pass);
    final file = c.read(backupControllerProvider).recent.single.path;

    await ctl.stageRestore(file, 'wrong-password');
    expect(
      c.read(backupControllerProvider).message,
      contains('Wrong backup password'),
    );
    expect(c.read(backupControllerProvider).restoreStaged, isFalse);

    await ctl.stageRestore('${dir.path}/missing.efbak', pass);
    expect(
      c.read(backupControllerProvider).message,
      'Could not read that file.',
    );

    await ctl.stageRestore(file, pass);
    expect(c.read(backupControllerProvider).restoreStaged, isTrue);
    expect(
      c.read(backupControllerProvider).message,
      contains('reopen the app'),
    );
    expect(
      File(p.join(dir.path, 'live.sqlite.restore-pending')).existsSync(),
      isTrue,
    );
  });

  test('signing out clears the backup state from memory', () async {
    c = await boot(folder: p.join(dir.path, 'bk'));
    c.read(sessionProvider.notifier).signOut();
    expect(c.read(backupControllerProvider).folder, isNull);
  });
}
