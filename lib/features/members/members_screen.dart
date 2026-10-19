import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/providers.dart';
import '../../core/router.dart';
import '../../shared/formatters.dart';

class MembersScreen extends ConsumerStatefulWidget {
  const MembersScreen({super.key});

  @override
  ConsumerState<MembersScreen> createState() => _MembersScreenState();
}

class _MembersScreenState extends ConsumerState<MembersScreen> {
  String _query = '';
  String? _status;

  static const _filters = <(String?, String)>[
    (null, 'All'),
    ('active', 'Active'),
    ('pending_payment', 'Awaiting payment'),
    ('frozen', 'Frozen'),
    ('expired', 'Expired'),
    ('cancelled', 'Cancelled'),
  ];

  @override
  Widget build(BuildContext context) {
    final members = ref.watch(
      memberSummariesProvider((query: _query, status: _status)),
    );
    return Scaffold(
      floatingActionButton: FloatingActionButton.extended(
        key: const Key('registerMember'),
        onPressed: () => context.go(Routes.newMember),
        icon: const Icon(Icons.person_add),
        label: const Text('Register member'),
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
            child: TextField(
              key: const Key('memberSearch'),
              decoration: const InputDecoration(
                prefixIcon: Icon(Icons.search),
                labelText: 'Search by name, phone or member ID',
              ),
              onChanged: (v) => setState(() => _query = v),
            ),
          ),
          SizedBox(
            height: 48,
            child: ListView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 12),
              children: [
                for (final f in _filters)
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 4),
                    child: ChoiceChip(
                      label: Text(f.$2),
                      selected: _status == f.$1,
                      onSelected: (_) => setState(() => _status = f.$1),
                    ),
                  ),
              ],
            ),
          ),
          Expanded(
            child: members.when(
              loading: () => const Center(child: CircularProgressIndicator()),
              error: (_, _) =>
                  const Center(child: Text('Could not load members.')),
              data: (list) {
                if (list.isEmpty) {
                  return Center(
                    child: Text(
                      _query.isEmpty && _status == null
                          ? 'No members yet. Register your first member.'
                          : 'No members match.',
                    ),
                  );
                }
                return ListView.builder(
                  padding: const EdgeInsets.only(bottom: 88),
                  itemCount: list.length,
                  itemBuilder: (_, i) {
                    final s = list[i];
                    final ms = s.membership;
                    return ListTile(
                      key: Key('member-${s.member.memberCode}'),
                      title: Text(s.member.fullName),
                      subtitle: Text(
                        '${s.member.memberCode} · ${s.member.phone}'
                        '${ms == null ? '' : ' · ${ms.planName} to ${formatDate(ms.endDate)}'}',
                      ),
                      trailing: StatusChip(s.status),
                      onTap: () => context.go(Routes.member(s.member.id)),
                    );
                  },
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}
