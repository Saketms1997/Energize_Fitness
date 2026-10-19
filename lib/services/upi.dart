import '../core/validators.dart';

/// Builds NPCI `upi://pay` deep links (also what a UPI QR code encodes).
class UpiLink {
  const UpiLink._();

  /// Returns null when the payee VPA is missing or malformed.
  /// [amountPaise] <= 0 omits the amount so the payer types it.
  static String? build({
    required String? upiId,
    required String payeeName,
    int amountPaise = 0,
    String? note,
  }) {
    final vpa = upiId?.trim() ?? '';
    if (vpa.isEmpty || Validators.upiId(vpa, optional: false) != null) {
      return null;
    }
    final params = <String, String>{
      'pa': vpa,
      'pn': _clean(payeeName, 50),
      if (amountPaise > 0) 'am': _amount(amountPaise),
      'cu': 'INR',
      if (note != null && _clean(note, 50).isNotEmpty) 'tn': _clean(note, 50),
    };
    final query = params.entries
        .map((e) => '${e.key}=${Uri.encodeComponent(e.value)}')
        .join('&');
    return 'upi://pay?$query';
  }

  static String _amount(int paise) =>
      '${paise ~/ 100}.${(paise % 100).toString().padLeft(2, '0')}';

  static String _clean(String v, int max) {
    final s = v.replaceAll(RegExp(r'[\r\n\t]+'), ' ').trim();
    return s.length <= max ? s : s.substring(0, max);
  }
}
