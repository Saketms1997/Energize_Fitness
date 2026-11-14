# Status: what is verified and what is not

Earlier full run: **212 tests passed** (pytest, Windows 11, Python 3.13, PostgreSQL 17.5) plus a packaged-build smoke test.
Database tests run against real PostgreSQL (throwaway databases `gym_management_test` and `gym_management_test_b`);
sync tests use a mocked MongoDB (`mongomock`).

## Password recovery and MongoDB update (2026-11-12)

* **93 service regression tests passed** with a clean process exit: authentication/settings, recovery,
  MongoDB client lifecycle, and two-way sync. Tests cover administrator verification failures (including
  refusal of `trust` authentication), password strength, reset audit events, transaction rollback,
  unchanged other accounts, clearing lockout, and retaining queued data through a TLS failure.
* **10 targeted Qt tests passed** with a clean exit: login, password recovery, locked-user recovery,
  cancellation/busy-state handling, settings tabs, and testing unsaved MongoDB settings. Password
  recovery uses worker-thread execution and requires a fresh sign-in after resetting.
* The build's **44 database-free tests passed** (these overlap the service tests above).
* **Two read-only checks against the saved Atlas connection succeeded**, reusing the same client pool.
  The pool had no active users afterward and closed successfully. The reported intermittent
  `TLSV1_ALERT_INTERNAL_ERROR` was not reproduced during these checks; Atlas IP access/network
  configuration still needs checking if it recurs. See the setup guide.
* **The updated Windows executable was built and smoke-tested** against the throwaway PostgreSQL
  database: sign-in -> Forgot password? -> Reset owner password -> Cancel, then clean exit (code 0).
  The distributable ZIP includes the executable, Qt runtime, and certificate bundle.

**Validation limitation:** running the broader existing UI tests reported 24 passing assertions but
the Python process exited with Windows access violation `0xC0000005` during shutdown. This reproduced
with all new recovery/cloud-test cases excluded. A combined 119-test run likewise passed its assertions
but crashed at shutdown; it is not counted as a clean full-suite pass. The targeted checks and packaged
login/recovery smoke test above exited normally. Clean-PC installation remains unverified.

## Verified by automated tests against real PostgreSQL

| Area | Evidence |
|---|---|
| Schema and migrations | all 15 required tables + extras; Alembic 0001-0003 apply, downgrade and re-apply; app role is not superuser and cannot run DDL |
| Members, plans, memberships, renewals | registration, duplicates, validation, search/filters, Excel export, photo storage, expiry maths, renewal dates |
| Attendance | duplicate check-in blocked in code **and** by a database unique index; check-out; daily/monthly reports; trainers; auto-close of forgotten sessions |
| Payments | explicit owner confirmation; partial payments; overpayment blocked; refunds/voids; receipts (PDF); UPI QR (uploaded image and generated); atomic registration; every action audited |
| Dashboard, reports | all figures against known data; every report builds and exports to `.xlsx` and `.pdf` |
| Backup and restore | `.backup` and `.sql` created and validated; restore verified (data returns, safety copy kept, failed restore leaves the database unchanged); verify-by-restore; retention; schedule logic |
| Access `.mdb` export | real `.mdb` created with ACE 16, re-opened and checked (row counts, money totals), relationships enforced, Unicode and long text intact, credentials excluded |
| Two-way sync | two real databases sharing one mocked cloud: upload/download, idempotent repeat, edit/delete propagation, automatic merge of different fields, conflict detection and resolution (cloud / this PC / manual), payment conflicts flagged and never overwritten, cloud outage mid-sync loses nothing and recovers, per-record failure + retry, edits made during a sync are kept, duplicate member IDs from two offline PCs are renumbered |
| WhatsApp | request shape for the Cloud API, template configuration, queue, retries with backoff and attempt cap, reminders (idempotent), connection test - against a mocked HTTP layer |
| Security | Argon2id hashes, lockout, same message for unknown user and wrong password, password policy, audit entries never contain passwords, secrets never stored in the settings table, owners never synced |
| UI | every page loads on empty and populated data; registration wizard; verification gate; payment dialogs; login; settings tabs (offscreen Qt), plus screenshots of the real windows |
| Packaging | `build_exe.ps1` produces `dist\GymManagement\GymManagement.exe`; the packaged app starts and reaches the first-run screen |

## Verified against the real MongoDB Atlas cluster (MongoDB 8.0)

Connection test, then a two-device round trip in a temporary cloud database (dropped afterwards): upload, download on a
second device, automatic merge of edits to different fields, conflict detection on the same field, owner resolution, and
the resolved value reaching the other device. The automated tests still use `mongomock`; this live run was a one-off.
Not yet exercised: two physically separate PCs, and long-running automatic sync.

## NOT verified (needs your accounts or software)

| Item | Why | What to do |
|---|---|---|
| **WhatsApp Cloud API** | no Meta business account; HTTP calls are tested against a mock | follow the WhatsApp section of the setup guide; send yourself a test message first. Templates must be approved by Meta. Delivery receipts are not tracked |
| **Inno Setup installer** | Inno Setup 6 is not installed here, so `installer\GymManagement.iss` has never been compiled | install Inno Setup 6, run `scripts\build_installer.ps1` |
| **Clean-PC install** | the exe was only run on the development PC | test the installer on a PC without Python |
| **Real printed/viewed receipts, scheduled backups at the chosen time** | timers and PDF viewers were not exercised end to end | run a day with schedule on |
| **Idle auto-lock and background timers in a live session** | covered by logic tests, not by waiting 30 minutes | quick manual check |

## Known limits (by design)

* The live PostgreSQL data is not encrypted at rest (use BitLocker); backups are plain PostgreSQL dumps, so keep the backup folder private.
* Registration IDs are issued locally; two PCs working offline can pick the same ID - sync renumbers the later one and records it in the audit log.
* Receipt numbers are also issued locally; a collision between two offline PCs is reported as a failed record in Cloud Sync rather than renumbered (printed receipts must not change).
* The seeded plan prices (1000 / 2700 / 5000 / 9000) are placeholders. Set your own.
* Membership dates are inclusive: a 30-day plan starting on the 1st ends on the 30th.
* A full refund cancels the membership; a partial refund leaves it active and does not create a balance due.
