from datetime import timedelta

import pytest
from sqlalchemy import select

from database.models import AuditLog, Owner
from services import auth_service as auth, clock, settings_admin_service as admin, settings_service
from services.errors import ValidationError

pytestmark = pytest.mark.db


@pytest.fixture
def owner(db):
    return auth.create_owner(db, "Owner", "Secret123", "The Owner")


def test_password_is_hashed_with_argon2_never_stored_plain(db, owner):
    assert owner.password_hash.startswith("$argon2id$") and "Secret123" not in owner.password_hash
    assert owner.username == "owner"  # normalised


@pytest.mark.parametrize("password", ["short1", "allletters", "12345678", "owner"])
def test_weak_passwords_rejected(db, password):
    with pytest.raises(ValidationError):
        auth.create_owner(db, "owner", password)


def test_duplicate_username_rejected(db, owner):
    with pytest.raises(ValidationError):
        auth.create_owner(db, "OWNER", "Another123")


def test_login_success_resets_counters_and_is_audited(db, owner):
    owner.failed_attempts = 3
    result = auth.authenticate(db, " Owner ", "Secret123")
    assert result.ok and result.display_name == "The Owner" and owner.failed_attempts == 0
    assert "auth.login" in set(db.scalars(select(AuditLog.action)))


def test_wrong_password_and_unknown_user_give_the_same_message(db, owner):
    a = auth.authenticate(db, "owner", "nope")
    b = auth.authenticate(db, "ghost", "nope")
    assert not a.ok and not b.ok and a.message == b.message == "Incorrect username or password."


def test_lockout_after_repeated_failures_then_expires(db, owner):
    now = clock.now()
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        last = auth.authenticate(db, "owner", "bad", now)
    assert "locked" in last.message.lower() and last.locked_until > now
    refused = auth.authenticate(db, "owner", "Secret123", now + timedelta(minutes=1))
    assert not refused.ok and "try again" in refused.message.lower()   # even the right password
    allowed = auth.authenticate(db, "owner", "Secret123", now + auth.LOCKOUT + timedelta(seconds=1))
    assert allowed.ok


def test_failed_logins_are_audited_without_the_password(db, owner):
    auth.authenticate(db, "owner", "wrong-password-xyz")
    rows = db.scalars(select(AuditLog).where(AuditLog.action == "auth.login_failed")).all()
    assert rows and "wrong-password-xyz" not in str(rows[0].details)


def test_inactive_account_cannot_log_in(db, owner):
    owner.is_active = False
    db.flush()
    assert not auth.authenticate(db, "owner", "Secret123").ok


def test_change_password(db, owner):
    with pytest.raises(ValidationError):
        auth.change_password(db, owner.id, "wrong", "NewSecret123")
    with pytest.raises(ValidationError):
        auth.change_password(db, owner.id, "Secret123", "weak")
    auth.change_password(db, owner.id, "Secret123", "NewSecret123")
    assert not auth.authenticate(db, "owner", "Secret123").ok
    assert auth.authenticate(db, "owner", "NewSecret123").ok


def test_reset_password_verifies_admin_clears_lock_and_preserves_other_accounts(db, owner, monkeypatch):
    other = auth.create_owner(db, "other", "OtherSecret123")
    other_hash = other.password_hash
    owner.failed_attempts = 4
    owner.locked_until = clock.now() + auth.LOCKOUT
    verify = []
    monkeypatch.setattr(auth.db_setup_service, "verify_admin_password", verify.append)
    auth.reset_password(db, " Owner ", "administrator-secret", "NewSecret123")
    assert verify == ["administrator-secret"]
    assert owner.failed_attempts == 0 and owner.locked_until is None
    assert other.password_hash == other_hash
    assert auth.authenticate(db, "owner", "NewSecret123").ok
    assert not auth.authenticate(db, "owner", "Secret123").ok
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "auth.password_reset")).one()
    assert entry.entity_id == owner.id and entry.details == {"method": "postgres_admin"}
    assert "administrator-secret" not in str(entry.details) and "NewSecret123" not in str(entry.details)


def test_reset_password_rejects_bad_admin_without_changing_owner(db, owner, monkeypatch):
    original = owner.password_hash

    def reject(_password):
        raise auth.db_setup_service.SetupError("Administrator password is incorrect.")

    monkeypatch.setattr(auth.db_setup_service, "verify_admin_password", reject)
    with pytest.raises(ValidationError, match="Administrator password is incorrect"):
        auth.reset_password(db, "owner", "wrong-admin-password", "NewSecret123")
    assert owner.password_hash == original
    assert db.scalar(select(AuditLog.id).where(AuditLog.action == "auth.password_reset")) is None


@pytest.mark.parametrize("username,new", [("", "NewSecret123"), ("owner", "weak"), ("owner", "owner")])
def test_reset_password_enforces_required_fields_and_strength(db, owner, monkeypatch, username, new):
    monkeypatch.setattr(auth.db_setup_service, "verify_admin_password",
                        lambda _password: pytest.fail("Invalid form must not attempt administrator authentication"))
    with pytest.raises(ValidationError):
        auth.reset_password(db, username, "administrator-secret", new)
    assert auth.authenticate(db, "owner", "Secret123").ok


def test_reset_password_requires_existing_active_owner(db, owner, monkeypatch):
    from services.errors import NotFoundError

    monkeypatch.setattr(auth.db_setup_service, "verify_admin_password", lambda _password: None)
    with pytest.raises(NotFoundError):
        auth.reset_password(db, "missing", "administrator-secret", "NewSecret123")
    owner.is_active = False
    original = owner.password_hash
    with pytest.raises(ValidationError, match="inactive"):
        auth.reset_password(db, "owner", "administrator-secret", "NewSecret123")
    assert owner.password_hash == original and not owner.is_active


def test_reset_password_rolls_back_with_transaction(db, owner, monkeypatch):
    original = owner.password_hash
    monkeypatch.setattr(auth.db_setup_service, "verify_admin_password", lambda _password: None)
    with db.begin_nested() as transaction:
        auth.reset_password(db, "owner", "administrator-secret", "NewSecret123")
        transaction.rollback()
    assert owner.password_hash == original
    assert db.scalar(select(AuditLog.id).where(AuditLog.action == "auth.password_reset")) is None


def test_owner_accounts_are_never_queued_for_cloud_sync(db, owner):
    from database.models import SyncQueue

    assert db.scalar(select(SyncQueue.id).where(SyncQueue.entity_table == "owners")) is None
    assert Owner.__tablename__ not in __import__("database.serialization", fromlist=["x"]).sync_models()


# ---- settings validation -------------------------------------------------------------------------

def _gym(**over):
    base = dict(name="Iron House", address="1 Main St", phone="9876543210", email="gym@example.com", currency="inr",
                member_code_prefix="ih", country_code="+91", require_gender=True, expiry_alert_days="10", logo=None)
    return {**base, **over}


def test_gym_profile_saved_and_normalised(db):
    admin.save_gym_profile(db, _gym())
    g = lambda k: settings_service.get(db, k)  # noqa: E731
    assert (g("gym.name"), g("gym.currency"), g("gym.member_code_prefix"), g("gym.country_code")) == \
           ("Iron House", "INR", "IH", "91")
    assert g("gym.require_gender") is True and g("membership.expiry_alert_days") == 10


@pytest.mark.parametrize("bad", [dict(name=""), dict(email="nope"), dict(member_code_prefix="a b"),
                                 dict(country_code="9x"), dict(expiry_alert_days="0"), dict(phone="12")])
def test_gym_profile_rejects_bad_input(db, bad):
    with pytest.raises(ValidationError):
        admin.save_gym_profile(db, _gym(**bad))


def test_payment_settings(db):
    base = dict(methods="Cash, UPI, Cash", categories="Rent, Power", upi_id="gym@okbank", payee="Iron", qr_image=None)
    admin.save_payment_settings(db, base)
    assert settings_service.get(db, "payment.methods") == ["Cash", "UPI"]  # de-duplicated, order kept
    for bad in (dict(methods=" , "), dict(upi_id="nobank"), dict(categories="")):
        with pytest.raises(ValidationError):
            admin.save_payment_settings(db, {**base, **bad})


def _backup(**over):
    base = dict(folder="", schedule=True, time="03:30", retention="7", fmt_backup=True, fmt_sql=False, fmt_mdb=False,
                pg_bin="")
    return {**base, **over}


def test_backup_settings(db, tmp_path):
    admin.save_backup_settings(db, _backup(folder=str(tmp_path / "new")))
    assert (tmp_path / "new").is_dir() and settings_service.get(db, "local.backup.formats") == ["backup"]
    assert settings_service.get(db, "local.backup.time") == "03:30"
    for bad in (dict(time="25:00"), dict(retention="0"), dict(fmt_backup=False), dict(pg_bin=str(tmp_path))):
        with pytest.raises(Exception):
            admin.save_backup_settings(db, _backup(**bad))


def test_whatsapp_settings(db):
    base = dict(enabled=True, phone_number_id="123", api_version="v21.0", language="en", reminder_days="7, 1",
                renewal_days="3")
    admin.save_whatsapp_settings(db, base)
    assert settings_service.get(db, "whatsapp.reminder_days") == [7, 1]
    with pytest.raises(ValidationError):
        admin.save_whatsapp_settings(db, {**base, "phone_number_id": ""})
    with pytest.raises(ValidationError):
        admin.save_whatsapp_settings(db, {**base, "reminder_days": "seven"})


def test_cloud_settings(db):
    admin.save_cloud_settings(db, dict(database="gym_prod", auto_minutes="15"))
    assert settings_service.get(db, "local.sync.auto_minutes") == 15
    with pytest.raises(ValidationError):
        admin.save_cloud_settings(db, dict(database="gym", auto_minutes="soon"))


def test_secrets_never_land_in_the_settings_table(db):
    from database.models import GymSetting

    admin.save_whatsapp_settings(db, dict(enabled=False, phone_number_id="1", api_version="v21.0", language="en",
                                          reminder_days="7", renewal_days="3"))
    admin.save_cloud_settings(db, dict(database="gym", auto_minutes="0"))
    keys = {k for k in db.scalars(select(GymSetting.key))}
    assert not any("token" in k or "uri" in k or "password" in k for k in keys)
