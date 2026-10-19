# Implementation status

Last updated: 2026-10-19. All six phases are implemented. This page says what is **proven by automated tests**, what was **built but not exercised against the real outside service**, and what is **deliberately not done**.

## Toolchain (this machine)
- Flutter 3.47.7 / Dart 3.13.5 at `D:\tools\flutter` (add `D:\tools\flutter\bin` to PATH). Git, Node 22, Visual Studio 2022.

## Verification summary

| Check | Result |
|---|---|
| `flutter analyze` | no issues |
| `flutter test` | **157 tests pass**, stable over repeated runs |
| Server SQL (`backend/tests/sync_core.test.mjs`) | passes on a real PostgreSQL engine (PGlite): idempotency, conflicts, RLS, cross-gym blocking, write lockdown, WhatsApp status rules |
| WhatsApp edge-function logic (`backend/tests/whatsapp_logic.test.mjs`) | passes (HMAC signature, request validation, status parsing) |
| `flutter build windows --debug` and `--release` | build OK |

## What each phase delivered

### Phase 1 — Foundation
Drift/SQLite schema with UUID keys, versions, tombstones; first-run owner setup (single owner); PBKDF2 login with lockout, one-time recovery code, 30-minute idle expiry; every write = row + sync outbox entry + audit log in one transaction; money in integer paise.

### Phase 2 — Members, plans, payments, dashboard
Registration with duplicate detection (audited override), plans, renew / freeze / extend / cancel, payment lifecycle (pending → verified/rejected/failed → refunded, replay-safe, duplicate references blocked), password-gated medical notes, dashboard counting only verified money. UI screens and dialogs complete; widget tests cover registration → payment → verification, duplicate dialog, plans.

### Phase 3 — UPI + WhatsApp
- UPI deep link + on-screen QR in the payment dialog (needs the gym UPI ID in Settings).
- Five message templates (single source of truth for preview, wa.me text and Cloud API template), consent required, per-event de-duplication, reminder generation (expiring ≤3 days, expired ≤7 days unless renewed, balance due after 1 day), receipts, welcome.
- Messages screen: click-to-chat fallback with "Mark sent"; automatic sending through the `send-whatsapp` Edge Function when connected; retry with attempt limit; delivery status written back by `whatsapp-webhook`.

### Phase 4 — Trainers and attendance
Trainers (add/edit/deactivate, assign to members), attendance check-in by member ID or phone, one per gym-local day (IST midnight rollover tested), blocked for unpaid/frozen/expired/cancelled with an audited written override, same-day undo.

### Phase 5 — Cloud sync (Supabase)
Outbox push in true insertion order, idempotent retries, pull with a persisted cursor, server-wins conflict rule that audits what the loser lost, one bad operation cannot block the rest, secrets (password hashes) never leave the device, cloud sign-in with token refresh and tokens kept in the OS credential store, 5-minute background sync, Settings screen card.

### Phase 6 — Backup, installer, release
AES-256-GCM encrypted backups (PBKDF2, authenticated header, integrity-checked, atomic write, retention), daily auto-backup, staged restore applied at next start with the old database kept, Inno Setup script, build scripts for Windows/Android, signing setup, Android INTERNET permission fix, setup/user/release docs.

## Bugs found by the tests while building this (all fixed)
- v1 → v3 migration added `members.trainer_id` twice.
- Sync ordered outbox rows by one-second timestamps with a random tie-break, so a v2 edit could reach the server before its v1 insert (spurious conflicts). Now ordered by insertion rowid; regression test added.
- 401/403 responses wrongly triggered one-by-one retries.
- Release Android manifest lacked the INTERNET permission (sync/WhatsApp would silently fail in a release APK).
- Methods that threw before returning a `Future` (trainer/attendance), which made error handling inconsistent.

## NOT verified against the real thing
These were built to the documented API and tested against faithful fakes, but never run against the live service. Do the manual steps in `docs/RELEASE_CHECKLIST.md`.

| Item | Why |
|---|---|
| Real Supabase project (auth, REST paths, deployed functions) | no project/account available here; the SQL is verified on PostgreSQL, the HTTP calls against a hand-written fake of the Supabase endpoints |
| WhatsApp Cloud API sending and webhook | no Meta business account; function code untested on Deno |
| Inno Setup installer | Inno Setup not installed here; script written but never compiled |
| Launching the release `.exe` | not started on this machine, to avoid creating data in the real app-data folder; run the smoke test |
| Android release APK | see "Android" below |
| iOS | needs a Mac with Xcode and an Apple Developer account |
| macOS/Linux | not targeted |

### Android
A release APK build (`flutter build apk --release`) was started on this machine but had not finished when this page was written, and the earlier-known blockers (Android command-line tools missing, SDK licences not accepted) were not resolved. Treat Android as **unverified** until `scripts\build_android.ps1` succeeds and the APK is installed on a phone. The code paths are shared with Windows; the Android-specific changes made are the INTERNET permission, the https `queries` entry for opening WhatsApp links, the app label, and release-signing config (`android/key.properties`).

## Deliberately not implemented
- SQLCipher encryption of the live database file (backups are encrypted; use BitLocker for the live file).
- Biometric unlock.
- A new device joining an existing cloud gym (owner credentials are intentionally not synced). Moving PCs = restore a backup.
- Cloud sync of `payment_events` and audit logs (they are in backups).
- Member photos / logo upload UI (columns exist).
- Full time-zone database (Asia/Kolkata only; other zones fall back to UTC).
