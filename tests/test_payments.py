import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from services import clock, membership_service, payment_service, receipt_service, settings_service, upi_service
from services.errors import BusinessRuleError, DuplicateError, ValidationError
from tests.factories import make_member, make_plan, make_subscription

pytestmark = pytest.mark.db


@pytest.fixture
def sub(db):
    return make_subscription(db, make_member(db), make_plan(db), date(2026, 5, 1))


def test_partial_payment_does_not_activate(db, sub):
    payment_service.record_confirmed_payment(db, sub.id, "400", "Cash")
    assert sub.status == "pending" and payment_service.outstanding(db, sub.id) == Decimal("600.00")


def test_full_payment_activates_and_issues_receipt(db, sub):
    p1 = payment_service.record_confirmed_payment(db, sub.id, "400", "Cash")
    p2 = payment_service.record_confirmed_payment(db, sub.id, "600", "Cash")
    assert sub.status == "active"
    assert re.fullmatch(r"REC-\d{6}-\d{4}", p1.receipt_no) and p1.receipt_no != p2.receipt_no


def test_overpayment_blocked(db, sub):
    with pytest.raises(BusinessRuleError):
        payment_service.record_confirmed_payment(db, sub.id, "1000.01", "Cash")


def test_pending_payment_is_not_money_until_owner_confirms(db, sub):
    pending = payment_service.create_pending_payment(db, sub.id, "1000", "Cash")
    assert pending.status == "pending" and pending.receipt_no is None and sub.status == "pending"
    assert payment_service.collection_summary(db, date(2020, 1, 1), date(2030, 1, 1)).net == 0
    payment_service.confirm_payment(db, pending.id, confirmed_by="owner")
    assert sub.status == "active" and pending.confirmed_by == "owner"


def test_upi_requires_reference_and_rejects_reuse(db, sub):
    with pytest.raises(ValidationError):
        payment_service.record_confirmed_payment(db, sub.id, "100", "UPI")
    payment_service.record_confirmed_payment(db, sub.id, "100", "upi", reference="UTR123")
    with pytest.raises(DuplicateError):
        payment_service.record_confirmed_payment(db, sub.id, "100", "UPI", reference="utr123")


def test_unknown_method_rejected_and_methods_configurable(db, sub):
    with pytest.raises(ValidationError):
        payment_service.record_confirmed_payment(db, sub.id, "100", "Barter")
    settings_service.set_value(db, "payment.methods", ["Cash", "Cheque"])
    assert payment_service.record_confirmed_payment(db, sub.id, "100", "cheque").method == "Cheque"


def test_void_reverts_activation(db, sub):
    p = payment_service.record_confirmed_payment(db, sub.id, "1000", "Cash")
    payment_service.void_payment(db, p.id, "entered twice")
    assert p.status == "voided" and sub.status == "pending"
    assert payment_service.net_paid(db, sub.id) == 0
    with pytest.raises(ValidationError):
        payment_service.void_payment(db, p.id, "")


def test_partial_refund_keeps_membership(db, sub):
    p = payment_service.record_confirmed_payment(db, sub.id, "1000", "Cash")
    r = payment_service.refund_payment(db, p.id, "200", "discount applied")
    assert r.kind == "refund" and r.refund_of_id == p.id and r.receipt_no.startswith("RFD-")
    assert payment_service.net_paid(db, sub.id) == Decimal("800.00")
    assert payment_service.refundable_amount(db, p.id) == Decimal("800.00")
    # a goodwill refund must not reactivate a debt or deactivate the membership
    assert sub.status == "active" and payment_service.outstanding(db, sub.id) == 0


def test_refund_cannot_exceed_payment(db, sub):
    p = payment_service.record_confirmed_payment(db, sub.id, "1000", "Cash")
    payment_service.refund_payment(db, p.id, "700", "x")
    with pytest.raises(BusinessRuleError):
        payment_service.refund_payment(db, p.id, "400", "x")


def test_full_refund_cancels_membership_and_blocks_void(db, sub):
    p = payment_service.record_confirmed_payment(db, sub.id, "1000", "Cash")
    payment_service.refund_payment(db, p.id, "1000", "left town")
    assert sub.status == "cancelled"
    with pytest.raises(BusinessRuleError):
        payment_service.void_payment(db, p.id, "oops")


def test_only_confirmed_payments_refundable_and_confirm_is_idempotent(db, sub):
    pending = payment_service.create_pending_payment(db, sub.id, "500", "Cash")
    with pytest.raises(BusinessRuleError):
        payment_service.refund_payment(db, pending.id, "100", "x")
    payment_service.confirm_payment(db, pending.id)
    again = payment_service.confirm_payment(db, pending.id)
    assert again.receipt_no == pending.receipt_no


def test_every_payment_action_is_audited(db, sub):
    from sqlalchemy import select

    from database.models import AuditLog

    p = payment_service.record_confirmed_payment(db, sub.id, "1000", "Cash")
    payment_service.refund_payment(db, p.id, "100", "r")
    actions = set(db.scalars(select(AuditLog.action)))
    assert {"payment.create_pending", "payment.confirm", "payment.refund"} <= actions


def test_list_and_outstanding_views(db, sub):
    payment_service.record_confirmed_payment(db, sub.id, "250", "Cash")
    assert [r.amount for r in payment_service.list_payments(db, "confirmed")] == [Decimal("250.00")]
    out = payment_service.outstanding_subscriptions(db)
    assert out[0].outstanding == Decimal("750.00")


def test_collection_summary_nets_refunds_by_method(db, sub):
    p = payment_service.record_confirmed_payment(db, sub.id, "600", "Cash")
    payment_service.record_confirmed_payment(db, sub.id, "400", "UPI", reference="U1")
    payment_service.refund_payment(db, p.id, "100", "r")
    today = clock.today()
    s = payment_service.collection_summary(db, today, today)
    assert (s.payments_total, s.refunds_total, s.net) == (Decimal("1000.00"), Decimal("100.00"), Decimal("900.00"))
    assert s.by_method == {"Cash": Decimal("500.00"), "UPI": Decimal("400.00")}


def test_receipt_pdf_only_for_confirmed(db, sub, tmp_path):
    pending = payment_service.create_pending_payment(db, sub.id, "1000", "Cash")
    with pytest.raises(BusinessRuleError):
        receipt_service.generate_receipt_pdf(db, pending.id, tmp_path)
    payment_service.confirm_payment(db, pending.id)
    pdf = receipt_service.generate_receipt_pdf(db, pending.id, tmp_path)
    assert pdf.read_bytes().startswith(b"%PDF") and pdf.stem == pending.receipt_no


# ---- UPI ----------------------------------------------------------------------------------

def test_upi_uri_contains_amount_and_payee():
    uri = upi_service.build_upi_uri("gym@okbank", "Energize Gym", Decimal("1500"), "Membership")
    assert uri.startswith("upi://pay?pa=gym@okbank") and "am=1500.00" in uri and "cu=INR" in uri
    with pytest.raises(ValidationError):
        upi_service.build_upi_uri("nobank", "x")


def test_qr_requires_configuration(db):
    with pytest.raises(BusinessRuleError):
        upi_service.get_payment_qr(db, Decimal("100"))


def test_dynamic_qr_from_upi_id(db):
    settings_service.set_value(db, "payment.upi_id", "gym@okbank")
    qr = upi_service.get_payment_qr(db, Decimal("100"))
    assert qr.dynamic and qr.png.startswith(b"\x89PNG") and qr.upi_id == "gym@okbank"


def test_uploaded_qr_image_preferred(db, tmp_path, monkeypatch):
    monkeypatch.setattr(upi_service, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path / "data"))
    src = tmp_path / "myqr.png"
    src.write_bytes(b"\x89PNGowner-qr")
    settings_service.set_value(db, "payment.upi_qr_path", upi_service.store_qr_image(src))
    settings_service.set_value(db, "payment.upi_id", "gym@okbank")
    qr = upi_service.get_payment_qr(db, Decimal("100"))
    assert not qr.dynamic and qr.png == b"\x89PNGowner-qr"
