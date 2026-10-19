import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../core/dates.dart';

final _dateFmt = DateFormat('d MMM yyyy');
final _dateTimeFmt = DateFormat('d MMM yyyy, h:mm a');

/// Formats a stored date-only value (see [GymDates.dateOnly]).
String formatDate(DateTime d) => _dateFmt.format(GymDates.dateOnly(d));

String formatDateTime(DateTime utc) => _dateTimeFmt.format(utc.toLocal());

/// Converts a date-picker result into the UTC-midnight date-only convention.
DateTime pickedToDateOnly(DateTime picked) =>
    DateTime.utc(picked.year, picked.month, picked.day);

/// Status pill. Always carries a text label, so meaning never depends on
/// colour alone.
class StatusChip extends StatelessWidget {
  const StatusChip(this.status, {super.key});
  final String status;

  static const _labels = {
    'active': 'Active',
    'frozen': 'Frozen',
    'pending_payment': 'Awaiting payment',
    'expired': 'Expired',
    'cancelled': 'Cancelled',
    'none': 'No plan',
    'pending': 'Pending',
    'verified': 'Verified',
    'failed': 'Failed',
    'rejected': 'Rejected',
    'refunded': 'Refunded',
    'queued': 'Queued',
    'sent': 'Sent',
    'delivered': 'Delivered',
    'read': 'Read',
  };

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final (bg, fg) = switch (status) {
      'active' ||
      'verified' ||
      'delivered' ||
      'read' => (cs.primaryContainer, cs.onPrimaryContainer),
      'frozen' => (cs.tertiaryContainer, cs.onTertiaryContainer),
      'pending_payment' ||
      'pending' => (cs.secondaryContainer, cs.onSecondaryContainer),
      'expired' ||
      'failed' ||
      'rejected' => (cs.errorContainer, cs.onErrorContainer),
      _ => (cs.surfaceContainerHighest, cs.onSurfaceVariant),
    };
    return Chip(
      label: Text(_labels[status] ?? status),
      backgroundColor: bg,
      labelStyle: TextStyle(color: fg, fontSize: 12),
      visualDensity: VisualDensity.compact,
      side: BorderSide.none,
      padding: EdgeInsets.zero,
    );
  }
}
