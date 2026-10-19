/// Money is always stored as integer paise; never as double.
class Money {
  const Money._();

  /// Parses user input such as "1,500" or "1500.50" into paise.
  /// Returns null when the input is not a valid non-negative amount.
  static int? parseRupeesToPaise(String input) {
    final cleaned = input.trim().replaceAll(',', '');
    final match = RegExp(r'^(\d{1,9})(?:\.(\d{1,2}))?$').firstMatch(cleaned);
    if (match == null) return null;
    final rupees = int.parse(match.group(1)!);
    final fraction = (match.group(2) ?? '').padRight(2, '0');
    return rupees * 100 + int.parse(fraction);
  }

  static String formatPaise(int paise, {String symbol = '₹'}) {
    final sign = paise < 0 ? '-' : '';
    final abs = paise.abs();
    final rupees = abs ~/ 100;
    final fraction = (abs % 100).toString().padLeft(2, '0');
    final digits = rupees.toString();
    // Indian digit grouping: last 3 digits, then groups of 2.
    String grouped;
    if (digits.length <= 3) {
      grouped = digits;
    } else {
      var rest = digits.substring(0, digits.length - 3);
      final tail = digits.substring(digits.length - 3);
      final parts = <String>[];
      while (rest.length > 2) {
        parts.insert(0, rest.substring(rest.length - 2));
        rest = rest.substring(0, rest.length - 2);
      }
      if (rest.isNotEmpty) parts.insert(0, rest);
      grouped = '${parts.join(',')},$tail';
    }
    return '$sign$symbol$grouped.$fraction';
  }
}
