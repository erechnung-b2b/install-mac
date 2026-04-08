"""
E-Rechnungssystem – Erweiterte Workflow-Funktionen
FR-260: Kontierungsvorschläge aus historischen Daten
FR-360: Stellvertretung und Abwesenheitsregeln
Bulk: Massenbearbeitung (Zuweisung, Statuswechsel, Exportwiederholung)
FR-530: Aufbewahrungsfristen und Löschregeln
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional
from collections import defaultdict
import json, uuid, logging, threading

from models import Invoice, InvoiceStatus

log = logging.getLogger("erechnung.advanced")


# ═══════════════════════════════════════════════════════════════════════
#  FR-260: Kontierungsvorschläge
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class AccountingSuggestion:
    """Ein Kontierungsvorschlag."""
    account: str = ""          # Sachkonto (z.B. "4900")
    cost_center: str = ""      # Kostenstelle
    project: str = ""          # Projekt
    confidence: float = 0.0    # 0.0–1.0
    source: str = ""           # "history", "rule", "default"
    based_on: str = ""         # Rechnungsnummer der Quelle


class AccountingSuggestionEngine:
    """
    Lernt aus historischen Kontierungen und schlägt für neue
    Rechnungen passende Konten/Kostenstellen vor.
    """

    def __init__(self):
        # Speichert: seller_name → [(account, cost_center, project, count)]
        self._history: dict[str, list[dict]] = defaultdict(list)
        # Manuelle Regeln: seller_name → fixed assignment
        self._rules: dict[str, dict] = {}

    def learn_from_invoice(self, inv: Invoice, account: str,
                           cost_center: str = "", project: str = ""):
        """Speichert eine Kontierung für zukünftige Vorschläge."""
        key = inv.seller.name.lower().strip()
        if not key:
            return

        # Existierenden Eintrag updaten oder neu anlegen
        for entry in self._history[key]:
            if (entry["account"] == account and
                entry["cost_center"] == cost_center and
                entry["project"] == project):
                entry["count"] += 1
                entry["last_used"] = datetime.now().isoformat()
                entry["last_invoice"] = inv.invoice_number
                return

        self._history[key].append({
            "account": account,
            "cost_center": cost_center,
            "project": project,
            "count": 1,
            "last_used": datetime.now().isoformat(),
            "last_invoice": inv.invoice_number,
        })

    def add_rule(self, seller_name: str, account: str,
                 cost_center: str = "", project: str = ""):
        """Feste Kontierungsregel für einen Lieferanten."""
        self._rules[seller_name.lower().strip()] = {
            "account": account,
            "cost_center": cost_center,
            "project": project,
        }

    def suggest(self, inv: Invoice) -> list[AccountingSuggestion]:
        """Gibt Kontierungsvorschläge für eine Rechnung zurück."""
        key = inv.seller.name.lower().strip()
        suggestions = []

        # 1. Feste Regel?
        if key in self._rules:
            r = self._rules[key]
            suggestions.append(AccountingSuggestion(
                account=r["account"], cost_center=r["cost_center"],
                project=r["project"], confidence=1.0, source="rule",
            ))
            return suggestions

        # 2. Aus Historie
        if key in self._history:
            entries = sorted(self._history[key], key=lambda e: e["count"], reverse=True)
            total = sum(e["count"] for e in entries)
            for entry in entries[:3]:  # Top 3
                conf = entry["count"] / total if total > 0 else 0
                suggestions.append(AccountingSuggestion(
                    account=entry["account"],
                    cost_center=entry["cost_center"],
                    project=entry["project"],
                    confidence=round(conf, 2),
                    source="history",
                    based_on=entry.get("last_invoice", ""),
                ))

        # 3. Default
        if not suggestions:
            suggestions.append(AccountingSuggestion(
                account="6300",  # Sonstige betriebliche Aufwendungen
                cost_center="ALLGEMEIN",
                confidence=0.1,
                source="default",
            ))

        return suggestions

    def get_stats(self) -> dict:
        total_suppliers = len(self._history)
        total_entries = sum(len(v) for v in self._history.values())
        total_rules = len(self._rules)
        return {
            "suppliers_with_history": total_suppliers,
            "total_entries": total_entries,
            "manual_rules": total_rules,
        }


# ═══════════════════════════════════════════════════════════════════════
#  FR-360: Stellvertretung
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class DeputyRule:
    """Abwesenheitsregel: Vorgänge werden automatisch umgeleitet."""
    rule_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    absent_user: str = ""
    deputy_user: str = ""
    start_date: date = field(default_factory=date.today)
    end_date: date = field(default_factory=lambda: date.today() + timedelta(days=7))
    active: bool = True
    reason: str = ""
    created_by: str = ""


class DeputyManager:
    """Verwaltet Stellvertreterregeln und leitet Vorgänge um."""

    def __init__(self):
        self.rules: list[DeputyRule] = []

    def add_rule(self, absent: str, deputy: str, start: date, end: date,
                 reason: str = "", created_by: str = "admin") -> DeputyRule:
        rule = DeputyRule(
            absent_user=absent, deputy_user=deputy,
            start_date=start, end_date=end,
            reason=reason, created_by=created_by,
        )
        self.rules.append(rule)
        log.info(f"Stellvertretung: {absent} → {deputy} ({start} bis {end})")
        return rule

    def remove_rule(self, rule_id: str) -> bool:
        before = len(self.rules)
        self.rules = [r for r in self.rules if r.rule_id != rule_id]
        return len(self.rules) < before

    def get_deputy(self, user: str, check_date: date = None) -> Optional[str]:
        """Gibt den aktiven Stellvertreter zurück, oder None."""
        d = check_date or date.today()
        for rule in self.rules:
            if (rule.active and rule.absent_user == user and
                rule.start_date <= d <= rule.end_date):
                return rule.deputy_user
        return None

    def redirect_invoice(self, inv: Invoice) -> tuple[bool, str]:
        """Prüft ob der zugewiesene Bearbeiter abwesend ist und leitet um."""
        if not inv.assigned_to:
            return False, ""

        deputy = self.get_deputy(inv.assigned_to)
        if deputy:
            old = inv.assigned_to
            inv.assigned_to = deputy
            inv.add_audit("STELLVERTRETUNG",
                          comment=f"Umgeleitet von {old} an {deputy} (Abwesenheit)")
            return True, f"{old} → {deputy}"
        return False, ""

    def get_active_rules(self) -> list[DeputyRule]:
        today = date.today()
        return [r for r in self.rules if r.active and r.start_date <= today <= r.end_date]

    def get_all_rules(self) -> list[dict]:
        return [{
            "rule_id": r.rule_id, "absent_user": r.absent_user,
            "deputy_user": r.deputy_user,
            "start_date": r.start_date.isoformat(), "end_date": r.end_date.isoformat(),
            "active": r.active, "reason": r.reason,
            "is_active_now": r.active and r.start_date <= date.today() <= r.end_date,
        } for r in self.rules]


# ═══════════════════════════════════════════════════════════════════════
#  Massenbearbeitung (Bulk Operations)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class BulkResult:
    action: str = ""
    total: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    details: list[dict] = field(default_factory=list)


class BulkProcessor:
    """Massenbearbeitung für sichere Vorgänge."""

    def __init__(self, workflow_engine=None, export_manager=None):
        self.wf = workflow_engine
        self.exporter = export_manager

    def bulk_assign(self, invoices: list[Invoice], assigned_to: str,
                    user: str = "system") -> BulkResult:
        """Massenhafte Zuweisung an einen Bearbeiter."""
        result = BulkResult(action="ZUWEISUNG", total=len(invoices))
        for inv in invoices:
            try:
                old = inv.assigned_to
                inv.assigned_to = assigned_to
                inv.add_audit("BULK_ZUWEISUNG", user,
                              f"Zugewiesen an {assigned_to}", old_value=old)
                result.success += 1
                result.details.append({"number": inv.invoice_number, "status": "OK"})
            except Exception as e:
                result.failed += 1
                result.details.append({"number": inv.invoice_number, "error": str(e)})
        return result

    def bulk_start_workflow(self, invoices: list[Invoice],
                           user: str = "system") -> BulkResult:
        """Startet Workflows für mehrere Rechnungen."""
        result = BulkResult(action="WORKFLOW_START", total=len(invoices))
        for inv in invoices:
            if inv.status != InvoiceStatus.NEU.value:
                result.skipped += 1
                result.details.append({"number": inv.invoice_number, "skipped": f"Status: {inv.status}"})
                continue
            try:
                if self.wf:
                    self.wf.start_workflow(inv, user)
                result.success += 1
                result.details.append({"number": inv.invoice_number, "status": inv.status})
            except Exception as e:
                result.failed += 1
                result.details.append({"number": inv.invoice_number, "error": str(e)})
        return result

    def bulk_export(self, invoices: list[Invoice],
                    format: str = "DATEV") -> BulkResult:
        """Exportiert mehrere freigegebene Rechnungen."""
        result = BulkResult(action=f"EXPORT_{format}", total=len(invoices))
        if not self.exporter:
            result.failed = len(invoices)
            return result

        for inv in invoices:
            if inv.status != InvoiceStatus.FREIGEGEBEN.value:
                result.skipped += 1
                result.details.append({"number": inv.invoice_number, "skipped": f"Status: {inv.status}"})
                continue
            try:
                res = self.exporter.export(inv, format)
                if res.success:
                    result.success += 1
                    result.details.append({"number": inv.invoice_number, "file": res.filename})
                else:
                    result.failed += 1
                    result.details.append({"number": inv.invoice_number, "error": res.error})
            except Exception as e:
                result.failed += 1
                result.details.append({"number": inv.invoice_number, "error": str(e)})
        return result

    def bulk_retry_export(self, invoices: list[Invoice],
                          format: str = "DATEV") -> BulkResult:
        """Wiederholt fehlgeschlagene Exporte."""
        result = BulkResult(action=f"RETRY_EXPORT_{format}", total=len(invoices))
        if not self.exporter:
            return result

        for inv in invoices:
            try:
                res = self.exporter.retry(inv, format)
                if res.success:
                    result.success += 1
                    result.details.append({"number": inv.invoice_number, "file": res.filename})
                else:
                    result.failed += 1
                    result.details.append({"number": inv.invoice_number, "error": res.error})
            except Exception as e:
                result.failed += 1
                result.details.append({"number": inv.invoice_number, "error": str(e)})
        return result


# ═══════════════════════════════════════════════════════════════════════
#  FR-530: Aufbewahrungsfristen
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class RetentionPolicy:
    """Aufbewahrungsfrist pro Mandant/Dokumenttyp."""
    policy_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Standard"
    retention_years: int = 10  # §14b UStG: 8 Jahre + Sicherheit
    document_type: str = ""    # Leer = alle
    locked: bool = False       # Sperrkennzeichen
    auto_delete: bool = False  # Automatisches Löschen nach Ablauf


class RetentionManager:
    """Verwaltet Aufbewahrungsfristen und Sperrkennzeichen."""

    def __init__(self):
        self.policies: list[RetentionPolicy] = [
            RetentionPolicy(name="Rechnungen (§14b UStG)", retention_years=10),
        ]
        self.locked_invoices: set[str] = set()  # invoice_ids

    def is_deletable(self, inv: Invoice) -> tuple[bool, str]:
        """Prüft ob eine Rechnung gelöscht werden darf."""
        # Gesperrt?
        if inv._id in self.locked_invoices:
            return False, "Rechnung ist gesperrt (Sperrkennzeichen aktiv)"

        # Aufbewahrungsfrist
        policy = self._get_policy(inv)
        if not policy:
            return False, "Keine Aufbewahrungsrichtlinie gefunden"

        cutoff = date.today() - timedelta(days=policy.retention_years * 365)
        if inv.invoice_date > cutoff:
            remaining = (inv.invoice_date + timedelta(days=policy.retention_years * 365) - date.today()).days
            return False, f"Aufbewahrungsfrist läuft noch {remaining} Tage (bis {inv.invoice_date + timedelta(days=policy.retention_years * 365)})"

        return True, "Aufbewahrungsfrist abgelaufen"

    def lock(self, invoice_id: str):
        self.locked_invoices.add(invoice_id)

    def unlock(self, invoice_id: str):
        self.locked_invoices.discard(invoice_id)

    def is_locked(self, invoice_id: str) -> bool:
        return invoice_id in self.locked_invoices

    def _get_policy(self, inv: Invoice) -> Optional[RetentionPolicy]:
        for p in self.policies:
            if not p.document_type or p.document_type == inv.invoice_type_code:
                return p
        return self.policies[0] if self.policies else None

    def check_all(self, invoices: list[Invoice]) -> list[dict]:
        """Prüft alle Rechnungen auf Löschbarkeit."""
        results = []
        for inv in invoices:
            deletable, reason = self.is_deletable(inv)
            results.append({
                "invoice_number": inv.invoice_number,
                "invoice_date": inv.invoice_date.isoformat(),
                "deletable": deletable,
                "locked": inv._id in self.locked_invoices,
                "reason": reason,
            })
        return results


# ═══════════════════════════════════════════════════════════════════════
#  IMAP Background Polling
# ═══════════════════════════════════════════════════════════════════════

class BackgroundPoller:
    """Hintergrund-Thread für IMAP-Polling."""

    def __init__(self, email_receiver, on_new_invoice=None):
        self.receiver = email_receiver
        self.on_new_invoice = on_new_invoice
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        self.poll_count = 0
        self.last_poll: Optional[str] = None
        self.last_result: Optional[dict] = None

    def start(self, interval_seconds: int = 60):
        """Startet den Polling-Thread."""
        if self._running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, args=(interval_seconds,), daemon=True)
        self._thread.start()
        self._running = True
        log.info(f"IMAP-Polling gestartet (alle {interval_seconds}s)")

    def stop(self):
        """Stoppt den Polling-Thread."""
        self._stop_event.set()
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        log.info("IMAP-Polling gestoppt")

    def _loop(self, interval: int):
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception as e:
                log.error(f"Polling-Fehler: {e}")
            self._stop_event.wait(interval)

    def _poll_once(self):
        self.poll_count += 1
        self.last_poll = datetime.now().isoformat()

        try:
            self.receiver.connect()
            receipts = self.receiver.fetch_new_invoices()
            self.last_result = {
                "timestamp": self.last_poll,
                "messages": len(receipts),
                "invoices": sum(len(r.invoice_numbers) for r in receipts),
            }

            if receipts and self.on_new_invoice:
                for r in receipts:
                    self.on_new_invoice(r)

        except Exception as e:
            self.last_result = {"timestamp": self.last_poll, "error": str(e)}
            self.receiver.disconnect()

    @property
    def is_running(self) -> bool:
        return self._running

    def status(self) -> dict:
        return {
            "running": self._running,
            "poll_count": self.poll_count,
            "last_poll": self.last_poll,
            "last_result": self.last_result,
        }
