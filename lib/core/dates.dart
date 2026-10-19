/// Calendar-date helpers. Dates such as membership start/end are stored as
/// `DateTime.utc(y, m, d)` (midnight UTC) and treated as date-only values.
class GymDates {
  const GymDates._();

  /// Fixed UTC offsets for supported gym timezones. Asia/Kolkata has no DST.
  /// Unknown zones fall back to UTC until a full tz database is added.
  static Duration offsetFor(String timezone) => switch (timezone) {
    'Asia/Kolkata' => const Duration(hours: 5, minutes: 30),
    _ => Duration.zero,
  };

  /// Normalises to a date-only value (midnight UTC).
  ///
  /// Invariant: date-only values are always UTC-midnight instants. Drift hands
  /// stored timestamps back as *local* DateTimes, so this goes through UTC
  /// first; reading `.day` of the local value could land on the wrong date.
  /// UI code must build dates with `DateTime.utc(y, m, d)` from picker output.
  static DateTime dateOnly(DateTime d) {
    final u = d.toUtc();
    return DateTime.utc(u.year, u.month, u.day);
  }

  /// Today's calendar date in the gym's timezone.
  static DateTime today(DateTime nowUtc, String timezone) =>
      dateOnly(nowUtc.toUtc().add(offsetFor(timezone)));

  /// Start of the gym-local day containing [nowUtc], as a UTC instant.
  static DateTime startOfLocalDayUtc(DateTime nowUtc, String timezone) =>
      today(nowUtc, timezone).subtract(offsetFor(timezone));

  /// Adds calendar months, clamping the day (31 Jan + 1 month = 28/29 Feb).
  static DateTime addMonths(DateTime d, int months) {
    final total = d.month - 1 + months;
    final year = d.year + total ~/ 12;
    final month = total % 12 + 1;
    final lastDay = DateTime.utc(year, month + 1, 0).day;
    return DateTime.utc(year, month, d.day > lastDay ? lastDay : d.day);
  }

  /// Last day (inclusive) of a membership starting on [start].
  /// 1 month from 1 Jan ends 31 Jan; 30 days from 1 Jan ends 30 Jan.
  static DateTime membershipEnd(DateTime start, String unit, int value) {
    final s = dateOnly(start);
    return unit == 'months'
        ? addMonths(s, value).subtract(const Duration(days: 1))
        : s.add(Duration(days: value - 1));
  }

  static int daysBetween(DateTime from, DateTime to) =>
      dateOnly(to).difference(dateOnly(from)).inDays;
}
