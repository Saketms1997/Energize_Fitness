import 'package:flutter_test/flutter_test.dart';
import 'package:gym_management_system/core/dates.dart';
import 'package:gym_management_system/database/app_database.dart';
import 'package:gym_management_system/models/membership_logic.dart';

Membership ms(String status, DateTime start, DateTime end, {String id = 'm'}) =>
    Membership(
      id: id,
      version: 1,
      createdAt: DateTime.utc(2026),
      updatedAt: DateTime.utc(2026),
      gymId: 'g',
      memberId: 'x',
      planId: 'p',
      planName: 'Monthly',
      pricePaise: 100000,
      startDate: start,
      endDate: end,
      status: status,
    );

void main() {
  group('GymDates', () {
    test('addMonths clamps to the end of shorter months', () {
      expect(
        GymDates.addMonths(DateTime.utc(2026, 1, 31), 1),
        DateTime.utc(2026, 2, 28),
      );
      expect(
        GymDates.addMonths(DateTime.utc(2028, 1, 31), 1),
        DateTime.utc(2028, 2, 29),
      );
      expect(
        GymDates.addMonths(DateTime.utc(2026, 11, 15), 3),
        DateTime.utc(2027, 2, 15),
      );
      expect(
        GymDates.addMonths(DateTime.utc(2026, 5, 1), 12),
        DateTime.utc(2027, 5, 1),
      );
    });

    test('membership end dates are inclusive', () {
      final start = DateTime.utc(2026, 1, 1);
      expect(
        GymDates.membershipEnd(start, 'months', 1),
        DateTime.utc(2026, 1, 31),
      );
      expect(
        GymDates.membershipEnd(start, 'months', 3),
        DateTime.utc(2026, 3, 31),
      );
      expect(
        GymDates.membershipEnd(start, 'months', 6),
        DateTime.utc(2026, 6, 30),
      );
      expect(
        GymDates.membershipEnd(start, 'months', 12),
        DateTime.utc(2026, 12, 31),
      );
      expect(
        GymDates.membershipEnd(start, 'days', 30),
        DateTime.utc(2026, 1, 30),
      );
      expect(GymDates.membershipEnd(start, 'days', 1), start);
    });

    test('today uses the gym timezone (IST is UTC+5:30)', () {
      // 20:00 UTC on 31 Jan is already 1 Feb 01:30 in India.
      final t = DateTime.utc(2026, 1, 31, 20);
      expect(GymDates.today(t, 'Asia/Kolkata'), DateTime.utc(2026, 2, 1));
      expect(GymDates.today(t, 'UTC'), DateTime.utc(2026, 1, 31));
      expect(
        GymDates.startOfLocalDayUtc(t, 'Asia/Kolkata'),
        DateTime.utc(2026, 1, 31, 18, 30),
      );
    });
  });

  group('membership status', () {
    final today = DateTime.utc(2026, 3, 10);
    test('active past its end date reads as expired', () {
      final m = ms(
        'active',
        DateTime.utc(2026, 1, 1),
        DateTime.utc(2026, 3, 9),
      );
      expect(effectiveMembershipStatus(m, today), 'expired');
    });
    test('active on its last day is still active', () {
      final m = ms(
        'active',
        DateTime.utc(2026, 2, 10),
        DateTime.utc(2026, 3, 10),
      );
      expect(effectiveMembershipStatus(m, today), 'active');
    });
    test('overall status prefers active, then frozen, pending, expired', () {
      final expired = ms('active', DateTime.utc(2025), DateTime.utc(2025, 2));
      final active = ms(
        'active',
        DateTime.utc(2026, 3),
        DateTime.utc(2026, 4),
        id: 'a',
      );
      final cancelled = ms(
        'cancelled',
        DateTime.utc(2026),
        DateTime.utc(2026, 2),
        id: 'c',
      );
      expect(
        overallMemberStatus([expired, active, cancelled], today),
        'active',
      );
      expect(overallMemberStatus([expired, cancelled], today), 'expired');
      expect(overallMemberStatus([cancelled], today), 'cancelled');
      expect(overallMemberStatus(const [], today), 'none');
    });
    test('current membership ignores cancelled unless it is the only one', () {
      final old = ms(
        'active',
        DateTime.utc(2026),
        DateTime.utc(2026, 2),
        id: 'old',
      );
      final newer = ms(
        'cancelled',
        DateTime.utc(2026, 3),
        DateTime.utc(2026, 4),
        id: 'new',
      );
      expect(currentMembership([old, newer])!.id, 'old');
      expect(currentMembership([newer])!.id, 'new');
    });
    test('name normalisation collapses case and spaces', () {
      expect(normalizeName('  Ravi   KUMAR '), 'ravi kumar');
    });
  });
}
