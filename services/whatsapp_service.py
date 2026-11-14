"""WhatsApp messaging through Meta's official WhatsApp Cloud API.

* Messages are *template* messages (Meta requires approved templates for business-initiated
  messages). Template names/languages/parameter order are owner-configurable; the template
  itself must be created and approved in Meta Business Manager.
* Nothing opens WhatsApp Web or a compose window.
* Sending is queue based: callers enqueue (inside their DB transaction); ``process_queue``
  sends from a worker. ``status`` = ``sent`` means *accepted by Meta*; a desktop app has no public
  webhook endpoint, so delivered/read receipts are not tracked.
* The access token lives in Windows Credential Manager, never in the database.
"""
from __future__ import annotations

import logging
import uuid
from urllib.parse import quote
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import get_secret, set_secret
from database.models import Member, Payment, Subscription, WhatsAppLog
from services import clock, membership_service, settings_service
from services.errors import BusinessRuleError, ValidationError

log = logging.getLogger(__name__)

TOKEN_SECRET = "whatsapp:access_token"
GRAPH_URL = "https://graph.facebook.com"
MAX_ATTEMPTS = 5
RETRY_BACKOFF_MINUTES = 5
REQUEST_TIMEOUT = 15

KINDS = ("registration", "payment_confirmation", "expiry_reminder", "renewal_reminder")

# name = template name as approved in Meta; params = ordered {{1}}, {{2}}... values.
DEFAULT_TEMPLATES: dict[str, dict[str, Any]] = {
    "registration": {
        "name": "registration_confirmation",
        "params": ["member_name", "gym_name", "plan_name", "start_date", "expiry_date", "amount_paid"],
        "preview": "Hi {member_name}, welcome to {gym_name}! Your {plan_name} membership runs from "
                   "{start_date} to {expiry_date}. Amount paid: {amount_paid}.",
    },
    "payment_confirmation": {
        "name": "payment_confirmation",
        "params": ["member_name", "amount_paid", "receipt_no", "gym_name"],
        "preview": "Hi {member_name}, we received {amount_paid} (receipt {receipt_no}). Thank you! - {gym_name}",
    },
    "expiry_reminder": {
        "name": "membership_expiry_reminder",
        "params": ["member_name", "plan_name", "expiry_date", "days_left", "gym_name"],
        "preview": "Hi {member_name}, your {plan_name} membership expires on {expiry_date} "
                   "({days_left} day(s) left). - {gym_name}",
    },
    "renewal_reminder": {
        "name": "membership_renewal_reminder",
        "params": ["member_name", "plan_name", "expiry_date", "gym_name"],
        "preview": "Hi {member_name}, your {plan_name} membership ended on {expiry_date}. "
                   "Renew to keep training with {gym_name}!",
    },
}


# ---- configuration ------------------------------------------------------------------------

def set_access_token(token: str) -> None:
    set_secret(TOKEN_SECRET, token.strip())


def get_access_token() -> str | None:
    return get_secret(TOKEN_SECRET, "GYM_WHATSAPP_TOKEN")


def get_templates(session: Session) -> dict[str, dict[str, Any]]:
    stored = settings_service.get(session, "whatsapp.templates") or {}
    return {k: {**DEFAULT_TEMPLATES[k], **stored.get(k, {})} for k in KINDS}


def save_template(session: Session, kind: str, name: str, params: list[str], preview: str,
                  language: str | None = None) -> None:
    if kind not in KINDS:
        raise ValidationError(f"Unknown message type '{kind}'.")
    name = name.strip()
    if not name:
        raise ValidationError("Template name is required.", "Template name")
    stored = dict(settings_service.get(session, "whatsapp.templates") or {})
    stored[kind] = {"name": name, "params": params, "preview": preview,
                    **({"language": language} if language else {})}
    settings_service.set_value(session, "whatsapp.templates", stored)


def is_enabled(session: Session) -> bool:
    return bool(settings_service.get(session, "whatsapp.enabled"))


def _config(session: Session) -> dict[str, str]:
    phone_id = (settings_service.get(session, "whatsapp.phone_number_id") or "").strip()
    token = get_access_token()
    if not phone_id or not token:
        raise BusinessRuleError("WhatsApp is not configured: add the phone number ID and access token in Settings.")
    return {"phone_id": phone_id, "token": token,
            "version": settings_service.get(session, "whatsapp.api_version") or "v21.0",
            "language": settings_service.get(session, "whatsapp.language") or "en"}


# ---- message building ---------------------------------------------------------------------

def normalize_phone(phone: str, country_code: str = "91") -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    if phone.strip().startswith("+"):
        return digits
    digits = digits.lstrip("0")
    return digits if len(digits) > 10 else f"{country_code}{digits}"


def _money(value) -> str:
    return f"INR {value:,.2f}"


def build_context(session: Session, member: Member, sub: Subscription | None = None,
                  payment: Payment | None = None, today: date | None = None) -> dict[str, str]:
    today = today or clock.today()
    ctx = {"member_name": member.full_name, "gym_name": settings_service.get(session, "gym.name")}
    if sub is not None:
        ctx.update(plan_name=sub.plan.name, start_date=f"{sub.start_date:%d %b %Y}",
                   expiry_date=f"{sub.end_date:%d %b %Y}", days_left=str(max(0, (sub.end_date - today).days)),
                   amount_due=_money(sub.amount_due))
    if payment is not None:
        ctx.update(amount_paid=_money(payment.amount), receipt_no=payment.receipt_no or "")
    return ctx


def render_preview(template: dict[str, Any], context: dict[str, str]) -> str:
    return template["preview"].format_map({**{k: "" for k in template["params"]}, **context})


def build_chat_link(session: Session, payment_id: uuid.UUID, kind: str = "payment_confirmation") -> str:
    """A ``wa.me`` link that opens a chat with the member and the message already typed (owner presses Send).

    Uses the same editable message text as the WhatsApp page, but needs no API, token or approved template.
    """
    payment = session.get(Payment, payment_id)
    if payment is None:
        raise ValidationError("Payment not found.")
    member = session.get(Member, payment.member_id)
    sub = session.get(Subscription, payment.subscription_id) if payment.subscription_id else None
    if not (member.phone or "").strip():
        raise ValidationError(f"{member.full_name} has no phone number saved.")
    country = settings_service.get(session, "gym.country_code") or "91"
    text = render_preview(get_templates(session)[kind], build_context(session, member, sub, payment))
    return f"https://wa.me/{normalize_phone(member.phone, country)}?text={quote(text)}"


@dataclass(frozen=True)
class ReminderLink:
    subscription_id: uuid.UUID
    member_name: str
    phone: str
    url: str | None   # None when the member has no phone number
    kind: str


def build_reminder_links(session: Session, subscription_ids: list[uuid.UUID],
                         today: date | None = None) -> list[ReminderLink]:
    """``wa.me`` links for membership reminders: expiry notice before the end date, renewal notice after it."""
    today = today or clock.today()
    country = settings_service.get(session, "gym.country_code") or "91"
    templates = get_templates(session)
    links = []
    for sub_id in subscription_ids:
        sub = session.get(Subscription, sub_id)
        if sub is None:
            continue
        member = session.get(Member, sub.member_id)
        kind = "expiry_reminder" if sub.end_date >= today else "renewal_reminder"
        if not (member.phone or "").strip():
            links.append(ReminderLink(sub_id, member.full_name, "", None, kind))
            continue
        text = render_preview(templates[kind], build_context(session, member, sub, None, today))
        phone = normalize_phone(member.phone, country)
        links.append(ReminderLink(sub_id, member.full_name, phone, f"https://wa.me/{phone}?text={quote(text)}", kind))
    return links


def queue_message(session: Session, kind: str, member: Member, context: dict[str, str],
                  dedupe_key: str | None = None) -> WhatsAppLog | None:
    """Add a message to the outbox. Returns None when WhatsApp is switched off or it is a duplicate."""
    if not is_enabled(session):
        return None
    if dedupe_key and session.scalars(select(WhatsAppLog.id).where(
            WhatsAppLog.payload["dedupe"].as_string() == dedupe_key)).first():
        return None
    template = get_templates(session)[kind]
    country = settings_service.get(session, "gym.country_code")
    entry = WhatsAppLog(
        member_id=member.id, to_phone=normalize_phone(member.phone, country), kind=kind,
        template_name=template["name"], status="queued",
        payload={"language": template.get("language") or settings_service.get(session, "whatsapp.language"),
                 "params": [context.get(p, "") for p in template["params"]],
                 "preview": render_preview(template, context), "dedupe": dedupe_key},
    )
    session.add(entry)
    session.flush()
    return entry


def queue_registration_message(session: Session, member: Member, sub: Subscription,
                               payment: Payment) -> WhatsAppLog | None:
    return queue_message(session, "registration", member, build_context(session, member, sub, payment),
                         dedupe_key=f"registration:{sub.id}")


def queue_payment_confirmation(session: Session, member: Member, sub: Subscription | None,
                               payment: Payment) -> WhatsAppLog | None:
    return queue_message(session, "payment_confirmation", member, build_context(session, member, sub, payment),
                         dedupe_key=f"payment:{payment.id}")


def queue_reminders(session: Session, today: date | None = None) -> int:
    """Queue expiry reminders (N days before) and renewal reminders (N days after). Idempotent per day."""
    if not is_enabled(session):
        return 0
    today = today or clock.today()
    queued = 0
    before = {int(d) for d in settings_service.get(session, "whatsapp.reminder_days")}
    after = {int(d) for d in settings_service.get(session, "whatsapp.renewal_reminder_days_after")}
    renewed = set(session.scalars(select(Subscription.renewed_from_id).where(
        Subscription.renewed_from_id.is_not(None), Subscription.status != "cancelled")))
    for sub in session.scalars(select(Subscription).where(
            Subscription.status == "active", Subscription.deleted_at.is_(None))):
        if sub.id in renewed:
            continue  # the member already renewed
        member = session.get(Member, sub.member_id)
        if member is None or not member.is_active or member.deleted_at is not None:
            continue
        days_left = (sub.end_date - today).days
        if days_left in before:
            kind, key = "expiry_reminder", f"expiry:{sub.id}:{days_left}"
        elif -days_left in after:
            kind, key = "renewal_reminder", f"renewal:{sub.id}:{-days_left}"
        else:
            continue
        if queue_message(session, kind, member, build_context(session, member, sub, None, today), key):
            queued += 1
    return queued


# ---- sending ------------------------------------------------------------------------------

_post: Callable[..., requests.Response] = requests.post  # replaced in tests
_get: Callable[..., requests.Response] = requests.get


def _is_retriable(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def _send_one(cfg: dict[str, str], to_phone: str, template_name: str, payload: dict) -> tuple[bool, str | None, str | None, bool]:
    """Returns (ok, provider_message_id, error, retriable)."""
    body = {
        "messaging_product": "whatsapp", "to": to_phone, "type": "template",
        "template": {
            "name": template_name, "language": {"code": payload.get("language") or cfg["language"]},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": p or "-"}
                                                           for p in payload.get("params", [])]}]
            if payload.get("params") else [],
        },
    }
    url = f"{GRAPH_URL}/{cfg['version']}/{cfg['phone_id']}/messages"
    try:
        resp = _post(url, json=body, headers={"Authorization": f"Bearer {cfg['token']}"}, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        return False, None, f"Network error: {exc}", True
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code == 200 and data.get("messages"):
        return True, data["messages"][0].get("id"), None, False
    detail = (data.get("error") or {}).get("message") or resp.text[:200]
    return False, None, f"HTTP {resp.status_code}: {detail}", _is_retriable(resp.status_code)


def process_queue(limit: int = 50, scope: Callable[[], AbstractContextManager[Session]] | None = None,
                  now=None) -> dict[str, int]:
    """Send queued messages (and failed ones due for retry). Safe to call repeatedly.

    Each row is committed on its own so a crash never loses or duplicates other rows' state.
    """
    if scope is None:
        from database.connection import session_scope as scope
    now = now or clock.now()
    result = {"sent": 0, "failed": 0, "skipped": 0}
    with scope() as s:
        if not is_enabled(s):
            return result
        cfg = _config(s)
        retry_before = now - timedelta(minutes=RETRY_BACKOFF_MINUTES)
        ids = list(s.scalars(select(WhatsAppLog.id).where(
            (WhatsAppLog.status == "queued") |
            ((WhatsAppLog.status == "failed") & (WhatsAppLog.attempts < MAX_ATTEMPTS)
             & (WhatsAppLog.updated_at < retry_before) & (WhatsAppLog.error.like("RETRY:%")))
        ).order_by(WhatsAppLog.created_at).limit(limit)))
    for log_id in ids:
        with scope() as s:
            entry = s.get(WhatsAppLog, log_id)
            if entry is None or entry.status not in ("queued", "failed"):
                result["skipped"] += 1
                continue
            ok, provider_id, error, retriable = _send_one(cfg, entry.to_phone, entry.template_name,
                                                          entry.payload or {})
            entry.attempts += 1
            entry.updated_at = clock.now()
            if ok:
                entry.status, entry.provider_message_id, entry.error = "sent", provider_id, None
                result["sent"] += 1
            else:
                entry.status = "failed"
                # "RETRY:" marks failures worth retrying automatically; others need the owner.
                entry.error = ("RETRY: " if retriable and entry.attempts < MAX_ATTEMPTS else "") + (error or "")
                result["failed"] += 1
                log.warning("WhatsApp send failed (%s): %s", entry.kind, error)
    return result


def retry_failed(session: Session) -> int:
    """Owner action: put every failed message back in the queue."""
    count = 0
    for entry in session.scalars(select(WhatsAppLog).where(WhatsAppLog.status == "failed")):
        entry.status, entry.attempts = "queued", 0
        entry.error = None
        count += 1
    session.flush()
    return count


def list_logs(session: Session, status: str | None = None, limit: int = 300) -> list[WhatsAppLog]:
    stmt = select(WhatsAppLog).order_by(WhatsAppLog.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(WhatsAppLog.status == status)
    return list(session.scalars(stmt))


def test_connection(session: Session) -> str:
    """Ask Meta for the number's display name; raises BusinessRuleError with the reason on failure."""
    cfg = _config(session)
    url = f"{GRAPH_URL}/{cfg['version']}/{cfg['phone_id']}"
    try:
        resp = _get(url, params={"fields": "display_phone_number,verified_name"},
                    headers={"Authorization": f"Bearer {cfg['token']}"}, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise BusinessRuleError(f"Could not reach WhatsApp Cloud API: {exc}") from exc
    data = resp.json() if resp.content else {}
    if resp.status_code != 200:
        raise BusinessRuleError(f"WhatsApp API said: {(data.get('error') or {}).get('message', resp.status_code)}")
    return f"{data.get('verified_name', '?')} ({data.get('display_phone_number', '?')})"
