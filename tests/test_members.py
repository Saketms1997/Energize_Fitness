from datetime import date
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook

from services import member_service, settings_service
from services.errors import DuplicateError, NotFoundError, ValidationError
from tests.factories import make_member, make_paid_membership, make_plan

pytestmark = pytest.mark.db


def test_member_codes_increment(db):
    a, b = make_member(db), make_member(db, "Ben", "9000000002")
    assert (a.member_code, b.member_code) == ("GYM-00001", "GYM-00002")


def test_code_prefix_is_configurable(db):
    settings_service.set_value(db, "gym.member_code_prefix", "fit")
    assert make_member(db).member_code == "FIT-00001"


def test_duplicate_name_and_phone_rejected(db):
    make_member(db)
    with pytest.raises(DuplicateError):
        make_member(db, "  asha rao ", "9000000001")


def test_same_phone_different_name_allowed(db):
    make_member(db)
    assert make_member(db, "Ravi Rao", "9000000001")


@pytest.mark.parametrize("phone", ["", "abc", "123", "1" * 16])
def test_invalid_phone_rejected(db, phone):
    with pytest.raises(ValidationError):
        make_member(db, phone=phone)


def test_name_required_and_email_validated(db):
    with pytest.raises(ValidationError):
        make_member(db, name="   ")
    with pytest.raises(ValidationError):
        make_member(db, email="not-an-email")


def test_gender_required_when_configured(db):
    settings_service.set_value(db, "gym.require_gender", True)
    with pytest.raises(ValidationError):
        make_member(db)
    assert make_member(db, gender="Female").gender == "female"


def test_update_member_and_duplicate_guard(db):
    a, b = make_member(db), make_member(db, "Ben", "9000000002")
    member_service.update_member(db, b.id, email="ben@example.com", address="1 Main St")
    assert b.email == "ben@example.com" and b.version == 2
    with pytest.raises(DuplicateError):
        member_service.update_member(db, b.id, full_name="Asha Rao", phone="9000000001")
    with pytest.raises(ValidationError):
        member_service.update_member(db, a.id, member_code="HACK")


def test_deactivate_and_reactivate(db):
    m = make_member(db)
    member_service.set_member_active(db, m.id, False)
    assert not m.is_active
    assert [r.name for r in member_service.search_members(db, status="inactive")] == ["Asha Rao"]
    member_service.set_member_active(db, m.id, True)
    assert member_service.search_members(db, status="inactive") == []


def test_unknown_member_raises(db):
    import uuid

    with pytest.raises(NotFoundError):
        member_service.get_member(db, uuid.uuid4())


def test_search_text_and_status_filters(db):
    plan = make_plan(db)
    a, b, c = make_member(db), make_member(db, "Ben Kumar", "9111111111"), make_member(db, "Cara", "9222222222")
    make_paid_membership(db, a, plan, start=date(2026, 1, 1))  # ended Jan 30
    make_paid_membership(db, b, plan, start=date(2026, 3, 1))
    today = date(2026, 3, 10)
    assert [r.name for r in member_service.search_members(db, "ben")] == ["Ben Kumar"]
    assert [r.name for r in member_service.search_members(db, "9222")] == ["Cara"]
    assert [r.name for r in member_service.search_members(db, status="active", today=today)] == ["Ben Kumar"]
    assert [r.name for r in member_service.search_members(db, status="expired", today=today)] == ["Asha Rao"]
    assert [r.name for r in member_service.search_members(db, status="none", today=today)] == ["Cara"]
    with pytest.raises(ValidationError):
        member_service.search_members(db, status="bogus")


def test_search_joined_date_range(db):
    make_member(db, date_of_joining=date(2026, 1, 5))
    make_member(db, "Ben", "9000000002", date_of_joining=date(2026, 2, 5))
    rows = member_service.search_members(db, joined_from=date(2026, 2, 1), joined_to=date(2026, 2, 28))
    assert [r.name for r in rows] == ["Ben"]


def test_excel_export_roundtrip(db, tmp_path):
    make_member(db)
    path = member_service.export_members_xlsx(member_service.search_members(db), tmp_path / "m.xlsx")
    rows = list(load_workbook(path).active.values)
    assert rows[0][0] == "Member ID" and rows[1][1] == "Asha Rao"


def test_photo_is_copied_into_app_data(db, tmp_path, monkeypatch):
    monkeypatch.setattr(member_service, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path / "data"))
    src = tmp_path / "me.png"
    src.write_bytes(b"\x89PNG fake")
    m = make_member(db, photo_source=src)
    assert m.photo_path == f"photos/{m.id}.png"
    assert member_service.photo_path(m).read_bytes() == b"\x89PNG fake"


def test_non_image_photo_rejected(db, tmp_path):
    bad = tmp_path / "x.exe"
    bad.write_bytes(b"x")
    with pytest.raises(ValidationError):
        make_member(db, photo_source=bad)


def test_member_history_collects_everything(db):
    m, plan = make_member(db), make_plan(db)
    make_paid_membership(db, m, plan)
    history = member_service.member_history(db, m.id)
    assert len(history["subscriptions"]) == 1 and len(history["payments"]) == 1
