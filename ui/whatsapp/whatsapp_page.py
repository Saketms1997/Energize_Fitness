from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QMessageBox, QTabWidget, QVBoxLayout, QWidget

from database.connection import session_scope
from services import whatsapp_service as wa
from ui.async_utils import run_async
from ui.common import notify, show_error
from ui.widgets.forms import Field, ask_form
from ui.widgets.page import Page, button_row, make_button
from ui.widgets.table import DataTable

KIND_LABELS = {"registration": "Registration confirmation", "payment_confirmation": "Payment confirmation",
               "expiry_reminder": "Membership expiry reminder", "renewal_reminder": "Renewal reminder"}
PLACEHOLDERS = "member_name, gym_name, plan_name, start_date, expiry_date, amount_paid, amount_due, receipt_no, days_left"


class WhatsAppPage(Page):
    def __init__(self, parent=None):
        super().__init__("WhatsApp", "Messages are sent through Meta's official WhatsApp Cloud API using approved templates.", parent)
        self.send_btn = self.add_action("Send queued now", self.send_now)
        self.add_action("Queue reminders", self.queue_reminders, "secondary")
        self.add_action("Retry failed", self.retry, "secondary")
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.root.addWidget(self.status)
        self.tabs = QTabWidget()
        self.root.addWidget(self.tabs, 1)

        logs = QWidget()
        ll = QVBoxLayout(logs)
        bar = QHBoxLayout()
        self.filter = QComboBox()
        for label, value in (("All", None), ("Queued", "queued"), ("Sent", "sent"), ("Failed", "failed")):
            self.filter.addItem(label, value)
        bar.addWidget(QLabel("Show"))
        bar.addWidget(self.filter)
        bar.addStretch(1)
        ll.addLayout(bar)
        self.logs = DataTable(["When", "To", "Type", "Template", "Status", "Tries", "Message / error"], stretch=6)
        ll.addWidget(self.logs)
        ll.addWidget(QLabel("Sent = accepted by WhatsApp. Delivery/read receipts are not tracked by this desktop app."))
        self.tabs.addTab(logs, "Delivery log")

        templates = QWidget()
        tl = QVBoxLayout(templates)
        self.templates = DataTable(["Message", "Template name", "Language", "Parameters (in order)", "Preview"], stretch=4)
        tl.addWidget(self.templates)
        tl.addLayout(button_row(make_button("Edit template...", self.edit_template)))
        hint = QLabel("Template names must match templates approved in Meta Business Manager, with the same "
                      f"number of {{{{1}}}}, {{{{2}}}}... variables. Available values: {PLACEHOLDERS}.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        tl.addWidget(hint)
        self.tabs.addTab(templates, "Message templates")
        self.filter.currentIndexChanged.connect(lambda _: self.refresh())
        self.logs.row_activated.connect(self.show_message)
        self._logs: dict = {}

    def refresh(self) -> None:
        try:
            with session_scope() as s:
                enabled, templates = wa.is_enabled(s), wa.get_templates(s)
                logs = wa.list_logs(s, self.filter.currentData())
                counts = {st: len(wa.list_logs(s, st, 100000)) for st in ("queued", "sent", "failed")}
                rows = [(m.id, [m.created_at, m.to_phone, KIND_LABELS.get(m.kind, m.kind), m.template_name, m.status,
                                m.attempts, m.error or (m.payload or {}).get("preview", "")]) for m in logs]
                self._logs = {m.id: ((m.payload or {}).get("preview", ""), m.error) for m in logs}
        except Exception as exc:  # noqa: BLE001
            return show_error(self, exc)
        self.status.setText(("WhatsApp sending is <b>ON</b>." if enabled else
                             "WhatsApp sending is <b>OFF</b> - turn it on and add credentials in Settings > WhatsApp.") +
                            f"   Queued {counts['queued']}  |  Sent {counts['sent']}  |  Failed {counts['failed']}")
        self.logs.set_rows(rows)
        self.templates.set_rows([(k, [KIND_LABELS[k], t["name"], t.get("language") or "default",
                                      ", ".join(t["params"]), t["preview"]]) for k, t in templates.items()])

    def show_message(self, log_id) -> None:
        preview, error = self._logs.get(log_id, ("", None))
        QMessageBox.information(self, "Message", f"{preview}\n\n{'Error: ' + error if error else ''}".strip())

    def _run(self, fn, done_text) -> None:
        self.send_btn.setEnabled(False)

        def done(result) -> None:
            self.send_btn.setEnabled(True)
            notify(done_text(result))
            self.refresh()

        run_async(fn, done, lambda exc: (self.send_btn.setEnabled(True), show_error(self, exc, "WhatsApp")))

    def send_now(self) -> None:
        self._run(wa.process_queue, lambda r: f"Sent {r['sent']}, failed {r['failed']}.")

    def queue_reminders(self) -> None:
        def work():
            with session_scope() as s:
                n = wa.queue_reminders(s)
            return n, wa.process_queue()

        self._run(work, lambda r: f"{r[0]} reminder(s) queued; sent {r[1]['sent']}, failed {r[1]['failed']}.")

    def retry(self) -> None:
        with session_scope() as s:
            n = wa.retry_failed(s)
        notify(f"{n} message(s) re-queued.", "info")
        self.send_now() if n else self.refresh()

    def edit_template(self) -> None:
        kind = self.templates.selected_id()
        if kind is None:
            return notify("Select a message first.", "info")
        with session_scope() as s:
            t = wa.get_templates(s)[kind]

        def submit(v: dict) -> None:
            params = [p.strip() for p in v["params"].split(",") if p.strip()]
            with session_scope() as s:
                wa.save_template(s, kind, v["name"], params, v["preview"], v["language"].strip() or None)

        fields = [Field("name", "Template name", required=True, value=t["name"]),
                  Field("language", "Language code", value=t.get("language") or "", placeholder="en, en_US, hi..."),
                  Field("params", "Parameters, in order", required=True, value=", ".join(t["params"])),
                  Field("preview", "Preview text", "multiline", True, t["preview"])]
        ok, _ = ask_form(self, KIND_LABELS[kind], fields, submit, intro=f"Available values: {PLACEHOLDERS}")
        if ok:
            notify("Template saved.")
            self.refresh()
