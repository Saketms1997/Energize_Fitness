import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';

/// PBKDF2-HMAC-SHA256 password hashing.
///
/// Encoded format: `pbkdf2-sha256$<iterations>$<saltB64>$<hashB64>`, so the
/// work factor can be raised later and old hashes still verify.
class PasswordHasher {
  PasswordHasher({this.iterations = 120000, Random? random})
    : _random = random ?? Random.secure();

  final int iterations;
  final Random _random;

  static const _scheme = 'pbkdf2-sha256';
  static const _keyLength = 32;

  String hash(String password) {
    final salt = Uint8List.fromList(
      List<int>.generate(16, (_) => _random.nextInt(256)),
    );
    final derived = _pbkdf2(utf8.encode(password), salt, iterations);
    return [
      _scheme,
      '$iterations',
      base64.encode(salt),
      base64.encode(derived),
    ].join(r'$');
  }

  bool verify(String password, String encoded) {
    final parts = encoded.split(r'$');
    if (parts.length != 4 || parts[0] != _scheme) return false;
    final iters = int.tryParse(parts[1]);
    if (iters == null || iters < 1) return false;
    try {
      final salt = base64.decode(parts[2]);
      final expected = base64.decode(parts[3]);
      final actual = _pbkdf2(utf8.encode(password), salt, iters);
      return _constantTimeEquals(actual, expected);
    } on FormatException {
      return false;
    }
  }

  /// Random recovery code, shown to the owner once (e.g. `K7QM-2XDA-...`).
  String generateRecoveryCode() {
    const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
    String group() => List.generate(
      4,
      (_) => alphabet[_random.nextInt(alphabet.length)],
    ).join();
    return List.generate(5, (_) => group()).join('-');
  }

  Uint8List _pbkdf2(List<int> password, List<int> salt, int iters) =>
      pbkdf2(password, salt, iters);

  /// Exposed for known-answer tests.
  static Uint8List pbkdf2(List<int> password, List<int> salt, int iters) {
    final hmac = Hmac(sha256, password);
    final out = BytesBuilder();
    var block = 1;
    while (out.length < _keyLength) {
      var u = hmac.convert([...salt, 0, 0, 0, block]).bytes;
      final t = List<int>.from(u);
      for (var i = 1; i < iters; i++) {
        u = hmac.convert(u).bytes;
        for (var j = 0; j < t.length; j++) {
          t[j] ^= u[j];
        }
      }
      out.add(t);
      block++;
    }
    return Uint8List.fromList(out.toBytes().sublist(0, _keyLength));
  }

  bool _constantTimeEquals(List<int> a, List<int> b) {
    if (a.length != b.length) return false;
    var diff = 0;
    for (var i = 0; i < a.length; i++) {
      diff |= a[i] ^ b[i];
    }
    return diff == 0;
  }
}
