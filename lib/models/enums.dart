/// Persisted as `.name` strings; SQL check constraints list the same values.
enum MembershipStatus {
  pendingPayment('pending_payment'),
  active('active'),
  frozen('frozen'),
  expired('expired'),
  cancelled('cancelled');

  const MembershipStatus(this.value);
  final String value;

  static MembershipStatus parse(String v) =>
      values.firstWhere((s) => s.value == v);
}

enum PaymentStatus { pending, verified, failed, rejected, refunded }

enum PaymentMethod { upi, cash, other }

enum DurationUnit { days, months }
