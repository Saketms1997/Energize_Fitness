import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/money.dart';
import '../../core/providers.dart';
import '../../core/router.dart';
import '../../repositories/dashboard_repository.dart';
import '../../services/session_controller.dart';
import '../../shared/formatters.dart';

class DashboardScreen extends ConsumerWidget {
  const DashboardScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final name = ref.watch(sessionProvider.select((s) => s?.ownerName)) ?? '';
    final data = ref.watch(dashboardProvider);
    return ListView(
      padding: const EdgeInsets.all(24),
      children: [
        Text(
          'Welcome, $name',
          style: Theme.of(context).textTheme.headlineSmall,
        ),
        const SizedBox(height: 16),
        data.when(
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (_, _) => const Text('Could not load the dashboard.'),
          data: (d) => _Body(d),
        ),
      ],
    );
  }
}

class _Body extends StatelessWidget {
  const _Body(this.d);
  final DashboardData d;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Wrap(
          spacing: 12,
          runSpacing: 12,
          children: [
            _Metric('Total members', '${d.totalMembers}'),
            _Metric('Active', '${d.activeMembers}'),
            _Metric('Expired', '${d.expiredMembers}'),
            _Metric('New this month', '${d.newRegistrationsThisMonth}'),
            _Metric(
              "Today's collection",
              Money.formatPaise(d.todayCollectionPaise),
              note: 'Verified payments only',
            ),
            _Metric(
              'Revenue this month',
              Money.formatPaise(d.monthRevenuePaise),
              note: 'Verified payments only',
            ),
            _Metric(
              'Outstanding balance',
              Money.formatPaise(d.pendingPaise),
              note: 'Not yet received',
              emphasis: d.pendingPaise > 0,
            ),
          ],
        ),
        const SizedBox(height: 24),
        Text(
          'Expiring in the next 7 days',
          style: Theme.of(context).textTheme.titleMedium,
        ),
        const SizedBox(height: 8),
        if (d.upcomingExpiries.isEmpty)
          const Text('No memberships are about to expire.')
        else
          for (final e in d.upcomingExpiries)
            Card(
              child: ListTile(
                title: Text(e.member.fullName),
                subtitle: Text(
                  '${e.membership.planName} · ends ${formatDate(e.membership.endDate)}',
                ),
                trailing: Text(
                  e.daysLeft == 0
                      ? 'Today'
                      : '${e.daysLeft} day${e.daysLeft == 1 ? '' : 's'}',
                ),
                onTap: () => context.go(Routes.member(e.member.id)),
              ),
            ),
      ],
    );
  }
}

class _Metric extends StatelessWidget {
  const _Metric(this.label, this.value, {this.note, this.emphasis = false});
  final String label;
  final String value;
  final String? note;
  final bool emphasis;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return SizedBox(
      width: 200,
      child: Card(
        color: emphasis ? t.colorScheme.secondaryContainer : null,
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(label, style: t.textTheme.labelLarge),
              const SizedBox(height: 6),
              Text(value, style: t.textTheme.headlineSmall),
              if (note != null) Text(note!, style: t.textTheme.bodySmall),
            ],
          ),
        ),
      ),
    );
  }
}
