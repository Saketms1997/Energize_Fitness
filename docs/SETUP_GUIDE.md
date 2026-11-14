# Setup guide

## 1. Prerequisites

| Need | Version | Notes |
|---|---|---|
| Windows | 10 / 11, 64-bit | |
| Python | 3.12 or newer | only to run from source or to build; the installed app bundles its own |
| PostgreSQL | 15 or newer (tested on 17) | installs **pgAdmin 4** too; remember the `postgres` password |
| Microsoft Access Database Engine (x64) | any recent | only for the `.mdb` export - see section 8 |
| Inno Setup 6 | 6.x | only to build `Setup.exe` |

### Install PostgreSQL and pgAdmin 4
1. Download the Windows installer from <https://www.postgresql.org/download/windows/> (EnterpriseDB).
2. Keep **PostgreSQL Server** and **pgAdmin 4** ticked; set a password for the `postgres` user; keep port **5432**.
3. After installing, the *PostgreSQL* Windows service starts automatically.

## 2. Run from source

```powershell
cd <project folder>
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe main.py
```

## 3. Create the database (first run)

The app does this for you: its **setup wizard** asks for the `postgres` password, then creates

* `gym_migrator` - owns the database; runs schema upgrades only;
* `gym_app` - least privilege (read/write rows, **no schema changes**); what the running app uses;
* database `gym_management`, then applies the Alembic migrations.

Their random passwords are stored in **Windows Credential Manager** (service `GymManagement`), never in files.
Command-line equivalent: `.\.venv\Scripts\python.exe scripts\setup_database.py`.

Use pgAdmin 4 to look at the data: *Register > Server*, host `localhost`, port `5432`, database `gym_management`.
(To read the app's data with pgAdmin, connect as `postgres`, or as `gym_app` after reading its password from Credential Manager.)

## 4. First start

1. Create the owner account (gym name, your name, username, password of 8+ characters with letters and numbers).
2. **Settings > Gym profile**: name, address, phone, logo, member-ID prefix.
3. **Settings > Membership plans**: the app creates Monthly / Quarterly / Half-yearly / Yearly with **placeholder
   prices - set your real prices**. Custom durations are supported.
4. **Settings > Payments & UPI**: upload your UPI QR image and/or enter your UPI ID and payee name.

The app locks itself after 30 minutes of inactivity (or press **Lock**). Five wrong passwords lock the account for 5 minutes.

### Forgotten owner password

On the sign-in or lock screen, choose **Forgot password?**. Enter the owner username, the PostgreSQL
`postgres` administrator password, and your new login password twice. The administrator password is
verified against the configured database server, is never saved, and is not the forgotten gym password.
PostgreSQL must actually use password authentication (prefer `scram-sha-256` in `pg_hba.conf`);
`trust` or Windows-only authentication cannot be used to approve a reset.

A successful reset clears the account's failed attempts and temporary lock, records an audit event,
and returns to sign-in. Gym records and other accounts are unchanged. Inactive accounts are not
reactivated. If you also forgot the PostgreSQL administrator password, contact your database administrator.

## 5. Registering a member (UPI workflow)

*Members > Register member*: details → plan and amount (editable for a discount) → QR screen.
The QR is only a display. The membership activates **only** after you tick *"I have checked my bank / UPI app and
confirm the amount has been received"* and press **Payment received - register**. Otherwise choose **Register -
payment pending**; collect later from *Memberships* or *Payments*. A receipt PDF opens after a confirmed payment.

## 6. MongoDB Atlas (optional cloud sync)

1. Create a free cluster at <https://www.mongodb.com/cloud/atlas>.
2. *Database Access*: add a user with read/write on one database. *Network Access*: allow this PC's IP.
3. *Connect > Drivers*: copy the `mongodb+srv://...` string.
4. In the app: **Settings > Cloud (MongoDB)** → paste it (it goes to Credential Manager, not to any file), set the
   database name, **Test connection**, Save. The test uses the values currently entered without saving them.
   Optionally set automatic sync every N minutes.
5. **Cloud Sync > Sync now**.

How it behaves: every change is queued in the same transaction that saves it; sync uploads the queue and downloads
only newer cloud changes. If the same field was changed in both places you get a **conflict** to resolve (nothing is
overwritten). Several PCs can share one cloud database. Machine-specific settings (backup folder, etc.) and owner
passwords are never synced.

If two PCs created the same membership plan name or gym setting before syncing (for example, the default plans each
new installation creates), sync keeps the **oldest** record on every PC. A duplicate plan is retired and its
subscriptions move to the kept plan; a duplicate setting's value is discarded. Both are recorded in the audit log.
Check plan prices and gym settings after connecting a new PC.

The app reuses one thread-safe MongoDB client pool for connection tests and syncs, retires it safely
when the connection string changes, and closes it on exit. If a connection test or sync fails because the
cloud is unreachable (including a TLS handshake rejection), the app discards that connection pool and
retries once with a fresh client after a short pause. Failed syncs leave local changes queued for the next sync.

If `SSL handshake failed` / `TLSV1_ALERT_INTERNAL_ERROR` appears after previously working:

1. In Atlas **Network Access**, allow this PC's **current public IP**. Router reconnects, a different
   network, or a VPN can change it; temporary allowlist entries can also expire. Prefer a stable,
   narrowly scoped IP entry rather than allowing all IPs.
2. Check that the Atlas cluster is available and your network allows its MongoDB endpoints. Ask your
   administrator to check firewall, antivirus, VPN, and TLS-inspection policies.
3. Check the PC's date/time. Certificate-verification errors also require checking the CA bundle or
   an inspecting proxy's trust configuration.
4. Run **Test connection**, then **Sync now** again. Do not disable TLS, hostname, or certificate checks.

The automatic fresh-client retry recovers from stale connections after sleep or a network change. If Atlas
keeps rejecting this PC's IP, retrying cannot fix it; update Network Access as above.
See [Atlas connection troubleshooting](https://www.mongodb.com/docs/atlas/troubleshoot-connection/).
When diagnosing repeated reconnects, monitor Atlas connection counts alongside local sync-history errors;
enable debug logging to see when the app creates a pool (no credentials are logged).

## 7. WhatsApp Cloud API (optional)

1. In Meta Business Manager create a WhatsApp Business account and phone number; note the **Phone number ID**.
2. Create a permanent **access token** (System User).
3. Create and get approved the message templates (names and variables are listed on the **WhatsApp** page; edit the
   names there to match what you created).
4. **Settings > WhatsApp**: enter the phone number ID and token, switch sending on, **Test connection**.

Messages are sent only through the official API using approved templates; the app never opens WhatsApp Web.
"Sent" means accepted by WhatsApp; a desktop app cannot receive delivery receipts.

## 8. Backups and the Access `.mdb` export

* **Backup & Restore** page: `.backup` (PostgreSQL custom format) and `.sql` are restorable and are the authoritative
  backups. Each file is validated after creation; *Verify a backup file* restores it into a scratch database and
  counts the rows. Restore saves a safety copy first and runs in one transaction (a failure changes nothing).
* Schedule: *Settings > Backup* (daily time, retention, formats, folder). The app must be running; a missed backup
  runs at the next start.
* **`.mdb` export is for opening your data in Microsoft Access. It is not a PostgreSQL backup and cannot be restored.**
  It needs the 64-bit *Microsoft Access Database Engine* (installed with Microsoft Office, or the free
  "Access Database Engine 2016 Redistributable (x64)"). The export writes the business tables with their
  relationships and then re-opens the file and checks every table's row count and money totals before reporting success.
  Credentials (`owners`) and sync internals are not exported. Money columns use Access `CURRENCY` (exact, 4 decimals).

## 9. Build the EXE and the installer

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1        # -> dist\GymManagement\GymManagement.exe
powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1  # -> dist\installer\GymManagementSetup-1.0.0.exe
```
The installer checks that PostgreSQL is present and warns if not. The installed app needs no Python or terminal.

## 10. Where things live

| What | Where |
|---|---|
| Application | `C:\Program Files\Gym Management System\` |
| Data, photos, receipts, logs, default backups | `%LOCALAPPDATA%\GymManagement\` (`logs\`, `backups\`, `photos\`, `receipts\`, `images\`) |
| Database | PostgreSQL, database `gym_management` |
| Secrets | Windows Credential Manager, service `GymManagement` |
| Optional overrides | `%LOCALAPPDATA%\GymManagement\.env` (see `.env.example`) |

## 11. Upgrades

Install the new version over the old one (your data folder and database are untouched). On start the app compares the
database schema with its own version and applies any pending Alembic migrations automatically (as `gym_migrator`).
Restoring an older backup is also brought up to the current schema. Take a backup before upgrading.

## 12. Troubleshooting

* *"Cannot reach PostgreSQL"* - start the *postgresql-x64-NN* service (Services app) and check port 5432.
* *Setup says the password is wrong* - it is the `postgres` password chosen at PostgreSQL install time.
* *`.mdb` export fails* - install the Access Database Engine (x64) (section 8).
* Logs: `%LOCALAPPDATA%\GymManagement\logs\gym.log`.
