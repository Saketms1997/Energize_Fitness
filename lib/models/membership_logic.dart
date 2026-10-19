import '../core/dates.dart';
import '../database/app_database.dart';

/// Status shown to the owner. A stored 'active' membership whose end date has
/// passed is reported as expired, so no background job is needed for expiry.
String effectiveMembershipStatus(Membership m, DateTime today) {
  if (m.status == 'active' && m.endDate.isBefore(GymDates.dateOnly(today))) {
    return 'expired';
  }
  return m.status;
}

/// Overall status of a member across all of their memberships.
/// One of: active, frozen, pending_payment, expired, cancelled, none.
String overallMemberStatus(Iterable<Membership> memberships, DateTime today) {
  final statuses = memberships
      .map((m) => effectiveMembershipStatus(m, today))
      .toSet();
  for (final s in const [
    'active',
    'frozen',
    'pending_payment',
    'expired',
    'cancelled',
  ]) {
    if (statuses.contains(s)) return s;
  }
  return 'none';
}

/// Membership that best represents "current": the latest non-cancelled one by
/// start date, falling back to the latest cancelled one.
Membership? currentMembership(Iterable<Membership> memberships) {
  Membership? best;
  Membership? bestCancelled;
  for (final m in memberships) {
    if (m.status == 'cancelled') {
      if (bestCancelled == null ||
          m.startDate.isAfter(bestCancelled.startDate)) {
        bestCancelled = m;
      }
    } else if (best == null || m.startDate.isAfter(best.startDate)) {
      best = m;
    }
  }
  return best ?? bestCancelled;
}

/// Normalised form used for duplicate-name comparison.
String normalizeName(String name) =>
    name.trim().toLowerCase().replaceAll(RegExp(r'\s+'), ' ');
