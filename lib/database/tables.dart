// Drift's documented `.check(column...)` pattern reads the column being
// declared; the analyzer mistakes that for infinite recursion.
// ignore_for_file: recursive_getters

import 'package:drift/drift.dart';

/// Shared sync columns: stable UUID id, optimistic-concurrency version and
/// tombstone. Every synchronised table carries these.
mixin SyncColumns on Table {
  TextColumn get id => text()();
  IntColumn get version => integer().withDefault(const Constant(1))();
  DateTimeColumn get createdAt => dateTime()();
  DateTimeColumn get updatedAt => dateTime()();
  DateTimeColumn get deletedAt => dateTime().nullable()();

  @override
  Set<Column> get primaryKey => {id};
}

class Gyms extends Table with SyncColumns {
  TextColumn get name => text().withLength(min: 1, max: 120)();
  TextColumn get logoPath => text().nullable()();
  TextColumn get address => text().withDefault(const Constant(''))();
  TextColumn get contactNumber => text().withDefault(const Constant(''))();
  TextColumn get timezone =>
      text().withDefault(const Constant('Asia/Kolkata'))();
  TextColumn get currency => text().withDefault(const Constant('INR'))();
  TextColumn get upiId => text().nullable()();
  TextColumn get upiPayeeName => text().nullable()();
}

class GymOwners extends Table with SyncColumns {
  TextColumn get gymId => text().references(Gyms, #id)();
  TextColumn get displayName => text().withLength(min: 1, max: 120)();
  TextColumn get email => text()();
  TextColumn get passwordHash => text()();
  TextColumn get recoveryHash => text()();
  IntColumn get failedAttempts => integer().withDefault(const Constant(0))();
  DateTimeColumn get lockedUntil => dateTime().nullable()();

  @override
  List<Set<Column>> get uniqueKeys => [
    {email},
  ];
}

class AppDevices extends Table with SyncColumns {
  TextColumn get gymId => text().references(Gyms, #id)();
  TextColumn get deviceName => text()();
  TextColumn get platform => text()();
  DateTimeColumn get lastSeenAt => dateTime().nullable()();
  DateTimeColumn get revokedAt => dateTime().nullable()();
}

class AppSettings extends Table {
  TextColumn get gymId => text().references(Gyms, #id)();
  TextColumn get key => text()();
  TextColumn get value => text()();
  DateTimeColumn get updatedAt => dateTime()();

  @override
  Set<Column> get primaryKey => {gymId, key};
}

/// Local outbox. A row is only marked synced once the backend confirms.
class SyncOperations extends Table {
  TextColumn get id => text()();
  TextColumn get gymId => text()();
  TextColumn get entityTable => text()();
  TextColumn get entityId => text()();

  /// 'insert' | 'update' | 'delete'
  TextColumn get operation => text()();
  TextColumn get payloadJson => text()();
  IntColumn get baseVersion => integer().withDefault(const Constant(0))();
  TextColumn get idempotencyKey => text()();

  /// 'pending' | 'in_flight' | 'synced' | 'conflict' | 'failed'
  TextColumn get status => text().withDefault(const Constant('pending'))();
  IntColumn get attemptCount => integer().withDefault(const Constant(0))();
  TextColumn get lastError => text().nullable()();
  DateTimeColumn get createdAt => dateTime()();
  DateTimeColumn get syncedAt => dateTime().nullable()();

  @override
  Set<Column> get primaryKey => {id};

  @override
  List<Set<Column>> get uniqueKeys => [
    {idempotencyKey},
  ];
}

class MembershipPlans extends Table with SyncColumns {
  TextColumn get gymId => text().references(Gyms, #id)();
  TextColumn get name => text().withLength(min: 1, max: 80)();
  TextColumn get durationUnit =>
      text().check(durationUnit.isIn(['days', 'months']))();
  IntColumn get durationValue =>
      integer().check(durationValue.isBiggerThanValue(0))();
  IntColumn get pricePaise =>
      integer().check(pricePaise.isBiggerOrEqualValue(0))();
  BoolColumn get isActive => boolean().withDefault(const Constant(true))();

  @override
  List<Set<Column>> get uniqueKeys => [
    {gymId, name},
  ];
}

class Members extends Table with SyncColumns {
  TextColumn get gymId => text().references(Gyms, #id)();
  TextColumn get memberCode => text()();
  TextColumn get fullName => text().withLength(min: 1, max: 120)();

  /// Normalised 10-digit Indian mobile number.
  TextColumn get phone => text()();
  TextColumn get altPhone => text().nullable()();
  DateTimeColumn get dateOfBirth => dateTime().nullable()();
  IntColumn get ageYears => integer().nullable()();
  TextColumn get address => text().withDefault(const Constant(''))();
  TextColumn get emergencyName => text().nullable()();
  TextColumn get emergencyPhone => text().nullable()();
  TextColumn get photoPath => text().nullable()();

  /// Sensitive: only shown after the owner re-enters their password.
  TextColumn get medicalNotes => text().nullable()();
  BoolColumn get whatsappConsent =>
      boolean().withDefault(const Constant(false))();
  DateTimeColumn get whatsappConsentAt => dateTime().nullable()();
  DateTimeColumn get registeredAt => dateTime()();

  /// Assigned trainer (added in schema v3).
  TextColumn get trainerId => text().nullable()();

  @override
  List<Set<Column>> get uniqueKeys => [
    {gymId, memberCode},
  ];
}

class Memberships extends Table with SyncColumns {
  TextColumn get gymId => text().references(Gyms, #id)();
  TextColumn get memberId => text().references(Members, #id)();
  TextColumn get planId => text().references(MembershipPlans, #id)();

  /// Plan name at purchase time, so later plan edits don't rewrite history.
  TextColumn get planName => text()();
  IntColumn get pricePaise =>
      integer().check(pricePaise.isBiggerOrEqualValue(0))();
  DateTimeColumn get startDate => dateTime()();
  DateTimeColumn get endDate => dateTime()();
  TextColumn get status => text().check(
    status.isIn([
      'pending_payment',
      'active',
      'frozen',
      'expired',
      'cancelled',
    ]),
  )();
  TextColumn get statusReason => text().nullable()();

  /// Set on renewals to chain subscription history.
  TextColumn get previousMembershipId => text().nullable()();
}

class Payments extends Table with SyncColumns {
  TextColumn get gymId => text().references(Gyms, #id)();
  TextColumn get membershipId => text().references(Memberships, #id)();
  TextColumn get memberId => text().references(Members, #id)();
  IntColumn get amountPaise =>
      integer().check(amountPaise.isBiggerThanValue(0))();
  TextColumn get method =>
      text().check(method.isIn(['upi', 'cash', 'other']))();
  TextColumn get status => text().check(
    status.isIn(['pending', 'verified', 'failed', 'rejected', 'refunded']),
  )();

  /// Upper-cased UPI/transaction reference. Unique per gym when present.
  TextColumn get reference => text().nullable()();
  TextColumn get idempotencyKey => text()();
  DateTimeColumn get verifiedAt => dateTime().nullable()();
  TextColumn get verifiedBy => text().nullable()();
  TextColumn get note => text().nullable()();

  @override
  List<Set<Column>> get uniqueKeys => [
    {idempotencyKey},
    {gymId, reference},
  ];
}

/// Immutable payment audit trail: one row per state transition.
class PaymentEvents extends Table {
  TextColumn get id => text()();
  TextColumn get gymId => text()();
  TextColumn get paymentId => text().references(Payments, #id)();
  TextColumn get eventType => text()();
  TextColumn get fromStatus => text().nullable()();
  TextColumn get toStatus => text()();
  TextColumn get actorId => text().nullable()();
  TextColumn get detailsJson => text().withDefault(const Constant('{}'))();
  DateTimeColumn get createdAt => dateTime()();

  @override
  Set<Column> get primaryKey => {id};
}

class MembershipFreezes extends Table with SyncColumns {
  TextColumn get gymId => text().references(Gyms, #id)();
  TextColumn get membershipId => text().references(Memberships, #id)();
  DateTimeColumn get startDate => dateTime()();
  DateTimeColumn get endDate => dateTime().nullable()();
  IntColumn get daysFrozen => integer().nullable()();
  TextColumn get reason => text()();
}

/// Append-only audit trail. Never updated or deleted by application code.
class AuditLogs extends Table {
  TextColumn get id => text()();
  TextColumn get gymId => text()();
  TextColumn get actorId => text().nullable()();
  TextColumn get action => text()();
  TextColumn get entityTable => text().nullable()();
  TextColumn get entityId => text().nullable()();
  TextColumn get reason => text().nullable()();
  TextColumn get detailsJson => text().withDefault(const Constant('{}'))();
  DateTimeColumn get createdAt => dateTime()();

  @override
  Set<Column> get primaryKey => {id};
}

/// A message to a member. Created only for members who gave WhatsApp consent.
/// [dedupeKey] makes automatic reminders idempotent (one per event).
class WhatsappMessages extends Table with SyncColumns {
  TextColumn get gymId => text().references(Gyms, #id)();
  TextColumn get memberId => text().references(Members, #id)();

  /// E.164 digits without '+', e.g. 919876543210.
  TextColumn get toPhone => text()();
  TextColumn get templateKey => text()();
  TextColumn get bodyText => text()();

  /// JSON array of template parameters, in order ({{1}}, {{2}}...). Needed for
  /// WhatsApp Cloud API template sends; [bodyText] is the rendered result.
  TextColumn get paramsJson => text().withDefault(const Constant('[]'))();

  /// 'queued' | 'sent' | 'delivered' | 'read' | 'failed' | 'cancelled'
  TextColumn get status => text().check(
    status.isIn(['queued', 'sent', 'delivered', 'read', 'failed', 'cancelled']),
  )();
  TextColumn get dedupeKey => text()();
  TextColumn get providerMessageId => text().nullable()();
  TextColumn get errorMessage => text().nullable()();
  IntColumn get attemptCount => integer().withDefault(const Constant(0))();
  DateTimeColumn get sentAt => dateTime().nullable()();

  @override
  List<Set<Column>> get uniqueKeys => [
    {gymId, dedupeKey},
  ];
}

class Trainers extends Table with SyncColumns {
  TextColumn get gymId => text().references(Gyms, #id)();
  TextColumn get name => text().withLength(min: 1, max: 120)();
  TextColumn get phone => text().nullable()();
  TextColumn get specialty => text().nullable()();
  BoolColumn get isActive => boolean().withDefault(const Constant(true))();
}

/// One check-in per member per gym-local day.
class AttendanceRecords extends Table with SyncColumns {
  TextColumn get gymId => text().references(Gyms, #id)();
  TextColumn get memberId => text().references(Members, #id)();
  TextColumn get membershipId => text().nullable()();

  /// Gym-local calendar day (date-only convention, see GymDates.dateOnly).
  DateTimeColumn get attendanceDate => dateTime()();
  DateTimeColumn get checkedInAt => dateTime()();
  TextColumn get method => text().check(method.isIn(['manual', 'code']))();
  TextColumn get checkedInBy => text().nullable()();

  @override
  List<Set<Column>> get uniqueKeys => [
    {memberId, attendanceDate},
  ];
}
