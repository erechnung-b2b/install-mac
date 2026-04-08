"""
E-Rechnungssystem – Validierungsengine
P-04 / FR-210: Geschäftsregeln XRechnung / EN 16931
Unterscheidet: ERROR (blockierend), WARNING, INFO
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
import re
from models import Invoice


class Severity(Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class ValidationIssue:
    rule_id: str
    severity: Severity
    message: str
    field: str = ""
    context: str = ""


@dataclass
class ValidationReport:
    invoice_number: str = ""
    timestamp: str = ""
    rule_profile: str = "XRechnung 3.0 / EN 16931"
    validator_version: str = "1.0.0"
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not any(i.severity == Severity.ERROR for i in self.issues)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == Severity.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == Severity.WARNING)

    def summary(self) -> str:
        status = "GÜLTIG" if self.is_valid else "UNGÜLTIG"
        lines = [
            f"═══ Validierungsbericht ═══",
            f"Rechnung:    {self.invoice_number}",
            f"Zeitpunkt:   {self.timestamp}",
            f"Profil:      {self.rule_profile}",
            f"Status:      {status}",
            f"Fehler:      {self.error_count}  Warnungen: {self.warning_count}",
        ]
        if self.issues:
            lines.append("")
            for i in self.issues:
                px = {"ERROR": "✗", "WARNING": "⚠", "INFO": "ℹ"}[i.severity.value]
                lines.append(f"  {px} [{i.rule_id}] {i.message}")
                if i.field:
                    lines.append(f"    Feld: {i.field}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "invoice_number": self.invoice_number,
            "timestamp": self.timestamp,
            "rule_profile": self.rule_profile,
            "validator_version": self.validator_version,
            "is_valid": self.is_valid,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "issues": [{"rule_id": i.rule_id, "severity": i.severity.value,
                        "message": i.message, "field": i.field} for i in self.issues],
        }


IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{4,30}$")


def validate_invoice(inv: Invoice) -> ValidationReport:
    report = ValidationReport(invoice_number=inv.invoice_number, timestamp=datetime.now().isoformat())
    iss = report.issues

    def err(rid, msg, fld=""): iss.append(ValidationIssue(rid, Severity.ERROR, msg, fld))
    def warn(rid, msg, fld=""): iss.append(ValidationIssue(rid, Severity.WARNING, msg, fld))
    def info(rid, msg, fld=""): iss.append(ValidationIssue(rid, Severity.INFO, msg, fld))

    # Kopf-Pflichtfelder
    if not inv.invoice_number: err("BR-01", "Rechnungsnummer (BT-1) fehlt.", "BT-1")
    if not inv.invoice_date: err("BR-02", "Rechnungsdatum (BT-2) fehlt.", "BT-2")
    if not inv.invoice_type_code: err("BR-03", "Rechnungstyp (BT-3) fehlt.", "BT-3")
    if not inv.currency_code: err("BR-04", "Währungscode (BT-5) fehlt.", "BT-5")

    ref = inv.buyer_reference or inv.buyer.buyer_reference
    if not ref: err("BR-DE-15", "Buyer Reference (BT-10) ist in XRechnung verpflichtend.", "BT-10")

    # Seller
    if not inv.seller.name: err("BR-06", "Verkäufername (BT-27) fehlt.", "BT-27")
    if not inv.seller.electronic_address: err("BR-DE-16", "Seller Electronic Address (BT-34) fehlt.", "BT-34")
    if not inv.seller.electronic_address_scheme: err("BR-DE-16-S", "Scheme Identifier für BT-34 fehlt.", "BT-34-1")
    if not inv.seller.address.city: err("BR-09", "Seller City (BT-37) fehlt.", "BT-37")
    if not inv.seller.address.post_code: err("BR-DE-04", "Seller Post Code (BT-38) fehlt.", "BT-38")
    if not inv.seller.address.country_code: err("BR-09-C", "Seller Country Code (BT-40) fehlt.", "BT-40")
    if not inv.seller.contact.name: err("BR-DE-02", "Seller Contact Name (BT-41) fehlt.", "BT-41")
    if not inv.seller.contact.telephone: err("BR-DE-06", "Seller Contact Telephone (BT-42) fehlt.", "BT-42")
    if not inv.seller.contact.email: err("BR-DE-07", "Seller Contact Email (BT-43) fehlt.", "BT-43")
    if not inv.seller.vat_id and not inv.seller.tax_registration_id:
        err("BR-CO-26", "Mindestens Steuer-ID (BT-31 oder BT-32) erforderlich.", "BT-31/BT-32")

    # Buyer
    if not inv.buyer.name: err("BR-07", "Käufername (BT-44) fehlt.", "BT-44")
    if not inv.buyer.electronic_address: err("BR-DE-17", "Buyer Electronic Address (BT-49) fehlt.", "BT-49")
    if not inv.buyer.address.city: err("BR-11", "Buyer City (BT-52) fehlt.", "BT-52")
    if not inv.buyer.address.post_code: err("BR-DE-05", "Buyer Post Code (BT-53) fehlt.", "BT-53")

    # Zahlung
    if inv.amount_due() > Decimal("0"):
        if not inv.payment.due_date and not inv.payment.payment_terms:
            err("BR-CO-25", "Bei positivem Zahlbetrag: Fälligkeitsdatum oder Zahlungsbedingung nötig.", "BT-9/BT-20")
    if inv.payment.means_code in ("30", "58"):
        if not inv.payment.iban:
            err("BR-DE-19", "Bei Überweisung: IBAN (BT-84) erforderlich.", "BT-84")
        elif not IBAN_RE.match(inv.payment.iban.replace(" ", "").upper()):
            warn("BR-DE-19-F", f"IBAN-Format prüfen: '{inv.payment.iban}'", "BT-84")
    if inv.payment.means_code == "59" and not inv.payment.mandate_reference:
        err("BR-DE-20", "Bei Lastschrift: Mandatsreferenz (BT-89) erforderlich.", "BT-89")

    # Positionen
    if not inv.lines:
        err("BR-16", "Mindestens eine Position erforderlich.")
    for idx, line in enumerate(inv.lines, 1):
        ctx = f"Pos. {idx} ({line.line_id})"
        if not line.line_id: err("BR-21", f"{ctx}: Positionsnummer fehlt.", "BT-126")
        if line.quantity <= Decimal("0"): err("BR-22", f"{ctx}: Menge muss > 0 sein.", "BT-129")
        if not line.item_name: err("BR-25", f"{ctx}: Artikelbezeichnung fehlt.", "BT-153")
        if line.unit_price < Decimal("0"): err("BR-26", f"{ctx}: Einzelpreis darf nicht negativ sein.", "BT-146")
        computed = line.compute_net()
        if computed != line.line_net_amount:
            err("BR-CO-10", f"{ctx}: Nettobetrag inkonsistent. Erwartet={computed}, angegeben={line.line_net_amount}", "BT-131")

    # Nachlass-Konsistenz
    for idx, ac in enumerate(inv.allowances_charges, 1):
        if ac.percentage and ac.base_amount:
            expected = (ac.base_amount * ac.percentage / Decimal("100")).quantize(Decimal("0.01"))
            if expected != ac.amount:
                warn("BR-CO-05", f"Nachlass/Zuschlag {idx}: Betrag ({ac.amount}) ≠ Prozent×Basis ({expected})")

    # Summen
    expected_due = inv.tax_inclusive_amount()
    actual_due = inv.amount_due()
    if expected_due != actual_due:
        warn("BR-CO-15", f"Zahlbetrag ({actual_due}) ≠ Bruttobetrag ({expected_due})", "BT-115")

    # Hinweise
    if not inv.order_reference and not inv.contract_reference:
        info("HINT-01", "Keine Bestell-/Vertragsreferenz – empfohlen für automatisierte Verarbeitung.", "BT-13/BT-12")
    if not inv.period_start and not inv.period_end and not inv.tax_point_date:
        info("HINT-02", "Kein Leistungszeitraum angegeben.", "BT-73/BT-74")

    return report
