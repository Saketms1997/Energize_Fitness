# Gym Management System (Windows desktop)

A standalone Windows desktop application for running a gym: members, memberships, attendance,
payments with a UPI QR workflow, trainers, expenses, reports, backups, MongoDB Atlas two-way sync and
WhatsApp messages. Python 3.12+, PySide6, PostgreSQL (local, primary), SQLAlchemy 2, Alembic.

* **Offline first.** Everything works without internet. PostgreSQL on the PC is the source of truth;
  the cloud is an optional extra and its failure never loses or blocks local data.
* **Money is protected.** A payment is recorded only when the owner explicitly confirms it. Confirmed
  payments are never edited or deleted, only refunded or voided, and every action is audit-logged.

Full instructions: **[docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md)** · what is verified and what is not:
**[docs/STATUS.md](docs/STATUS.md)**.

## Quick start (PowerShell, from this folder)

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe main.py
```

On first run the app shows a **setup wizard** (enter the PostgreSQL `postgres` password once; it is not
stored), then asks you to create the owner account. No terminal is needed after installation.
Command-line alternative: `.\.venv\Scripts\python.exe scripts\setup_database.py`.
Forgotten login passwords can be reset from **Forgot password?** using the PostgreSQL administrator password;
gym records are preserved. See the setup guide for recovery and MongoDB TLS troubleshooting.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q              # everything (needs the local PostgreSQL)
.\.venv\Scripts\python.exe -m pytest -q -m "not db"  # no database needed
```
Database tests create and clean their own databases (`gym_management_test`, `gym_management_test_b`);
your real data is never touched.

## Build the installable app

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1        # dist\GymManagement\GymManagement.exe
powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1  # + Setup.exe (needs Inno Setup 6)
```

## Layout

| Folder | Purpose |
|---|---|
| `config/` | settings, logging; secrets via Windows Credential Manager |
| `database/` | SQLAlchemy models, change tracking + sync outbox, Alembic migrations |
| `services/` | all business logic (no UI code); one module per area |
| `ui/` | PySide6 screens, dialogs, charts, dark theme |
| `scripts/` | database setup, build scripts |
| `installer/` | PyInstaller spec, Inno Setup script |
| `tests/` | pytest suite |
