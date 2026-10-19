import 'dart:io';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';

import '../core/providers.dart';
import '../database/app_database.dart';
import '../repositories/settings_store.dart';
import '../sync/cloud_controller.dart';
import 'backup_service.dart';
import 'session_controller.dart';

class BackupState {
  const BackupState({
    this.folder,
    this.lastBackup,
    this.busy = false,
    this.message,
    this.recent = const [],
    this.passwordRemembered = false,
    this.restoreStaged = false,
  });

  final String? folder;
  final DateTime? lastBackup;
  final bool busy;
  final String? message;
  final List<File> recent;
  final bool passwordRemembered;
  final bool restoreStaged;

  BackupState copyWith({
    String? folder,
    DateTime? lastBackup,
    bool? busy,
    String? message,
    bool clearMessage = false,
    List<File>? recent,
    bool? passwordRemembered,
    bool? restoreStaged,
  }) => BackupState(
    folder: folder ?? this.folder,
    lastBackup: lastBackup ?? this.lastBackup,
    busy: busy ?? this.busy,
    message: clearMessage ? null : (message ?? this.message),
    recent: recent ?? this.recent,
    passwordRemembered: passwordRemembered ?? this.passwordRemembered,
    restoreStaged: restoreStaged ?? this.restoreStaged,
  );
}

/// Overridden in tests; resolves the live database file in production.
final liveDatabaseFileProvider = Provider<Future<File> Function()>(
  (_) => AppDatabase.databaseFile,
);

final defaultBackupFolderProvider = Provider<Future<String?> Function()>(
  (_) => () async {
    try {
      final docs = await getApplicationDocumentsDirectory();
      return p.join(docs.path, 'EnergizeFitness', 'Backups');
    } catch (_) {
      return null;
    }
  },
);

/// PBKDF2 cost for new backups (lowered in tests only).
final backupIterationsProvider = Provider<int>((_) => 210000);

class BackupController extends Notifier<BackupState> {
  static const autoInterval = Duration(hours: 24);
  static const _kPass = 'backup.passphrase';

  @override
  BackupState build() {
    ref.listen(sessionProvider, (_, next) {
      if (next == null) {
        state = const BackupState();
      } else {
        Future.microtask(() async {
          await refresh();
          await autoBackupIfDue();
        });
      }
    });
    return const BackupState();
  }

  String? get _gymId => ref.read(sessionProvider)?.gymId;
  SettingsStore get _settings => ref.read(settingsStoreProvider);

  BackupService get _service => BackupService(
    ref.read(databaseProvider),
    ref.read(clockProvider),
    pbkdf2Iterations: ref.read(backupIterationsProvider),
  );

  Future<void> refresh() async {
    final gym = _gymId;
    if (gym == null) return;
    String? folder;
    try {
      folder =
          await _settings.get(gym, SettingsStore.backupFolder) ??
          await ref.read(defaultBackupFolderProvider)();
    } catch (_) {}
    final lastMs = int.tryParse(
      await _settings.get(gym, SettingsStore.lastBackupAt) ?? '',
    );
    var remembered = false;
    try {
      remembered = await ref.read(secretStoreProvider).read(_kPass) != null;
    } catch (_) {}
    if (!ref.mounted) return;
    // Missing folder / unavailable drive / no platform storage must never
    // break the app: the card just shows no backups.
    var recent = const <File>[];
    var staged = false;
    try {
      final live = await ref.read(liveDatabaseFileProvider)();
      staged = await PendingRestore.fileFor(live).exists();
      if (folder != null) {
        recent = await _service.listBackups(Directory(folder));
      }
    } catch (_) {}
    if (!ref.mounted) return;
    state = BackupState(
      folder: folder,
      lastBackup: lastMs == null
          ? null
          : DateTime.fromMillisecondsSinceEpoch(lastMs),
      recent: recent,
      passwordRemembered: remembered,
      restoreStaged: staged,
    );
  }

  Future<void> setFolder(String folder) async {
    final gym = _gymId;
    if (gym == null) return;
    await _settings.set(gym, SettingsStore.backupFolder, folder.trim());
    await refresh();
  }

  /// Takes a backup now. With [remember], the password is kept in the OS
  /// credential store so automatic daily backups can run.
  Future<void> backupNow(String passphrase, {bool remember = false}) async {
    final gym = _gymId;
    final folder = state.folder;
    if (gym == null || folder == null) {
      state = state.copyWith(message: 'Choose a backup folder first.');
      return;
    }
    state = state.copyWith(busy: true, clearMessage: true);
    try {
      final f = await _service.createBackup(
        folder: Directory(folder),
        passphrase: passphrase,
      );
      await _settings.set(
        gym,
        SettingsStore.lastBackupAt,
        '${ref.read(clockProvider).now().millisecondsSinceEpoch}',
      );
      if (remember) {
        await ref.read(secretStoreProvider).write(_kPass, passphrase);
      }
      await refresh();
      if (!ref.mounted) return;
      state = state.copyWith(message: 'Backup saved: ${p.basename(f.path)}');
    } on BackupException catch (e) {
      state = state.copyWith(busy: false, message: e.message);
    } catch (_) {
      state = state.copyWith(
        busy: false,
        message: 'Could not write the backup. Is the folder available?',
      );
    } finally {
      if (ref.mounted) state = state.copyWith(busy: false);
    }
  }

  /// Daily backup using the remembered password. Silent when nothing is due.
  Future<void> autoBackupIfDue() async {
    if (!ref.mounted) return;
    final s = state;
    final folder = s.folder;
    if (folder == null || !s.passwordRemembered || s.busy) return;
    final last = s.lastBackup;
    if (last != null &&
        ref.read(clockProvider).now().difference(last) < autoInterval) {
      return;
    }
    try {
      final pass = await ref.read(secretStoreProvider).read(_kPass);
      if (pass != null) await backupNow(pass);
    } catch (_) {}
  }

  Future<void> forgetPassword() async {
    try {
      await ref.read(secretStoreProvider).delete(_kPass);
    } catch (_) {}
    state = state.copyWith(passwordRemembered: false);
  }

  /// Verifies [backupPath] and stages it. The swap happens at next app start.
  Future<void> stageRestore(String backupPath, String passphrase) async {
    state = state.copyWith(busy: true, clearMessage: true);
    try {
      final info = await _service.stageRestore(
        backup: File(backupPath.trim()),
        passphrase: passphrase,
        liveDatabase: await ref.read(liveDatabaseFileProvider)(),
      );
      state = state.copyWith(
        restoreStaged: true,
        message:
            'Backup of "${info.gymName}" verified. Close and reopen the app to finish restoring. '
            'Your current data is kept as a safety copy.',
      );
    } on BackupException catch (e) {
      state = state.copyWith(message: e.message);
    } on FileSystemException {
      state = state.copyWith(message: 'Could not read that file.');
    } finally {
      state = state.copyWith(busy: false);
    }
  }
}

final backupControllerProvider =
    NotifierProvider<BackupController, BackupState>(BackupController.new);
