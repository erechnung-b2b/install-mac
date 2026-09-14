"""
E-Rechnungssystem – Benachrichtigungen
FR-640: E-Mail / In-App Benachrichtigungen bei Statusereignissen
FR-650: Webhook-Integration für externes Monitoring

Konfigurierbare Auslöser pro Mandant/Benutzer.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Callable
import json, logging, threading
from pathlib import Path

from models import Invoice

log = logging.getLogger("erechnung.notifications")


# ── Benachrichtigungstypen ─────────────────────────────────────────────

class NotificationType:
    NEUE_RECHNUNG = "NEUE_RECHNUNG"
    VALIDIERUNG_FEHLER = "VALIDIERUNG_FEHLER"
    FREIGABE_ERFORDERLICH = "FREIGABE_ERFORDERLICH"
    FREIGABE_ERTEILT = "FREIGABE_ERTEILT"
    ZURUECKGEWIESEN = "ZURUECKGEWIESEN"
    EXPORT_ERFOLGT = "EXPORT_ERFOLGT"
    EXPORT_FEHLER = "EXPORT_FEHLER"
    ESKALATION = "ESKALATION"
    DUBLETTE = "DUBLETTE"
    EMAIL_VERSANDT = "EMAIL_VERSANDT"
    EMAIL_EMPFANGEN = "EMAIL_EMPFANGEN"


@dataclass
class Notification:
    notification_id: str = ""
    type: str = ""
    title: str = ""
    message: str = ""
    invoice_number: str = ""
    invoice_id: str = ""
    recipient: str = ""
    channel: str = "in_app"  # in_app, email, webhook
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    read: bool = False
    sent: bool = False
    error: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class NotificationRule:
    """Konfigurierbare Benachrichtigungsregel."""
    rule_id: str = ""
    event_type: str = ""
    channel: str = "in_app"  # in_app, email, webhook
    recipient: str = ""      # username, email oder webhook-URL
    enabled: bool = True
    filter_min_amount: float = 0
    filter_status: str = ""


@dataclass
class WebhookConfig:
    """FR-650: Webhook-Endpunkt für externes Monitoring."""
    url: str = ""
    secret: str = ""
    events: list[str] = field(default_factory=list)
    enabled: bool = True
    retry_count: int = 3
    timeout_seconds: int = 10


# ── Notification-Engine ────────────────────────────────────────────────

class NotificationEngine:
    """Zentrale Steuerung für alle Benachrichtigungen."""

    def __init__(self):
        self.notifications: list[Notification] = []
        self.rules: list[NotificationRule] = []
        self.webhooks: list[WebhookConfig] = []
        self._email_callback: Optional[Callable] = None
        self._max_notifications = 500

        # Standard-Regeln
        self.rules = [
            NotificationRule("r1", NotificationType.NEUE_RECHNUNG, "in_app", "buchhaltung"),
            NotificationRule("r2", NotificationType.VALIDIERUNG_FEHLER, "in_app", "buchhaltung"),
            NotificationRule("r3", NotificationType.FREIGABE_ERFORDERLICH, "in_app", "freigeber"),
            NotificationRule("r4", NotificationType.ZURUECKGEWIESEN, "in_app", "buchhaltung"),
            NotificationRule("r5", NotificationType.ESKALATION, "in_app", "admin"),
            NotificationRule("r6", NotificationType.EXPORT_FEHLER, "in_app", "admin"),
            NotificationRule("r7", NotificationType.DUBLETTE, "in_app", "buchhaltung"),
        ]

    def set_email_callback(self, callback: Callable):
        """Setzt die E-Mail-Sendefunktion (aus email_handler)."""
        self._email_callback = callback

    # ── Events auslösen ────────────────────────────────────────

    def notify_new_invoice(self, inv: Invoice, valid: bool, errors: int = 0):
        self._create(
            NotificationType.NEUE_RECHNUNG,
            f"Neue Rechnung: {inv.invoice_number}",
            f"Von {inv.seller.name}, {inv.tax_inclusive_amount():.2f} {inv.currency_code}"
            + (f" – {errors} Validierungsfehler" if not valid else " – Validierung OK"),
            inv,
        )
        if not valid:
            self._create(
                NotificationType.VALIDIERUNG_FEHLER,
                f"Validierungsfehler: {inv.invoice_number}",
                f"{errors} Fehler in Rechnung {inv.invoice_number} von {inv.seller.name}",
                inv,
            )

    def notify_approval_needed(self, inv: Invoice, role: str):
        self._create(
            NotificationType.FREIGABE_ERFORDERLICH,
            f"Freigabe erforderlich: {inv.invoice_number}",
            f"Rechnung {inv.invoice_number} ({inv.tax_inclusive_amount():.2f} {inv.currency_code}) wartet auf {role}",
            inv,
        )

    def notify_approved(self, inv: Invoice, user: str):
        self._create(
            NotificationType.FREIGABE_ERTEILT,
            f"Freigegeben: {inv.invoice_number}",
            f"Rechnung {inv.invoice_number} wurde von {user} freigegeben",
            inv,
        )

    def notify_rejected(self, inv: Invoice, user: str, reason: str):
        self._create(
            NotificationType.ZURUECKGEWIESEN,
            f"Zurückgewiesen: {inv.invoice_number}",
            f"Rechnung {inv.invoice_number} von {user} abgelehnt: {reason}",
            inv,
        )

    def notify_exported(self, inv: Invoice, format: str, filename: str):
        self._create(
            NotificationType.EXPORT_ERFOLGT,
            f"Export: {inv.invoice_number}",
            f"Rechnung als {format} exportiert: {filename}",
            inv,
        )

    def notify_export_error(self, inv: Invoice, error: str):
        self._create(
            NotificationType.EXPORT_FEHLER,
            f"Export-Fehler: {inv.invoice_number}",
            f"Export fehlgeschlagen: {error}",
            inv,
        )

    def notify_escalation(self, inv: Invoice, days: int, limit: int):
        self._create(
            NotificationType.ESKALATION,
            f"Eskalation: {inv.invoice_number}",
            f"Rechnung seit {days} Tagen offen (Limit: {limit} Tage)",
            inv,
        )

    def notify_duplicate(self, inv: Invoice, original_id: str):
        self._create(
            NotificationType.DUBLETTE,
            f"Dublette: {inv.invoice_number}",
            f"Verdacht auf Dublette zu {original_id}",
            inv,
        )

    # ── Interne Erstellung ─────────────────────────────────────

    def _create(self, event_type: str, title: str, message: str, inv: Invoice):
        """Erstellt Benachrichtigungen für alle passenden Regeln."""
        import uuid
        for rule in self.rules:
            if not rule.enabled or rule.event_type != event_type:
                continue
            if rule.filter_min_amount and float(inv.tax_inclusive_amount()) < rule.filter_min_amount:
                continue

            notif = Notification(
                notification_id=str(uuid.uuid4()),
                type=event_type,
                title=title,
                message=message,
                invoice_number=inv.invoice_number,
                invoice_id=inv._id,
                recipient=rule.recipient,
                channel=rule.channel,
            )

            if rule.channel == "email" and self._email_callback:
                try:
                    self._email_callback(rule.recipient, title, message)
                    notif.sent = True
                except Exception as e:
                    notif.error = str(e)
            elif rule.channel == "in_app":
                notif.sent = True

            self.notifications.append(notif)

        # Webhooks
        self._fire_webhooks(event_type, title, message, inv)

        # Alte Benachrichtigungen aufräumen
        if len(self.notifications) > self._max_notifications:
            self.notifications = self.notifications[-self._max_notifications:]

    def _fire_webhooks(self, event_type: str, title: str, message: str, inv: Invoice):
        """FR-650: Sendet Webhook-Events."""
        for wh in self.webhooks:
            if not wh.enabled:
                continue
            if wh.events and event_type not in wh.events:
                continue

            payload = {
                "event": event_type,
                "title": title,
                "message": message,
                "invoice_number": inv.invoice_number,
                "invoice_id": inv._id,
                "amount": float(inv.tax_inclusive_amount()),
                "status": inv.status,
                "timestamp": datetime.now().isoformat(),
            }

            # In Produktion: requests.post() mit retry
            log.info(f"Webhook → {wh.url}: {event_type} ({inv.invoice_number})")

    # ── Abfragen ───────────────────────────────────────────────

    def get_unread(self, recipient: str = "") -> list[Notification]:
        return [n for n in self.notifications
                if not n.read and (not recipient or n.recipient == recipient)]

    def get_all(self, limit: int = 50, recipient: str = "") -> list[Notification]:
        filtered = self.notifications if not recipient else [
            n for n in self.notifications if n.recipient == recipient]
        return list(reversed(filtered[-limit:]))

    def mark_read(self, notification_id: str):
        for n in self.notifications:
            if n.notification_id == notification_id:
                n.read = True
                return True
        return False

    def mark_all_read(self, recipient: str = ""):
        for n in self.notifications:
            if not recipient or n.recipient == recipient:
                n.read = True

    def unread_count(self, recipient: str = "") -> int:
        return len(self.get_unread(recipient))
