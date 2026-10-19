import 'package:uuid/uuid.dart';

const _uuid = Uuid();

/// Stable client-generated UUID v4 used as primary key and idempotency key.
String newId() => _uuid.v4();
