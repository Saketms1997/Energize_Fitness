import 'package:drift/drift.dart';

import '../database/app_database.dart';

/// Another device/session changed the record since it was loaded.
class ConcurrentModificationException implements Exception {
  ConcurrentModificationException(this.entity, this.expectedVersion);
  final String entity;
  final int expectedVersion;
  @override
  String toString() =>
      'ConcurrentModificationException($entity changed since version $expectedVersion)';
}

/// A business rule was violated (not a field-validation problem).
class DomainException implements Exception {
  DomainException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// Emits [load] now and again whenever any of [tables] change.
Stream<T> watchQuery<T>(
  AppDatabase db,
  List<ResultSetImplementation> tables,
  Future<T> Function() load,
) async* {
  yield await load();
  await for (final _ in db.tableUpdates(TableUpdateQuery.onAllTables(tables))) {
    yield await load();
  }
}
