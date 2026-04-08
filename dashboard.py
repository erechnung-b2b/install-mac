"""
E-Rechnungssystem – Dashboard & KPIs
FR-630 / Abschnitt 12.3: Eingangsvolumen, Fehlerquoten, Durchlaufzeiten, offene Freigaben
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional
from collections import Counter

from models import Invoice, InvoiceStatus
from validator import ValidationReport
from inbox import InboxItem


@dataclass
class KPIResult:
    name: str
    value: str
    unit: str = ""
    trend: str = ""  # ↑ ↓ →
    detail: str = ""


class Dashboard:
    """Berechnet KPIs aus Pflichtenheft Abschnitt 12.3"""

    def __init__(self):
        self.invoices: list[Invoice] = []
        self.inbox_items: list[InboxItem] = []
        self.export_log: list[dict] = []

    def add_invoice(self, inv: Invoice):
        self.invoices.append(inv)

    def add_inbox_item(self, item: InboxItem):
        self.inbox_items.append(item)

    def set_export_log(self, log: list[dict]):
        self.export_log = log

    # ── KPI-Berechnungen ───────────────────────────────────────

    def durchlaufzeit(self) -> KPIResult:
        """Zeit vom Eingang bis zur finalen Freigabe/Ablehnung."""
        durations = []
        for inv in self.invoices:
            eingang = None
            abschluss = None
            for evt in inv.audit_trail:
                ts = datetime.fromisoformat(evt.timestamp)
                if evt.event_type == "EINGANG_VERARBEITET" and eingang is None:
                    eingang = ts
                if evt.event_type in ("FREIGABE_ERTEILT", "KAUFMAENNISCHE_FREIGABE",
                                      "ZURUECKGEWIESEN", "EXPORTIERT"):
                    abschluss = ts
            if eingang and abschluss:
                durations.append((abschluss - eingang).total_seconds() / 3600)

        if durations:
            avg = sum(durations) / len(durations)
            return KPIResult("Ø Durchlaufzeit", f"{avg:.1f}", "Stunden",
                             detail=f"Min: {min(durations):.1f}h, Max: {max(durations):.1f}h, n={len(durations)}")
        return KPIResult("Ø Durchlaufzeit", "—", "Stunden", detail="Keine abgeschlossenen Vorgänge")

    def touchless_rate(self) -> KPIResult:
        """Anteil automatisiert verarbeiteter Rechnungen ohne manuelle Korrektur."""
        if not self.invoices:
            return KPIResult("Touchless-Rate", "—", "%")

        touchless = 0
        for inv in self.invoices:
            manual_actions = [e for e in inv.audit_trail
                              if e.event_type in ("MANUELLE_KORREKTUR", "FELD_GEAENDERT")]
            if not manual_actions:
                touchless += 1

        rate = (touchless / len(self.invoices)) * 100
        return KPIResult("Touchless-Rate", f"{rate:.1f}", "%",
                         detail=f"{touchless} von {len(self.invoices)} ohne manuelle Korrektur")

    def validierungsfehlerquote(self) -> KPIResult:
        """Anteil Rechnungen mit technischen oder fachlichen Fehlern."""
        if not self.inbox_items:
            return KPIResult("Validierungsfehlerquote", "—", "%")

        with_errors = sum(1 for item in self.inbox_items
                          if item.validation and not item.validation.is_valid)
        total = sum(1 for item in self.inbox_items if item.validation)

        if total == 0:
            return KPIResult("Validierungsfehlerquote", "—", "%")

        rate = (with_errors / total) * 100
        return KPIResult("Validierungsfehlerquote", f"{rate:.1f}", "%",
                         detail=f"{with_errors} von {total} mit Fehlern")

    def export_fehlerquote(self) -> KPIResult:
        """Anteil fehlerhafter Übergaben an Zielsysteme."""
        if not self.export_log:
            return KPIResult("Export-Fehlerquote", "—", "%")

        failed = sum(1 for e in self.export_log if not e.get("success"))
        rate = (failed / len(self.export_log)) * 100
        return KPIResult("Export-Fehlerquote", f"{rate:.1f}", "%",
                         detail=f"{failed} von {len(self.export_log)} fehlerhaft")

    def offene_ueberfaellige(self, max_days: int = 5) -> KPIResult:
        """Anzahl eskalierter oder über SLA liegender Vorgänge."""
        now = datetime.now()
        overdue = 0
        open_count = 0

        for inv in self.invoices:
            if inv.status in (InvoiceStatus.IN_PRUEFUNG.value, InvoiceStatus.IN_FREIGABE.value):
                open_count += 1
                first_event = inv.audit_trail[0].timestamp if inv.audit_trail else None
                if first_event:
                    age = (now - datetime.fromisoformat(first_event)).days
                    if age >= max_days:
                        overdue += 1

        return KPIResult("Offene / Überfällige", f"{open_count} / {overdue}", "Vorgänge",
                         detail=f"Offen: {open_count}, davon überfällig (>{max_days}d): {overdue}")

    def dublettenquote(self) -> KPIResult:
        """Anzahl erkannter Dubletten."""
        if not self.inbox_items:
            return KPIResult("Dublettenquote", "—", "")

        dups = sum(1 for item in self.inbox_items if item.status == "DUPLIKAT")
        return KPIResult("Dubletten", f"{dups}", "erkannt",
                         detail=f"{dups} von {len(self.inbox_items)} Eingängen")

    def eingangsvolumen(self) -> KPIResult:
        """Gesamtzahl eingegangener Rechnungen."""
        total = len(self.inbox_items)
        ok = sum(1 for i in self.inbox_items if i.status == "VERARBEITET")
        return KPIResult("Eingangsvolumen", f"{total}", "Rechnungen",
                         detail=f"{ok} verarbeitet, {total - ok} abgelehnt/Dubletten")

    def betrag_summen(self) -> KPIResult:
        """Summen über alle Rechnungen."""
        total = sum((inv.tax_inclusive_amount() for inv in self.invoices), Decimal("0"))
        return KPIResult("Gesamtvolumen", f"{total:.2f}", "EUR",
                         detail=f"{len(self.invoices)} Rechnungen")

    def status_verteilung(self) -> KPIResult:
        """Verteilung der Rechnungen nach Status."""
        counter = Counter(inv.status for inv in self.invoices)
        parts = [f"{status}: {count}" for status, count in sorted(counter.items())]
        return KPIResult("Status-Verteilung", ", ".join(parts), "",
                         detail=f"{len(self.invoices)} Rechnungen total")

    # ── Gesamtübersicht ────────────────────────────────────────

    def compute_all(self) -> list[KPIResult]:
        return [
            self.eingangsvolumen(),
            self.betrag_summen(),
            self.durchlaufzeit(),
            self.touchless_rate(),
            self.validierungsfehlerquote(),
            self.export_fehlerquote(),
            self.offene_ueberfaellige(),
            self.dublettenquote(),
            self.status_verteilung(),
        ]

    def render(self) -> str:
        """Textbasiertes Dashboard."""
        kpis = self.compute_all()
        lines = [
            "╔══════════════════════════════════════════════════════════════════╗",
            "║                    E-RECHNUNGS-DASHBOARD                       ║",
            f"║  Stand: {datetime.now().strftime('%d.%m.%Y %H:%M')}                                      ║",
            "╠══════════════════════════════════════════════════════════════════╣",
        ]
        for kpi in kpis:
            value_str = f"{kpi.value} {kpi.unit}".strip()
            lines.append(f"║  {kpi.name:<28} {value_str:>32}  ║")
            if kpi.detail:
                lines.append(f"║    {kpi.detail:<60}  ║")
            lines.append("║" + "─" * 64 + "║")

        lines.append("╚══════════════════════════════════════════════════════════════════╝")
        return "\n".join(lines)
