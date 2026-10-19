import 'package:drift/drift.dart';

import '../core/clock.dart';
import '../core/dates.dart';
import '../database/app_database.dart';
import '../models/membership_logic.dart';
import 'support.dart';

class ExpiringMembership {
  const ExpiringMembership(this.member, this.membership, this.daysLeft);
  final Member member;
  final Membership membership;
  final int daysLeft;
}

class DashboardData {
  const DashboardData({
    required this.totalMembers,
    required this.activeMembers,
    required this.expiredMembers,
    required this.newRegistrationsThisMonth,
    required this.todayCollectionPaise,
    required this.monthRevenuePaise,
    required this.pendingPaise,
    required this.upcomingExpiries,
  });

  final int totalMembers;
  final int activeMembers;
  final int expiredMembers;
  final int newRegistrationsThisMonth;

  /// Money actually received: verified, not refunded.
  final int todayCollectionPaise;
  final int monthRevenuePaise;

  /// Outstanding balances. Never included in collection/revenue.
  final int pendingPaise;
  final List<ExpiringMembership> upcomingExpiries;
}

class DashboardRepository {
  DashboardRepository(this._db, this._clock);

  final AppDatabase _db;
  final Clock _clock;

  Stream<DashboardData> watch(String gymId, {int expiryWindowDays = 7}) =>
      watchQuery(_db, [
        _db.members,
        _db.memberships,
        _db.payments,
      ], () => load(gymId, expiryWindowDays: expiryWindowDays));

  Future<DashboardData> load(String gymId, {int expiryWindowDays = 7}) async {
    final gym = await (_db.select(
      _db.gyms,
    )..where((g) => g.id.equals(gymId))).getSingle();
    final now = _clock.now();
    final today = GymDates.today(now, gym.timezone);
    final offset = GymDates.offsetFor(gym.timezone);
    final dayStart = today.subtract(offset);
    final dayEnd = dayStart.add(const Duration(days: 1));
    final monthStartLocal = DateTime.utc(today.year, today.month, 1);
    final monthStart = monthStartLocal.subtract(offset);
    final nextMonthStart = DateTime.utc(
      today.year,
      today.month + 1,
      1,
    ).subtract(offset);

    final members = await (_db.select(
      _db.members,
    )..where((m) => m.gymId.equals(gymId) & m.deletedAt.isNull())).get();
    final memberships = await (_db.select(
      _db.memberships,
    )..where((m) => m.gymId.equals(gymId) & m.deletedAt.isNull())).get();
    final verified =
        await (_db.select(_db.payments)..where(
              (p) =>
                  p.gymId.equals(gymId) &
                  p.status.equals('verified') &
                  p.deletedAt.isNull(),
            ))
            .get();

    final byMember = <String, List<Membership>>{};
    for (final m in memberships) {
      byMember.putIfAbsent(m.memberId, () => []).add(m);
    }
    var active = 0, expired = 0, newThisMonth = 0;
    for (final m in members) {
      final s = overallMemberStatus(byMember[m.id] ?? const [], today);
      if (s == 'active') active++;
      if (s == 'expired') expired++;
      if (!m.registeredAt.isBefore(monthStart) &&
          m.registeredAt.isBefore(nextMonthStart)) {
        newThisMonth++;
      }
    }

    var todayPaise = 0, monthPaise = 0;
    final paidByMembership = <String, int>{};
    for (final p in verified) {
      paidByMembership.update(
        p.membershipId,
        (v) => v + p.amountPaise,
        ifAbsent: () => p.amountPaise,
      );
      final at = p.verifiedAt;
      if (at == null) continue;
      if (!at.isBefore(dayStart) && at.isBefore(dayEnd)) {
        todayPaise += p.amountPaise;
      }
      if (!at.isBefore(monthStart) && at.isBefore(nextMonthStart)) {
        monthPaise += p.amountPaise;
      }
    }

    var pending = 0;
    for (final m in memberships) {
      if (m.status == 'cancelled') continue;
      final due = m.pricePaise - (paidByMembership[m.id] ?? 0);
      if (due > 0) pending += due;
    }

    final membersById = {for (final m in members) m.id: m};
    final expiring = <ExpiringMembership>[];
    for (final ms in memberships) {
      if (ms.status != 'active') continue;
      final left = GymDates.daysBetween(today, ms.endDate);
      final member = membersById[ms.memberId];
      if (member != null && left >= 0 && left <= expiryWindowDays) {
        expiring.add(ExpiringMembership(member, ms, left));
      }
    }
    expiring.sort((a, b) => a.daysLeft.compareTo(b.daysLeft));

    return DashboardData(
      totalMembers: members.length,
      activeMembers: active,
      expiredMembers: expired,
      newRegistrationsThisMonth: newThisMonth,
      todayCollectionPaise: todayPaise,
      monthRevenuePaise: monthPaise,
      pendingPaise: pending,
      upcomingExpiries: expiring,
    );
  }
}
