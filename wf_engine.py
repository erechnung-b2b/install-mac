"""
E-Rechnungssystem – Workflow & Freigabe
FR-300..FR-360: Status, Rollen, Betragsgrenzen, Vier-Augen, Eskalation
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional
from models import Invoice, InvoiceStatus, WorkflowAction


@dataclass
class WorkflowRule:
    """Konfigurierbare Freigaberegel pro Mandant (FR-340)"""
    name: str = ""
    min_amount: Decimal = Decimal("0.00")
    max_amount: Decimal = Decimal("999999999.99")
    required_role: str = "buchhaltung"
    four_eyes: bool = False
    escalation_days: int = 5


DEFAULT_RULES = [
    WorkflowRule("Kleinbetrag", Decimal("0"), Decimal("500"), "buchhaltung", False, 7),
    WorkflowRule("Standard", Decimal("500.01"), Decimal("10000"), "buchhaltung", True, 5),
    WorkflowRule("Großbetrag", Decimal("10000.01"), Decimal("999999999"), "geschaeftsfuehrung", True, 3),
]


@dataclass
class WorkflowStep:
    action: str
    user: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    comment: str = ""
    decision: str = ""  # GENEHMIGT, ABGELEHNT, RUECKFRAGE


class WorkflowEngine:
    """Steuerung des Freigabeworkflows für Eingangsrechnungen."""

    def __init__(self, rules: list[WorkflowRule] = None):
        self.rules = rules or DEFAULT_RULES

    def _find_rule(self, amount: Decimal) -> WorkflowRule:
        for r in self.rules:
            if r.min_amount <= amount <= r.max_amount:
                return r
        return self.rules[-1]

    def start_workflow(self, inv: Invoice, user: str = "system") -> str:
        """Setzt Rechnung in den Workflow (FR-300)."""
        rule = self._find_rule(inv.amount_due())
        inv.status = InvoiceStatus.IN_PRUEFUNG.value
        inv.assigned_to = rule.required_role
        inv.add_audit("WORKFLOW_GESTARTET", user,
                      f"Regel: {rule.name}, zugewiesen an: {rule.required_role}")
        return f"Workflow gestartet → {rule.name} → zugewiesen an {rule.required_role}"

    def sachliche_pruefung(self, inv: Invoice, user: str, approved: bool, comment: str = "") -> str:
        """FR-310: Sachliche Prüfung durch Fachabteilung."""
        if inv.status != InvoiceStatus.IN_PRUEFUNG.value:
            return f"Fehler: Status ist {inv.status}, erwartet IN_PRUEFUNG"

        if approved:
            rule = self._find_rule(inv.amount_due())
            if rule.four_eyes:
                inv.status = InvoiceStatus.IN_FREIGABE.value
                inv.add_audit("SACHLICHE_PRUEFUNG_OK", user, comment)
                return "Sachlich geprüft ✓ → Warte auf kaufmännische Freigabe (Vier-Augen)"
            else:
                inv.status = InvoiceStatus.FREIGEGEBEN.value
                inv.add_audit("FREIGABE_ERTEILT", user, comment)
                return "Sachlich geprüft und freigegeben ✓"
        else:
            inv.status = InvoiceStatus.ZURUECKGEWIESEN.value
            inv.add_audit("SACHLICHE_PRUEFUNG_ABGELEHNT", user, comment)
            return f"Abgelehnt: {comment}"

    def kaufmaennische_freigabe(self, inv: Invoice, user: str, approved: bool, comment: str = "") -> str:
        """FR-310/FR-340: Kaufmännische Freigabe (zweiter Schritt bei Vier-Augen)."""
        if inv.status != InvoiceStatus.IN_FREIGABE.value:
            return f"Fehler: Status ist {inv.status}, erwartet IN_FREIGABE"

        if approved:
            inv.status = InvoiceStatus.FREIGEGEBEN.value
            inv.add_audit("KAUFMAENNISCHE_FREIGABE", user, comment)
            return "Kaufmännisch freigegeben ✓ → Bereit für Export"
        else:
            inv.status = InvoiceStatus.ZURUECKGEWIESEN.value
            inv.add_audit("KAUFMAENNISCHE_FREIGABE_ABGELEHNT", user, comment)
            return f"Abgelehnt: {comment}"

    def zurueckweisen(self, inv: Invoice, user: str, comment: str) -> str:
        """FR-330: Ablehnung mit Kommentar."""
        old = inv.status
        inv.status = InvoiceStatus.ZURUECKGEWIESEN.value
        inv.add_audit("ZURUECKGEWIESEN", user, comment, old_value=old)
        return f"Zurückgewiesen: {comment}"

    def rueckfrage(self, inv: Invoice, user: str, question: str) -> str:
        """FR-330: Rückfrage stellen."""
        inv.add_audit("RUECKFRAGE", user, question)
        return f"Rückfrage gestellt: {question}"

    def wiedervorlage(self, inv: Invoice, user: str, days: int = 3, comment: str = "") -> str:
        """FR-350: Wiedervorlage setzen."""
        target_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        inv.add_audit("WIEDERVORLAGE", user, f"Wiedervorlage am {target_date}: {comment}")
        return f"Wiedervorlage am {target_date}"

    def mark_exported(self, inv: Invoice, export_target: str = "") -> str:
        """Nach erfolgreichem Export."""
        if inv.status != InvoiceStatus.FREIGEGEBEN.value:
            return f"Fehler: Nur freigegebene Rechnungen können exportiert werden (Status: {inv.status})"
        inv.status = InvoiceStatus.EXPORTIERT.value
        inv.add_audit("EXPORTIERT", comment=f"Ziel: {export_target}")
        return "Als exportiert markiert ✓"

    def check_escalations(self, invoices: list[Invoice], max_days: int = None) -> list[tuple[Invoice, str]]:
        """FR-350: Prüft überfällige Vorgänge."""
        escalations = []
        now = datetime.now()
        for inv in invoices:
            if inv.status not in (InvoiceStatus.IN_PRUEFUNG.value, InvoiceStatus.IN_FREIGABE.value):
                continue
            rule = self._find_rule(inv.amount_due())
            limit = max_days or rule.escalation_days
            # Prüfe wann der letzte Workflow-Schritt war
            last_action = None
            for evt in reversed(inv.audit_trail):
                if evt.event_type.startswith("WORKFLOW") or evt.event_type.startswith("SACHLICHE"):
                    last_action = datetime.fromisoformat(evt.timestamp)
                    break
            if last_action:
                days_open = (now - last_action).days
                if days_open >= limit:
                    escalations.append((inv, f"Überfällig seit {days_open} Tagen (Limit: {limit})"))

        return escalations
