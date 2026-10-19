import 'package:flutter_test/flutter_test.dart';
import 'package:gym_management_system/core/security/password_hasher.dart';

void main() {
  // Low iteration count keeps tests fast; production default is 120000.
  final hasher = PasswordHasher(iterations: 1000);

  test('hash is salted and never contains the password', () {
    final a = hasher.hash('correct horse 42');
    final b = hasher.hash('correct horse 42');
    expect(a, isNot(b));
    expect(a.contains('correct horse'), isFalse);
  });

  test('verifies correct and rejects wrong password', () {
    final h = hasher.hash('correct horse 42');
    expect(hasher.verify('correct horse 42', h), isTrue);
    expect(hasher.verify('correct horse 43', h), isFalse);
  });

  test('rejects malformed encodings', () {
    expect(hasher.verify('x', 'plaintext'), isFalse);
    expect(hasher.verify('x', r'pbkdf2-sha256$abc$!!$!!'), isFalse);
  });

  test('PBKDF2-HMAC-SHA256 matches published known-answer vectors', () {
    String hex(List<int> b) =>
        b.map((x) => x.toRadixString(16).padLeft(2, '0')).join();
    final pw = 'password'.codeUnits, salt = 'salt'.codeUnits;
    expect(
      hex(PasswordHasher.pbkdf2(pw, salt, 1)),
      '120fb6cffcf8b32c43e7225256c4f837a86548c92ccc35480805987cb70be17b',
    );
    expect(
      hex(PasswordHasher.pbkdf2(pw, salt, 2)),
      'ae4d0c95af6b46d32d0adff928f06dd02a303f8ef3c251dfd6e2d85a95474c43',
    );
  });

  test('recovery codes are well-formed and unique', () {
    final c1 = hasher.generateRecoveryCode();
    final c2 = hasher.generateRecoveryCode();
    expect(c1, matches(RegExp(r'^([A-Z2-9]{4}-){4}[A-Z2-9]{4}$')));
    expect(c1, isNot(c2));
  });
}
