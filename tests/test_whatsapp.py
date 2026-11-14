from contextlib import contextmanager
from datetime import date, timedelta

import pytest
import requests
import responses
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import WhatsAppLog
from services import settings_service, whatsapp_service as wa
from services.errors import BusinessRuleError, ValidationError
from tests.factories import make_member, make_paid_membership, make_plan

pytestmark = pytest.mark.db
URL = "https://graph.facebook.com/v21.0/PHONEID/messages"


@pytest.fixture
def configured(db, monkeypatch):
    settings_service.set_value(db, "whatsapp.enabled", True)
    settings_service.set_value(db, "whatsapp.phone_number_id", "PHONEID")
    monkeypatch.setattr(wa, "get_access_token", lambda: "TOKEN")
    return db


@pytest.fixture
def scope(engine_a, configured):
    configured.commit()

    @contextmanager
    def _scope():
        s = Session(engine_a)
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    return _scope


def _queue(db, phone="9876543210"):
    m = make_member(db, phone=phone)
    sub, pay = make_paid_membership(db, m, make_plan(db))
    return wa.queue_registration_message(db, m, sub, pay)


def test_phone_normalisation():
    assert wa.normalize_phone("98765 43210") == "919876543210"
    assert wa.normalize_phone("098765-43210") == "919876543210"
    assert wa.normalize_phone("+44 7700 900123") == "447700900123"
    assert wa.normalize_phone("919876543210") == "919876543210"


def test_registration_message_contents(configured):
    log = _queue(configured)
    p = log.payload
    assert log.template_name == "registration_confirmation" and log.status == "queued"
    assert p["params"][0] == "Asha Rao" and p["params"][1] == "My Gym" and p["params"][5] == "INR 1,000.00"
    assert "welcome to My Gym" in p["preview"]


def test_duplicate_events_are_not_queued_twice(configured):
    m = make_member(configured)
    sub, pay = make_paid_membership(configured, m, make_plan(configured))
    assert wa.queue_registration_message(configured, m, sub, pay)
    assert wa.queue_registration_message(configured, m, sub, pay) is None


def test_templates_are_configurable(configured):
    wa.save_template(configured, "registration", "my_welcome_v2", ["member_name", "gym_name"],
                     "Hello {member_name} @ {gym_name}", language="hi")
    log = _queue(configured)
    assert log.template_name == "my_welcome_v2" and log.payload["language"] == "hi"
    assert log.payload["params"] == ["Asha Rao", "My Gym"] and log.payload["preview"] == "Hello Asha Rao @ My Gym"
    with pytest.raises(ValidationError):
        wa.save_template(configured, "nonsense", "x", [], "")


@responses.activate
def test_send_success_uses_official_cloud_api_payload(scope, configured):
    log = _queue(configured)
    configured.commit()
    responses.post(URL, json={"messages": [{"id": "wamid.ABC"}]}, status=200)
    result = wa.process_queue(scope=scope)
    assert result == {"sent": 1, "failed": 0, "skipped": 0}
    request = responses.calls[0].request
    assert request.headers["Authorization"] == "Bearer TOKEN"
    import json

    body = json.loads(request.body)
    assert body["messaging_product"] == "whatsapp" and body["type"] == "template"
    assert body["to"] == "919876543210" and body["template"]["name"] == "registration_confirmation"
    assert body["template"]["components"][0]["parameters"][0] == {"type": "text", "text": "Asha Rao"}
    with scope() as s:
        row = s.get(WhatsAppLog, log.id)
        assert row.status == "sent" and row.provider_message_id == "wamid.ABC" and row.attempts == 1


@responses.activate
def test_permanent_failure_is_logged_and_needs_owner_retry(scope, configured):
    log = _queue(configured)
    configured.commit()
    responses.post(URL, json={"error": {"message": "Template not approved"}}, status=400)
    assert wa.process_queue(scope=scope)["failed"] == 1
    with scope() as s:
        row = s.get(WhatsAppLog, log.id)
        assert row.status == "failed" and "Template not approved" in row.error and not row.error.startswith("RETRY")
    # a 2nd run does not auto-retry a permanent failure
    assert wa.process_queue(scope=scope) == {"sent": 0, "failed": 0, "skipped": 0}
    with scope() as s:
        assert wa.retry_failed(s) == 1


@responses.activate
def test_transient_failure_retried_after_backoff_then_succeeds(scope, configured):
    from services import clock

    log = _queue(configured)
    configured.commit()
    responses.post(URL, status=503, json={"error": {"message": "unavailable"}})
    wa.process_queue(scope=scope)
    with scope() as s:
        assert s.get(WhatsAppLog, log.id).error.startswith("RETRY:")
    assert wa.process_queue(scope=scope)["failed"] == 0  # too soon: backoff not elapsed
    responses.replace(responses.POST, URL, json={"messages": [{"id": "wamid.OK"}]}, status=200)
    later = clock.now() + timedelta(minutes=wa.RETRY_BACKOFF_MINUTES + 1)
    assert wa.process_queue(scope=scope, now=later)["sent"] == 1


@responses.activate
def test_network_error_keeps_message_for_retry(scope, configured):
    log = _queue(configured)
    configured.commit()
    responses.post(URL, body=requests.ConnectionError("offline"))
    assert wa.process_queue(scope=scope)["failed"] == 1
    with scope() as s:
        assert s.get(WhatsAppLog, log.id).error.startswith("RETRY: Network error")


def test_attempts_are_capped(scope, configured, monkeypatch):
    log = _queue(configured)
    configured.commit()
    monkeypatch.setattr(wa, "_post", lambda *a, **k: (_ for _ in ()).throw(requests.Timeout("slow")))
    for _ in range(wa.MAX_ATTEMPTS):
        from services import clock

        wa.process_queue(scope=scope, now=clock.now() + timedelta(days=1))
    with scope() as s:
        row = s.get(WhatsAppLog, log.id)
        assert row.attempts == wa.MAX_ATTEMPTS and not row.error.startswith("RETRY:")


def test_nothing_sent_when_disabled_or_unconfigured(db, monkeypatch):
    assert wa.queue_message(db, "registration", make_member(db), {}) is None
    settings_service.set_value(db, "whatsapp.enabled", True)
    monkeypatch.setattr(wa, "get_access_token", lambda: None)
    db.commit()
    with pytest.raises(BusinessRuleError):
        wa.process_queue(scope=lambda: _noscope(db))


@contextmanager
def _noscope(session):
    yield session


def test_expiry_and_renewal_reminders_queued_once_per_day(configured):
    today = date(2026, 5, 20)
    plan = make_plan(configured)
    a = make_member(configured, "Soon", "9000000001")
    b = make_member(configured, "Lapsed", "9000000002")
    c = make_member(configured, "Far", "9000000003")
    make_paid_membership(configured, a, plan, today - timedelta(days=22))   # ends in 7 days
    make_paid_membership(configured, b, plan, today - timedelta(days=32))   # ended 3 days ago
    make_paid_membership(configured, c, plan, today - timedelta(days=5))
    assert wa.queue_reminders(configured, today) == 2
    assert wa.queue_reminders(configured, today) == 0  # idempotent
    kinds = sorted(l.kind for l in configured.scalars(select(WhatsAppLog)))
    assert kinds == ["expiry_reminder", "renewal_reminder"]


def test_no_reminder_for_members_who_already_renewed(configured):
    from services import membership_service

    today = date(2026, 5, 20)
    m, plan = make_member(configured), make_plan(configured)
    make_paid_membership(configured, m, plan, today - timedelta(days=22))
    membership_service.renew_subscription(configured, m.id, today=today)
    assert wa.queue_reminders(configured, today) == 0


@responses.activate
def test_connection_test(configured):
    responses.get("https://graph.facebook.com/v21.0/PHONEID",
                  json={"verified_name": "Iron House", "display_phone_number": "+91 98765 43210"})
    assert "Iron House" in wa.test_connection(configured)
    responses.replace(responses.GET, "https://graph.facebook.com/v21.0/PHONEID", status=401,
                      json={"error": {"message": "Invalid OAuth access token"}})
    with pytest.raises(BusinessRuleError, match="Invalid OAuth"):
        wa.test_connection(configured)
