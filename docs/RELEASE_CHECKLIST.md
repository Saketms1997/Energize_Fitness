# Release checklist

Run through this before giving a build to a real gym.

## Automated (the build script does these and stops on failure)
- [ ] `flutter analyze` — no issues
- [ ] `flutter test` — all pass (Flutter unit + widget tests, including migrations, sync, backup)
- [ ] `node backend/tests/sync_core.test.mjs` — server SQL rules (needs `npm i @electric-sql/pglite` in `backend/tests`)
- [ ] `node --experimental-strip-types backend/tests/whatsapp_logic.test.mjs`
- [ ] `flutter build windows --release`

## Manual smoke test on a clean PC (about 15 minutes)
- [ ] Install from the installer; app starts; first-run setup; **recovery code saved**
- [ ] Register a member (with and without WhatsApp consent); duplicate phone is caught
- [ ] Record a UPI payment → QR shows (after setting UPI ID) → verify → membership turns Active; dashboard collection updates
- [ ] Attendance check-in; second tap says already checked in; unpaid member is stopped
- [ ] Freeze → unfreeze; extend; cancel
- [ ] Messages → Create reminders; Open WhatsApp opens WhatsApp with the text
- [ ] Back up now → file appears; **restore it on a second PC / second profile** and confirm members and payments are present
- [ ] Close the app mid-use (Task Manager) → reopen: nothing lost
- [ ] Install a newer build over the old one: data still there
- [ ] Uninstall: data folder untouched; reinstall: data back
- [ ] Idle 30 minutes: signed out. Wrong password 5 times: locked out for the lockout period

## Cloud (only if enabled)
- [ ] Connect → status shows connected; Sync now → "everything is uploaded"
- [ ] Turn Wi-Fi off, register a member, turn on → syncs on its own within 5 minutes or on *Sync now*
- [ ] Supabase table editor: `sync_records` has rows; **no** `gym_owners`, no password hashes
- [ ] In the Supabase SQL editor confirm RLS is on for `sync_records`, `sync_applied`, `gym_access`
- [ ] WhatsApp: templates approved; send one to your own number; delivered/read status comes back

## Before shipping to a customer
- [ ] Code-signing certificate applied (SmartScreen)
- [ ] BitLocker enabled on the gym PC; Windows user account has a password
- [ ] Backup folder set, daily backup ticked, **backup password stored somewhere other than that PC**
- [ ] Version bumped in `pubspec.yaml` (`version: x.y.z+build`)
