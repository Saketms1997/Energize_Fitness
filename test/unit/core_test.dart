import 'package:flutter_test/flutter_test.dart';
import 'package:gym_management_system/core/money.dart';
import 'package:gym_management_system/core/validators.dart';

void main() {
  group('Money', () {
    test('parses rupees to exact paise', () {
      expect(Money.parseRupeesToPaise('1500'), 150000);
      expect(Money.parseRupeesToPaise('1,500.5'), 150050);
      expect(Money.parseRupeesToPaise('0.07'), 7);
      expect(Money.parseRupeesToPaise('-5'), isNull);
      expect(Money.parseRupeesToPaise('1.234'), isNull);
      expect(Money.parseRupeesToPaise('abc'), isNull);
    });
    test('formats with Indian grouping', () {
      expect(Money.formatPaise(150000), '₹1,500.00');
      expect(Money.formatPaise(12345678900), '₹12,34,56,789.00');
      expect(Money.formatPaise(5), '₹0.05');
    });
  });

  group('Validators', () {
    test('normalises Indian mobiles', () {
      expect(Validators.normalizeIndianMobile('+91 98765-43210'), '9876543210');
      expect(Validators.normalizeIndianMobile('09876543210'), '9876543210');
      expect(Validators.normalizeIndianMobile('1234567890'), isNull);
      expect(Validators.normalizeIndianMobile('98765'), isNull);
    });
    test('UPI id', () {
      expect(Validators.upiId('owner@okbank'), isNull);
      expect(Validators.upiId('no-at-sign'), isNotNull);
      expect(Validators.upiId('', optional: false), isNotNull);
    });
    test('password policy', () {
      expect(Validators.password('short1'), isNotNull);
      expect(Validators.password('onlyletterslong'), isNotNull);
      expect(Validators.password('goodpass1234'), isNull);
    });
  });
}
