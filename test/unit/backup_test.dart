import 'dart:io';

import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gym_management_system/database/app_database.dart';
import 'package:gym_management_system/services/backup_service.dart';
import 'package:path/path.dart' as p;

import '../support/test_env.dart';

void main() {
  late TestEnv env;
  late BackupService svc;
  late Directory dir;
  const pass = 'correct horse battery';

  setUp(() async {
    env = await TestEnv.create();
    // low iteration count keeps the tests fast; the format stores it per file
    svc = BackupService(env.db, env.clock, pbkdf2Iterations: 2000);
    dir = Directory.systemTemp.createTempSync('gym_backup_');
  });
  tearDown(() async {
    await env.dispose();
    dir.deleteSync(recursive: true);
  });

  test('backup is encrypted: no plaintext data in the file', () async {
    await env.register(name: 'Zebediah Quux');
    final f = await svc.createBackup(
      folder: Directory(p.join(dir.path, 'out')),
      passphrase: pass,
    );
    final bytes = await f.readAsBytes();
    expect(String.fromCharCodes(bytes), isNot(contains('Zebediah')));
    expect(String.fromCharCodes(bytes), isNot(contains('SQLite format')));
    expect(String.fromCharCodes(bytes.take(4)), 'EFBK');
    expect(f.path, endsWith('.efbak'));
    expect(f.path, contains('20260310-063000'));
    expect(
      Directory(p.join(dir.path, 'out'))
          .listSync()
          .whereType<File>()
          .where((x) => x.path.endsWith('.part')),
      isEmpty,
    );
  });

  test('round trip: stage -> next start swaps in the restored data', () async {
    final r = await env.register(name: 'Ravi Kumar');
    await env.pay(r.membership.id, 100000);
    final backup = await svc.createBackup(folder: dir, passphrase: pass);

    // the live DB moves on after the backup
    await env.register(name: 'Newer Member', phone: '9000000009');

    final info = await svc.inspect(backup, pass);
    expect(info.gymName, 'Iron Temple');
    expect(info.schemaVersion, AppDatabase.currentSchemaVersion);

    final live = File(p.join(dir.path, 'live.sqlite'));
    live.writeAsStringSync('old live database');
    await svc.stageRestore(
      backup: backup,
      passphrase: pass,
      liveDatabase: live,
    );
    expect(
      live.readAsStringSync(),
      'old live database',
      reason: 'nothing changes until restart',
    );

    final swapped = await PendingRestore.applyIfPresent(live, env.clock.t);
    expect(swapped, isTrue);
    final kept = dir
        .listSync()
        .whereType<File>()
        .where((f) => f.path.contains('.before-restore-'))
        .single;
    expect(
      kept.readAsStringSync(),
      'old live database',
      reason: 'previous data is never destroyed',
    );
    expect(
      await PendingRestore.applyIfPresent(live, env.clock.t),
      isFalse,
      reason: 'applied only once',
    );

    final restored = AppDatabase(NativeDatabase(live));
    final names = (await restored.select(restored.members).get()).map(
      (m) => m.fullName,
    );
    expect(names, [
      'Ravi Kumar',
    ], reason: 'the member added after the backup is not there');
    expect(
      (await restored.select(restored.payments).get()).single.status,
      'verified',
    );
    await restored.close();
  });

  test('wrong password and short password are refused', () async {
    final f = await svc.createBackup(folder: dir, passphrase: pass);
    await expectLater(
      svc.inspect(f, 'not the password'),
      throwsA(
        isA<BackupException>().having(
          (e) => e.message,
          'm',
          contains('Wrong backup password'),
        ),
      ),
    );
    await expectLater(
      svc.createBackup(folder: dir, passphrase: 'short'),
      throwsA(isA<BackupException>()),
    );
  });

  test('any tampering is detected, in header or body', () async {
    final f = await svc.createBackup(folder: dir, passphrase: pass);
    final original = await f.readAsBytes();
    for (final index in [
      6,
      20,
      30,
      100,
      original.length ~/ 2,
      original.length - 1,
    ]) {
      final bad = original.toList()..[index] ^= 0x01;
      final tampered = File(p.join(dir.path, 'tampered-$index.efbak'))
        ..writeAsBytesSync(bad);
      await expectLater(
        svc.inspect(tampered, pass),
        throwsA(isA<BackupException>()),
        reason: 'byte $index',
      );
    }
    final truncated = File(p.join(dir.path, 'trunc.efbak'))
      ..writeAsBytesSync(original.sublist(0, original.length - 40));
    await expectLater(
      svc.inspect(truncated, pass),
      throwsA(isA<BackupException>()),
    );
  });

  test('non-backup files are rejected with a clear message', () async {
    final junk = File(p.join(dir.path, 'x.efbak'))
      ..writeAsStringSync('hello world, definitely not a backup file at all');
    await expectLater(
      svc.inspect(junk, pass),
      throwsA(
        isA<BackupException>().having(
          (e) => e.message,
          'm',
          contains('not an Energize'),
        ),
      ),
    );
    final tiny = File(p.join(dir.path, 'y.efbak'))..writeAsBytesSync([1, 2, 3]);
    await expectLater(svc.inspect(tiny, pass), throwsA(isA<BackupException>()));
  });

  test('retention keeps only the newest N backups', () async {
    for (var i = 0; i < 4; i++) {
      await svc.createBackup(folder: dir, passphrase: pass, keepLatest: 2);
      env.clock.advance(const Duration(minutes: 1));
    }
    final left = await svc.listBackups(dir);
    expect(left, hasLength(2));
    expect(left.first.path, contains('063300'), reason: 'newest first');
    expect(left.last.path, contains('063200'));
  });

  test('a backup from a newer schema is refused and stages nothing', () async {
    final future = BackupService(
      env.db,
      env.clock,
      pbkdf2Iterations: 2000,
      manifestSchemaVersion: AppDatabase.currentSchemaVersion + 1,
    );
    final f = await future.createBackup(folder: dir, passphrase: pass);
    final live = File(p.join(dir.path, 'live2.sqlite'))..writeAsStringSync('x');
    await expectLater(
      svc.stageRestore(backup: f, passphrase: pass, liveDatabase: live),
      throwsA(
        isA<BackupException>().having(
          (e) => e.message,
          'm',
          contains('newer version'),
        ),
      ),
    );
    expect(File('${live.path}.restore-pending').existsSync(), isFalse);
  });

  test('a damaged staged restore never replaces the live file', () async {
    final live = File(p.join(dir.path, 'live3.sqlite'))
      ..writeAsStringSync('keep me');
    final bad = File(p.join(dir.path, 'bad.efbak'))
      ..writeAsStringSync('nope nope nope nope nope nope nope nope');
    await expectLater(
      svc.stageRestore(backup: bad, passphrase: pass, liveDatabase: live),
      throwsA(isA<BackupException>()),
    );
    expect(File('${live.path}.restore-pending').existsSync(), isFalse);
    expect(live.readAsStringSync(), 'keep me');
  });
}
