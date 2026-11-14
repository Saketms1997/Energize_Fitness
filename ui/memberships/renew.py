from __future__ import annotations

import uuid

from PySide6.QtWidgets import QWidget

from database.connection import session_scope
from services import member_service, membership_service, plan_service
from ui.common import money, notify
from ui.payments.payment_dialog import CollectPaymentDialog
from ui.widgets.forms import Field, ask_form


def renew_member(parent: QWidget | None, member_id: uuid.UUID) -> bool:
    """Choose a plan, create the renewal, then offer to collect the payment."""
    with session_scope() as s:
        name = member_service.get_member(s, member_id).full_name
        plans = [(p.id, p.name, p.duration_days, p.price) for p in plan_service.list_plans(s, True)]
        history = membership_service.list_subscriptions(s, member_id=member_id)
    if not plans:
        notify("No active membership plans. Add one in Settings.", "error")
        return False
    labels = [f"{n} - {d} days - {money(pr)}" for _, n, d, pr in plans]
    last_plan = next((r.plan_name for r in reversed(history) if r.state != "cancelled"), None)
    default = next((label for label, p in zip(labels, plans) if p[1] == last_plan), labels[0])
    created: dict = {}

    def submit(v: dict) -> None:
        plan_id = plans[labels.index(v["plan"])][0]
        with session_scope() as s:
            sub = membership_service.renew_subscription(s, member_id, plan_id, amount_due=v["amount"] or None)
            created.update(id=sub.id, start=sub.start_date, end=sub.end_date)

    fields = [Field("plan", "Plan", "choice", True, default, labels),
              Field("amount", "Amount due (blank = plan price)", placeholder="e.g. 900.00")]
    ok, _ = ask_form(parent, f"Renew membership - {name}", fields, submit, "Renew",
                     "The renewal starts the day after the current membership ends (or today if it has lapsed).")
    if not ok:
        return False
    notify(f"Renewed: {created['start']:%d %b} to {created['end']:%d %b %Y}. Collect the payment to activate it.", "info")
    CollectPaymentDialog(created["id"], parent).exec()
    return True
