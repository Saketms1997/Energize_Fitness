import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:cryptography/cryptography.dart';
import 'package:flutter/foundation.dart' show visibleForTesting;
import 'package:path/path.dart' as p;
import 'package:sqlite3/sqlite3.dart' as sql;

import '../core/clock.dart';
import '../database/app_database.dart';

class BackupException implements Exception {
  BackupException(this.message);
  final String message;
  @override
  String toString() => message;
}

class BackupInfo {
  const BackupInfo({
    required this.file,
    required this.createdAt,
    required this.schemaVersion,
    required this.gymName,
  });
  final File file;
  final DateTime createdAt;
  final int schemaVersion;
  final String gymName;
}

/// Encrypted, verified backups of the whole database.
///
/// File layout (all integers big-endian):
///   "EFBK" | version(1) | pbkdf2 iterations(4) | salt(16) | nonce(12)
///   | AES-256-GCM( len(4) | manifest JSON | SQLite bytes ) | mac(16)
/// The header is authenticated as AAD, so tampering with it fails decryption.
class BackupService {
  BackupService(
    this._db,
    this._clock, {
    this.pbkdf2Iterations = 210000,
    @visibleForTesting this.manifestSchemaVersion,
  });

  static const extension = '.efbak';
  static const _magic = [0x45, 0x46, 0x42, 0x4B]; // EFBK
  static const _formatVersion = 1;
  static const minPassphraseLength = 8;

  final AppDatabase _db;
  final Clock _clock;

  /// Cost factor for new backups. Stored in each file, so it can be raised
  /// later without breaking old backups.
  final int pbkdf2Iterations;

  /// Tests forge backups from other app versions with this.
  final int? manifestSchemaVersion;

  final _aes = AesGcm.with256bits();

  Future<SecretKey> _key(String pass, List<int> salt, int iterations) => Pbkdf2(
    macAlgorithm: Hmac.sha256(),
    iterations: iterations,
    bits: 256,
  ).deriveKeyFromPassword(password: pass, nonce: salt);

  static String _stamp(DateTime t) {
    final u = t.toUtc();
    String two(int n) => n.toString().padLeft(2, '0');
    return '${u.year}${two(u.month)}${two(u.day)}-${two(u.hour)}${two(u.minute)}${two(u.second)}';
  }

  /// Writes an encrypted backup into [folder] and returns the file. The
  /// snapshot is taken with `VACUUM INTO`, which is consistent even while the
  /// app keeps using the database.
  Future<File> createBackup({
    required Directory folder,
    required String passphrase,
    int keepLatest = 14,
  }) async {
    if (passphrase.length < minPassphraseLength) {
      throw BackupException(
        'Use a backup password of at least $minPassphraseLength characters.',
      );
    }
    await folder.create(recursive: true);
    final tmp = await Directory.systemTemp.createTemp('efbk_');
    try {
      final plain = File(p.join(tmp.path, 'snapshot.sqlite'));
      await _db.customStatement('VACUUM INTO ?', [plain.path]);
      _checkIntegrity(plain);
      final dbBytes = await plain.readAsBytes();

      final gym = await _db.select(_db.gyms).getSingleOrNull();
      final manifest = utf8.encode(
        jsonEncode({
          'schemaVersion':
              manifestSchemaVersion ?? AppDatabase.currentSchemaVersion,
          'createdAtMs': _clock.now().millisecondsSinceEpoch,
          'gymName': gym?.name ?? '',
          'dbBytes': dbBytes.length,
        }),
      );
      final body = BytesBuilder()
        ..add(_u32(manifest.length))
        ..add(manifest)
        ..add(dbBytes);

      final salt = _random(16);
      final nonce = _random(12);
      final header = BytesBuilder()
        ..add(_magic)
        ..addByte(_formatVersion)
        ..add(_u32(pbkdf2Iterations))
        ..add(salt)
        ..add(nonce);
      final box = await _aes.encrypt(
        body.toBytes(),
        secretKey: await _key(passphrase, salt, pbkdf2Iterations),
        nonce: nonce,
        aad: header.toBytes(),
      );
      final out = BytesBuilder()
        ..add(header.toBytes())
        ..add(box.cipherText)
        ..add(box.mac.bytes);

      final file = File(
        p.join(
          folder.path,
          'energize-backup-${_stamp(_clock.now())}$extension',
        ),
      );
      // Write beside, then rename: a crash never leaves a half-written backup
      // under the real name.
      final partial = File('${file.path}.part');
      await partial.writeAsBytes(out.toBytes(), flush: true);
      await partial.rename(file.path);

      // Prove the file we just wrote can be opened with this password.
      await _decrypt(file, passphrase);
      await _prune(folder, keepLatest);
      return file;
    } finally {
      await tmp.delete(recursive: true);
    }
  }

  /// Decrypts [file] and returns the database bytes plus its manifest.
  /// Throws [BackupException] for a wrong password or a damaged/foreign file.
  Future<(Map<String, dynamic>, Uint8List)> _decrypt(
    File file,
    String passphrase,
  ) async {
    final bytes = await file.readAsBytes();
    const headerLen = 4 + 1 + 4 + 16 + 12;
    if (bytes.length < headerLen + 16 + 4 ||
        !_listEq(bytes.sublist(0, 4), _magic)) {
      throw BackupException('This is not an Energize Fitness backup file.');
    }
    if (bytes[4] != _formatVersion) {
      throw BackupException(
        'This backup was made by a newer version of the app. Update the app first.',
      );
    }
    final iterations = ByteData.sublistView(bytes, 5, 9).getUint32(0);
    if (iterations < 1000 || iterations > 5000000) {
      throw BackupException('The backup file is damaged.');
    }
    final salt = bytes.sublist(9, 25);
    final nonce = bytes.sublist(25, 37);
    final cipher = bytes.sublist(headerLen, bytes.length - 16);
    final mac = Mac(bytes.sublist(bytes.length - 16));
    List<int> plain;
    try {
      plain = await _aes.decrypt(
        SecretBox(cipher, nonce: nonce, mac: mac),
        secretKey: await _key(passphrase, salt, iterations),
        aad: bytes.sublist(0, headerLen),
      );
    } on SecretBoxAuthenticationError {
      throw BackupException('Wrong backup password, or the file is damaged.');
    }
    final data = Uint8List.fromList(plain);
    final mLen = ByteData.sublistView(data, 0, 4).getUint32(0);
    if (mLen > data.length - 4) {
      throw BackupException('The backup file is damaged.');
    }
    final manifest = jsonDecode(
      utf8.decode(data.sublist(4, 4 + mLen)),
    ) as Map<String, dynamic>;
    return (manifest, Uint8List.sublistView(data, 4 + mLen));
  }

  /// Reads a backup's details without restoring it.
  Future<BackupInfo> inspect(File file, String passphrase) async {
    final (m, _) = await _decrypt(file, passphrase);
    return BackupInfo(
      file: file,
      createdAt: DateTime.fromMillisecondsSinceEpoch(m['createdAtMs'] as int),
      schemaVersion: m['schemaVersion'] as int,
      gymName: m['gymName'] as String,
    );
  }

  /// Verifies a backup and stages it so the NEXT app start swaps it in (the
  /// running database cannot be replaced safely while open). Nothing about the
  /// live data changes until then.
  Future<BackupInfo> stageRestore({
    required File backup,
    required String passphrase,
    required File liveDatabase,
  }) async {
    final (m, dbBytes) = await _decrypt(backup, passphrase);
    final schema = m['schemaVersion'] as int;
    if (schema > AppDatabase.currentSchemaVersion) {
      throw BackupException(
        'This backup is from a newer version of the app (schema $schema). '
        'Update the app, then restore.',
      );
    }
    final pending = PendingRestore.fileFor(liveDatabase);
    final tmp = File('${pending.path}.part');
    await tmp.writeAsBytes(dbBytes, flush: true);
    try {
      _checkIntegrity(tmp);
    } catch (_) {
      await tmp.delete();
      rethrow;
    }
    if (await pending.exists()) await pending.delete();
    await tmp.rename(pending.path);
    return BackupInfo(
      file: backup,
      createdAt: DateTime.fromMillisecondsSinceEpoch(m['createdAtMs'] as int),
      schemaVersion: schema,
      gymName: m['gymName'] as String,
    );
  }

  /// Newest first.
  Future<List<File>> listBackups(Directory folder) async {
    if (!await folder.exists()) return [];
    final files = [
      await for (final e in folder.list())
        if (e is File && e.path.endsWith(extension)) e,
    ]..sort((a, b) => b.path.compareTo(a.path)); // names embed the timestamp
    return files;
  }

  Future<void> _prune(Directory folder, int keep) async {
    final all = await listBackups(folder);
    for (final f in all.skip(keep < 1 ? 1 : keep)) {
      await f.delete();
    }
  }

  void _checkIntegrity(File f) {
    final db = sql.sqlite3.open(f.path, mode: sql.OpenMode.readOnly);
    try {
      final r = db.select('PRAGMA integrity_check');
      if (r.length != 1 || r.first.values.first != 'ok') {
        throw BackupException('The database failed its integrity check.');
      }
    } on sql.SqliteException {
      throw BackupException('The file is not a valid database.');
    } finally {
      db.close();
    }
  }

  static Uint8List _u32(int v) =>
      (ByteData(4)..setUint32(0, v)).buffer.asUint8List();

  static Uint8List _random(int n) {
    final r = SecureRandom.defaultRandom;
    return Uint8List.fromList(List.generate(n, (_) => r.nextInt(256)));
  }

  static bool _listEq(List<int> a, List<int> b) {
    if (a.length != b.length) return false;
    for (var i = 0; i < a.length; i++) {
      if (a[i] != b[i]) return false;
    }
    return true;
  }
}

/// Applies a staged restore at startup, before the database is opened.
class PendingRestore {
  const PendingRestore._();

  static File fileFor(File live) => File('${live.path}.restore-pending');

  /// Swaps the staged database in. The previous database is kept beside it as
  /// `*.before-restore-<time>` so nothing is ever destroyed. Returns true when
  /// a restore was applied.
  static Future<bool> applyIfPresent(File live, DateTime now) async {
    final pending = fileFor(live);
    if (!await pending.exists()) return false;
    if (await live.exists()) {
      final keep = File(
        '${live.path}.before-restore-${BackupService._stamp(now)}',
      );
      await live.rename(keep.path);
    }
    // SQLite sidecar files belong to the old database.
    for (final s in ['-wal', '-shm', '-journal']) {
      final side = File('${live.path}$s');
      if (await side.exists()) await side.delete();
    }
    await pending.rename(live.path);
    return true;
  }
}
