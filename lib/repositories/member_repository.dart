import 'package:drift/drift.dart';

import '../core/clock.dart';
import '../core/dates.dart';
import '../core/validators.dart';
import '../database/app_database.dart';
import '../models/membership_logic.dart';
import 'gym_repository.dart' show ValidationException;
import 'ids.dart';
import 'outbox.dart';
import 'support.dart';

class MemberInput {
  const MemberInput({
    required this.fullName,
    required this.phone,
    this.altPhone,
    this.dateOfBirth,
    this.ageYears,
    this.address = '',
    this.emergencyName,
    this.emergencyPhone,
    this.medicalNotes,
    this.photoPath,
    this.whatsappConsent = false,
  });

  final String fullName;
  final String phone;
  final String? altPhone;
  final DateTime? dateOfBirth;
  final int? ageYears;
  final String address;
  final String? emergencyName;
  final String? emergencyPhone;
  final String? medicalNotes;
  final String? photoPath;
  final bool whatsappConsent;

  Map<String, String> validate(DateTime today) {
    final errors = <String, String>{};
    final nameError = Validators.required(fullName, field: 'Full name');
    if (nameError != null) errors['fullName'] = nameError;
    if (fullName.trim().length > 120) errors['fullName'] = 'Name is too long';
    final phoneError = Validators.indianMobile(phone);
    if (phoneError != null) errors['phone'] = phoneError;
    final altError = Validators.indianMobile(altPhone, optional: true);
    if (altError != null) errors['altPhone'] = altError;
    final emError = Validators.indianMobile(emergencyPhone, optional: true);
    if (emError != null) errors['emergencyPhone'] = emError;
    final hasEmName = (emergencyName?.trim().isNotEmpty ?? false);
    final hasEmPhone = (emergencyPhone?.trim().isNotEmpty ?? false);
    if (hasEmName != hasEmPhone) {
      errors['emergencyName'] =
          'Provide both emergency contact name and number';
    }
    if (dateOfBirth == null && ageYears == null) {
      errors['dateOfBirth'] = 'Enter date of birth or age';
    }
    if (dateOfBirth != null) {
      final dob = GymDates.dateOnly(dateOfBirth!);
      if (dob.isAfter(today)) {
        errors['dateOfBirth'] = 'Date of birth cannot be in the future';
      } else if (today.year - dob.year > 120) {
        errors['dateOfBirth'] = 'Enter a valid date of birth';
      }
    }
    if (ageYears != null && (ageYears! < 1 || ageYears! > 120)) {
      errors['ageYears'] = 'Enter a valid age';
    }
    return errors;
  }
}

class DuplicateCandidate {
  const DuplicateCandidate(this.member, this.reason);
  final Member member;

  /// 'same_phone' | 'same_name_and_dob' | 'same_name'
  final String reason;
}

/// Likely duplicates found. The owner must either open the existing member or
/// repeat the call with a written override reason.
class DuplicateMemberException implements Exception {
  DuplicateMemberException(this.candidates);
  final List<DuplicateCandidate> candidates;
  @override
  String toString() => 'DuplicateMemberException(${candidates.length})';
}

class RegistrationResult {
  const RegistrationResult(this.member, this.membership);
  final Member member;
  final Membership membership;
}

class MemberSummary {
  const MemberSummary(this.member, this.membership, this.status);
  final Member member;
  final Membership? membership;

  /// active, frozen, pending_payment, expired, cancelled, none
  final String status;
}

class MemberDetail {
  const MemberDetail(this.member, this.memberships, this.payments);
  final Member member;
  final List<Membership> memberships;
  final List<Payment> payments;
}

class MemberRepository {
  MemberRepository(this._db, Clock clock)
    : _clock = clock,
      _outbox = OutboxWriter(_db, clock);

  final AppDatabase _db;
  final Clock _clock;
  final OutboxWriter _outbox;

  Future<String> _timezone(String gymId) async => (await (_db.select(
    _db.gyms,
  )..where((g) => g.id.equals(gymId))).getSingle()).timezone;

  // ---- reads --------------------------------------------------------------

  Stream<List<MemberSummary>> watchSummaries(
    String gymId, {
    String query = '',
    String? status,
  }) => watchQuery(_db, [
    _db.members,
    _db.memberships,
  ], () => summaries(gymId, query: query, status: status));

  Future<List<MemberSummary>> summaries(
    String gymId, {
    String query = '',
    String? status,
  }) async {
    final today = GymDates.today(_clock.now(), await _timezone(gymId));
    final members =
        await (_db.select(_db.members)
              ..where((m) => m.gymId.equals(gymId) & m.deletedAt.isNull())
              ..orderBy([(m) => OrderingTerm.asc(m.fullName)]))
            .get();
    final all = await (_db.select(
      _db.memberships,
    )..where((m) => m.gymId.equals(gymId) & m.deletedAt.isNull())).get();
    final byMember = <String, List<Membership>>{};
    for (final m in all) {
      byMember.putIfAbsent(m.memberId, () => []).add(m);
    }
    final q = query.trim().toLowerCase();
    // Ignore a leading +91 so "+91 99887..." matches stored 10-digit numbers.
    final digits = (q.startsWith('+91') ? q.substring(3) : q).replaceAll(
      RegExp(r'\D'),
      '',
    );
    final out = <MemberSummary>[];
    for (final m in members) {
      if (q.isNotEmpty) {
        final hit =
            m.fullName.toLowerCase().contains(q) ||
            m.memberCode.toLowerCase().contains(q) ||
            (digits.length >= 3 &&
                (m.phone.contains(digits) ||
                    (m.altPhone?.contains(digits) ?? false)));
        if (!hit) continue;
      }
      final ms = byMember[m.id] ?? const <Membership>[];
      final s = overallMemberStatus(ms, today);
      if (status != null && s != status) continue;
      out.add(MemberSummary(m, currentMembership(ms), s));
    }
    return out;
  }

  Stream<MemberDetail?> watchDetail(String memberId) => watchQuery(_db, [
    _db.members,
    _db.memberships,
    _db.payments,
  ], () => detail(memberId));

  Future<MemberDetail?> detail(String memberId) async {
    final member = await (_db.select(
      _db.members,
    )..where((m) => m.id.equals(memberId))).getSingleOrNull();
    if (member == null) return null;
    final ms =
        await (_db.select(_db.memberships)
              ..where((m) => m.memberId.equals(memberId))
              ..orderBy([(m) => OrderingTerm.desc(m.startDate)]))
            .get();
    final ps =
        await (_db.select(_db.payments)
              ..where((p) => p.memberId.equals(memberId))
              ..orderBy([(p) => OrderingTerm.desc(p.createdAt)]))
            .get();
    return MemberDetail(member, ms, ps);
  }

  Future<List<DuplicateCandidate>> findDuplicates(
    String gymId,
    MemberInput input, {
    String? excludeMemberId,
  }) async {
    final phone = Validators.normalizeIndianMobile(input.phone);
    final alt = Validators.normalizeIndianMobile(input.altPhone);
    final name = normalizeName(input.fullName);
    final rows = await (_db.select(
      _db.members,
    )..where((m) => m.gymId.equals(gymId) & m.deletedAt.isNull())).get();
    final out = <DuplicateCandidate>[];
    for (final m in rows) {
      if (m.id == excludeMemberId) continue;
      final phones = {m.phone, ?m.altPhone};
      if ((phone != null && phones.contains(phone)) ||
          (alt != null && phones.contains(alt))) {
        out.add(DuplicateCandidate(m, 'same_phone'));
      } else if (normalizeName(m.fullName) == name) {
        final sameDob =
            m.dateOfBirth != null &&
            input.dateOfBirth != null &&
            GymDates.dateOnly(m.dateOfBirth!) ==
                GymDates.dateOnly(input.dateOfBirth!);
        out.add(
          DuplicateCandidate(m, sameDob ? 'same_name_and_dob' : 'same_name'),
        );
      }
    }
    return out;
  }

  // ---- writes -------------------------------------------------------------

  /// Registers a member and creates their first membership as
  /// `pending_payment`. The membership only becomes active once a payment is
  /// verified (see PaymentRepository). Everything commits atomically with its
  /// outbox entries and audit trail.
  Future<RegistrationResult> registerMember({
    required String gymId,
    required String actorId,
    required MemberInput input,
    required String planId,
    required DateTime startDate,
    int? pricePaise,
    String? duplicateOverrideReason,
  }) async {
    final tz = await _timezone(gymId);
    final today = GymDates.today(_clock.now(), tz);
    final errors = input.validate(today);
    if (pricePaise != null && pricePaise < 0) {
      errors['pricePaise'] = 'Amount cannot be negative';
    }
    if (errors.isNotEmpty) throw ValidationException(errors);

    return _db.transaction(() async {
      final plan =
          await (_db.select(_db.membershipPlans)
                ..where((p) => p.id.equals(planId) & p.gymId.equals(gymId)))
              .getSingleOrNull();
      if (plan == null || plan.deletedAt != null || !plan.isActive) {
        throw DomainException('Selected plan is not available');
      }

      final dupes = await findDuplicates(gymId, input);
      final override = duplicateOverrideReason?.trim() ?? '';
      if (dupes.isNotEmpty && override.length < 5) {
        throw DuplicateMemberException(dupes);
      }

      final now = _clock.now();
      final memberId = newId();
      final membershipId = newId();
      final code = await _nextMemberCode(gymId);
      await _db
          .into(_db.members)
          .insert(
            MembersCompanion.insert(
              id: memberId,
              gymId: gymId,
              memberCode: code,
              fullName: input.fullName.trim(),
              phone: Validators.normalizeIndianMobile(input.phone)!,
              altPhone: Value(_phoneOrNull(input.altPhone)),
              dateOfBirth: Value(
                input.dateOfBirth == null
                    ? null
                    : GymDates.dateOnly(input.dateOfBirth!),
              ),
              ageYears: Value(input.ageYears),
              address: Value(input.address.trim()),
              emergencyName: Value(_trimOrNull(input.emergencyName)),
              emergencyPhone: Value(_phoneOrNull(input.emergencyPhone)),
              photoPath: Value(input.photoPath),
              medicalNotes: Value(_trimOrNull(input.medicalNotes)),
              whatsappConsent: Value(input.whatsappConsent),
              whatsappConsentAt: Value(input.whatsappConsent ? now : null),
              registeredAt: now,
              createdAt: now,
              updatedAt: now,
            ),
          );
      final start = GymDates.dateOnly(startDate);
      final price = pricePaise ?? plan.pricePaise;
      await _db
          .into(_db.memberships)
          .insert(
            MembershipsCompanion.insert(
              id: membershipId,
              gymId: gymId,
              memberId: memberId,
              planId: plan.id,
              planName: plan.name,
              pricePaise: price,
              startDate: start,
              endDate: GymDates.membershipEnd(
                start,
                plan.durationUnit,
                plan.durationValue,
              ),
              status: 'pending_payment',
              createdAt: now,
              updatedAt: now,
            ),
          );

      final member = await (_db.select(
        _db.members,
      )..where((m) => m.id.equals(memberId))).getSingle();
      final membership = await (_db.select(
        _db.memberships,
      )..where((m) => m.id.equals(membershipId))).getSingle();
      await _outbox.enqueue(
        gymId: gymId,
        entityTable: 'members',
        entityId: memberId,
        operation: 'insert',
        payload: member.toJson(),
      );
      await _outbox.enqueue(
        gymId: gymId,
        entityTable: 'memberships',
        entityId: membershipId,
        operation: 'insert',
        payload: membership.toJson(),
      );
      await _outbox.audit(
        gymId: gymId,
        actorId: actorId,
        action: 'member.registered',
        entityTable: 'members',
        entityId: memberId,
        details: {
          'memberCode': code,
          'planId': plan.id,
          'pricePaise': price,
          if (price != plan.pricePaise) 'planPricePaise': plan.pricePaise,
        },
      );
      if (dupes.isNotEmpty) {
        await _outbox.audit(
          gymId: gymId,
          actorId: actorId,
          action: 'member.duplicate_override',
          entityTable: 'members',
          entityId: memberId,
          reason: override,
          details: {
            'candidates': [
              for (final d in dupes) {'id': d.member.id, 'reason': d.reason},
            ],
          },
        );
      }
      return RegistrationResult(member, membership);
    });
  }

  /// Updates a member profile using optimistic concurrency: fails with
  /// [ConcurrentModificationException] if the stored version moved on.
  Future<Member> updateMember({
    required String memberId,
    required int expectedVersion,
    required String actorId,
    required MemberInput input,
    String? duplicateOverrideReason,
  }) async {
    return _db.transaction(() async {
      final existing = await (_db.select(
        _db.members,
      )..where((m) => m.id.equals(memberId))).getSingle();
      final today = GymDates.today(
        _clock.now(),
        await _timezone(existing.gymId),
      );
      final errors = input.validate(today);
      if (errors.isNotEmpty) throw ValidationException(errors);
      if (existing.version != expectedVersion) {
        throw ConcurrentModificationException('member', expectedVersion);
      }
      final newPhone = Validators.normalizeIndianMobile(input.phone)!;
      if (newPhone != existing.phone) {
        final dupes = (await findDuplicates(
          existing.gymId,
          input,
          excludeMemberId: memberId,
        )).where((d) => d.reason == 'same_phone').toList();
        final override = duplicateOverrideReason?.trim() ?? '';
        if (dupes.isNotEmpty && override.length < 5) {
          throw DuplicateMemberException(dupes);
        }
      }
      final now = _clock.now();
      final consentChanged = input.whatsappConsent != existing.whatsappConsent;
      await (_db.update(
        _db.members,
      )..where((m) => m.id.equals(memberId))).write(
        MembersCompanion(
          fullName: Value(input.fullName.trim()),
          phone: Value(newPhone),
          altPhone: Value(_phoneOrNull(input.altPhone)),
          dateOfBirth: Value(
            input.dateOfBirth == null
                ? null
                : GymDates.dateOnly(input.dateOfBirth!),
          ),
          ageYears: Value(input.ageYears),
          address: Value(input.address.trim()),
          emergencyName: Value(_trimOrNull(input.emergencyName)),
          emergencyPhone: Value(_phoneOrNull(input.emergencyPhone)),
          photoPath: Value(input.photoPath ?? existing.photoPath),
          medicalNotes: Value(_trimOrNull(input.medicalNotes)),
          whatsappConsent: Value(input.whatsappConsent),
          whatsappConsentAt: consentChanged
              ? Value(input.whatsappConsent ? now : existing.whatsappConsentAt)
              : const Value.absent(),
          version: Value(existing.version + 1),
          updatedAt: Value(now),
        ),
      );
      final row = await (_db.select(
        _db.members,
      )..where((m) => m.id.equals(memberId))).getSingle();
      await _outbox.enqueue(
        gymId: existing.gymId,
        entityTable: 'members',
        entityId: memberId,
        operation: 'update',
        baseVersion: existing.version,
        payload: row.toJson(),
      );
      await _outbox.audit(
        gymId: existing.gymId,
        actorId: actorId,
        action: 'member.updated',
        entityTable: 'members',
        entityId: memberId,
        details: {
          'phoneChanged': newPhone != existing.phone,
          'consentChanged': consentChanged,
        },
      );
      return row;
    });
  }

  /// Returns medical notes and records who looked. The UI must re-confirm the
  /// owner's password before calling this.
  Future<String?> revealMedicalNotes({
    required String memberId,
    required String actorId,
  }) async {
    final m = await (_db.select(
      _db.members,
    )..where((x) => x.id.equals(memberId))).getSingle();
    await _outbox.audit(
      gymId: m.gymId,
      actorId: actorId,
      action: 'member.medical_notes_viewed',
      entityTable: 'members',
      entityId: memberId,
    );
    return m.medicalNotes;
  }

  Future<String> _nextMemberCode(String gymId) async {
    final codes =
        await (_db.selectOnly(_db.members)
              ..addColumns([_db.members.memberCode])
              ..where(_db.members.gymId.equals(gymId)))
            .map((r) => r.read(_db.members.memberCode)!)
            .get();
    var max = 0;
    for (final c in codes) {
      final n = int.tryParse(c.replaceAll(RegExp(r'\D'), ''));
      if (n != null && n > max) max = n;
    }
    return 'M${(max + 1).toString().padLeft(6, '0')}';
  }

  static String? _trimOrNull(String? v) {
    final t = v?.trim();
    return (t == null || t.isEmpty) ? null : t;
  }

  static String? _phoneOrNull(String? v) => (v == null || v.trim().isEmpty)
      ? null
      : Validators.normalizeIndianMobile(v);
}
