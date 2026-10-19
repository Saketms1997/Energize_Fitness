import 'package:drift/drift.dart';

import '../core/clock.dart';
import '../database/app_database.dart';

/// Key/value app settings stored per gym (non-secret values only; passwords
/// and tokens belong in the OS credential store, see SecretStore).
class SettingsStore {
  SettingsStore(this._db, this._clock);

  final AppDatabase _db;
  final Clock _clock;

  static const cloudUrl = 'cloud.url';
  static const cloudAnonKey = 'cloud.anonKey';
  static const autoSync = 'cloud.autoSync';
  static const autoWhatsapp = 'whatsapp.auto';
  static const backupFolder = 'backup.folder';
  static const backupKeep = 'backup.keep';
  static const lastBackupAt = 'backup.lastAtMs';

  Future<String?> get(String gymId, String key) async {
    final row =
        await (_db.select(_db.appSettings)
              ..where((s) => s.gymId.equals(gymId) & s.key.equals(key)))
            .getSingleOrNull();
    return row?.value;
  }

  Future<void> set(String gymId, String key, String value) => _db
      .into(_db.appSettings)
      .insertOnConflictUpdate(
        AppSettingsCompanion.insert(
          gymId: gymId,
          key: key,
          value: value,
          updatedAt: _clock.now(),
        ),
      );

  Future<void> remove(String gymId, String key) => (_db.delete(
    _db.appSettings,
  )..where((s) => s.gymId.equals(gymId) & s.key.equals(key))).go();

  Stream<String?> watch(String gymId, String key) =>
      (_db.select(_db.appSettings)
            ..where((s) => s.gymId.equals(gymId) & s.key.equals(key)))
          .watchSingleOrNull()
          .map((r) => r?.value);
}
