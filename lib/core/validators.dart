/// Pure input validators. Each returns an error message, or null when valid.
class Validators {
  const Validators._();

  static String? required(String? v, {String field = 'This field'}) =>
      (v == null || v.trim().isEmpty) ? '$field is required' : null;

  static String? email(String? v) {
    final value = v?.trim() ?? '';
    if (value.isEmpty) return 'Email is required';
    final ok = RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]{2,}$').hasMatch(value);
    return ok ? null : 'Enter a valid email address';
  }

  /// Normalises an Indian mobile number to 10 digits, or returns null.
  static String? normalizeIndianMobile(String? v) {
    var d = (v ?? '').replaceAll(RegExp(r'[\s\-()]'), '');
    if (d.startsWith('+91')) d = d.substring(3);
    if (d.startsWith('91') && d.length == 12) d = d.substring(2);
    if (d.startsWith('0') && d.length == 11) d = d.substring(1);
    return RegExp(r'^[6-9]\d{9}$').hasMatch(d) ? d : null;
  }

  static String? indianMobile(String? v, {bool optional = false}) {
    if (optional && (v == null || v.trim().isEmpty)) return null;
    return normalizeIndianMobile(v) == null
        ? 'Enter a valid 10-digit mobile number'
        : null;
  }

  /// UPI VPA format: handle@provider.
  static String? upiId(String? v, {bool optional = true}) {
    final value = v?.trim() ?? '';
    if (value.isEmpty) return optional ? null : 'UPI ID is required';
    return RegExp(r'^[a-zA-Z0-9._\-]{2,256}@[a-zA-Z]{2,64}$').hasMatch(value)
        ? null
        : 'Enter a valid UPI ID (e.g. name@bank)';
  }

  static String? password(String? v) {
    final value = v ?? '';
    if (value.length < 10) return 'Use at least 10 characters';
    if (!RegExp(r'[A-Za-z]').hasMatch(value) ||
        !RegExp(r'\d').hasMatch(value)) {
      return 'Include both letters and numbers';
    }
    return null;
  }
}
