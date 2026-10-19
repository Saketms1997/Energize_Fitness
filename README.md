# Energize Fitness — gym management

Offline-first gym management for a single owner: members, plans, payments (UPI QR + manual verification), attendance, trainers, WhatsApp reminders, encrypted backups, and optional cloud sync. Flutter (Windows first; Android/iOS targets present).

* Owner's guide: [docs/USER_GUIDE.md](docs/USER_GUIDE.md)
* Setup (build, installer, Supabase, WhatsApp, Android): [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md)
* What is built, tested and *not* verified: [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md)
* Release checklist: [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md)

## Develop

```powershell
$env:Path = "D:\tools\flutter\bin;$env:Path"
flutter pub get
dart run build_runner build --delete-conflicting-outputs   # Drift code generation
flutter analyze
flutter test
flutter run -d windows
```

Layout: `lib/database` (Drift schema + migrations) · `lib/repositories` (all business rules; every write = row + sync outbox + audit log in one transaction) · `lib/services` (UPI, templates, WhatsApp sender, backup) · `lib/sync` (outbox push / pull, cloud auth) · `lib/features` (screens) · `backend` (Supabase SQL, edge functions, their tests).
