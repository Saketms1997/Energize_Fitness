# Setup guide

Everything here is optional except step 1. The app works fully offline on one PC; cloud sync and automatic WhatsApp are add-ons.

## 1. Build and install the app (Windows)

Prerequisites on the build PC:

| Need | How |
|---|---|
| Flutter 3.47 / Dart 3.13 | `D:\tools\flutter\bin` on `PATH` |
| Visual Studio 2022 with **Desktop development with C++** (MSVC, CMake, Windows 10/11 SDK) | VS Installer → Modify |
| Windows **Developer Mode** (plugins use symlinks) | `start ms-settings:developers` |
| Inno Setup 6 (only for the installer) | https://jrsoftware.org/isinfo.php |

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

This runs analyze + all tests, builds the release, and (if Inno Setup is found) writes `build\installer\EnergizeFitness-Setup-<version>.exe`. Without Inno Setup you still get the runnable app in `build\windows\x64\runner\Release\`.

**Where data lives:** `%APPDATA%\...\gym_management.sqlite` (per-user app-data folder). The installer and uninstaller never touch it.

**Code signing:** unsigned installers trigger a Windows SmartScreen warning. Buy a code-signing certificate and uncomment `SignTool=` in `installer\energize_fitness.iss`.

## 2. First run

1. Open the app → *Set up your gym*: gym name, owner name, email, password (10+ chars, letters and numbers).
2. **Write down the recovery code** shown once. It is the only way back in if the password is forgotten.
3. Settings → fill in the gym's **UPI ID** (enables the payment QR) and contact details.
4. Plans → create your membership plans.

## 3. Backups (do this on day one)

Settings → **Backup & restore**: choose a folder (USB drive or a OneDrive/Google Drive folder), set a backup password, tick *Back up automatically every day*, press **Back up now**.

* Backups are AES-256-GCM encrypted. **If the password is lost the backup cannot be opened by anyone.**
* The newest 14 are kept.
* To restore: Settings → Restore → pick the `.efbak` file and password → *Verify and prepare restore* → close and reopen the app. The old data is kept next to the database as `*.before-restore-<time>`.
* Enable **BitLocker** on the PC. The live database file itself is not encrypted (see "Known limits").

## 4. Cloud sync (optional): Supabase

Gives off-site backup of live data and lets a second device see the same gym.

1. Create a project at https://supabase.com (free tier is fine to start).
2. **SQL Editor** → paste and run `backend/migrations/0001_sync_core.sql`. (Tested against PostgreSQL by `backend/tests/sync_core.test.mjs`.)
3. **Authentication → Providers → Email**: keep enabled. Either disable *Confirm email* (simplest, single owner) or confirm the email once.
4. **Project Settings → API**: copy the *Project URL* and the *anon public* key. (The `service_role` key must never go in the app.)
5. In the app: Settings → *Cloud sync* → paste URL, anon key, choose a cloud email and password, tick *Create this cloud account* the first time → **Connect**.

The cloud password is separate from the owner password and is never stored by the app; only a refresh token is kept in the Windows Credential Locker.

Optional: pre-fill those two public values in release builds:
```powershell
$env:SUPABASE_URL='https://xxxx.supabase.co'; $env:SUPABASE_ANON_KEY='...'
powershell -File scripts\build_windows.ps1
```

**Conflict rule:** if two devices edit the same record, the version that reached the server first wins; the loser's edit is written to the audit log (`sync.conflict_server_wins`) so nothing is silently lost.

## 5. WhatsApp

### Level 1 — no account needed (works today)
Messages → **Create reminders** queues expiry / expired / balance-due reminders for members who agreed to WhatsApp. Press **Open WhatsApp** on a message (opens WhatsApp Desktop/Web with the text filled in), press send, then **Mark sent**.

### Level 2 — automatic sending (WhatsApp Cloud API)
Needs a Meta Business account and a WhatsApp Business phone number: https://developers.facebook.com/docs/whatsapp/cloud-api/get-started

1. Register the five message templates exactly as listed in `docs/WHATSAPP_TEMPLATES.md` (category **Utility**, language **English**) and wait for approval. WhatsApp only allows approved templates for business-initiated messages.
2. Install the Supabase CLI and deploy the functions:
   ```powershell
   supabase login
   supabase init                       # once, in an empty folder
   supabase link --project-ref <ref>
   # copy backend\functions\* into supabase\functions\ (keep the _shared folder)
   supabase functions deploy send-whatsapp
   supabase functions deploy whatsapp-webhook --no-verify-jwt
   supabase secrets set WHATSAPP_ACCESS_TOKEN=<permanent system-user token>
   supabase secrets set WHATSAPP_PHONE_NUMBER_ID=<phone number id>
   supabase secrets set WHATSAPP_APP_SECRET=<app secret>
   supabase secrets set WHATSAPP_WEBHOOK_VERIFY_TOKEN=<any long random string>
   ```
3. In Meta's dashboard → WhatsApp → Configuration: callback URL `https://<ref>.supabase.co/functions/v1/whatsapp-webhook`, verify token = the string above, subscribe to **messages**.
4. In the app: Settings → Cloud sync → turn on **Send WhatsApp messages automatically**. Messages → **Send all queued**.

The WhatsApp token exists only as a Supabase secret; the app sends its own signed-in session token to the function, which checks the caller owns the gym.

## Known limits

* The live SQLite file is not encrypted at rest (no SQLCipher). Mitigate with BitLocker/device encryption; backups *are* encrypted.
* No biometric unlock. Sessions are memory-only and expire after 30 idle minutes.
* A **new device** cannot yet "join" an existing cloud gym (owner credentials are deliberately not synced). To move to a new PC, restore a backup there.
* `payment_events` and audit logs stay on the device (covered by backups, not by cloud sync).
* Two devices registering members at the same moment can generate the same member code; the second device's copy is kept in the audit log rather than overwriting (`sync.apply_failed`).
