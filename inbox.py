"""
E-Rechnungssystem – Eingangsverarbeitung
FR-100..FR-130: Upload, Dateityperkennung, Virencheck-Stub, Dublettenerkennung
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
import hashlib, json, uuid

from models import Invoice
from xrechnung_parser import parse_xrechnung, detect_format
from validator import validate_invoice, ValidationReport


@dataclass
class InboxItem:
    item_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    filename: str = ""
    file_size: int = 0
    mime_type: str = ""
    sha256: str = ""
    received_at: str = field(default_factory=lambda: datetime.now().isoformat())
    sender_email: str = ""
    subject: str = ""
    message_id: str = ""
    format_type: str = ""
    status: str = "EMPFANGEN"  # EMPFANGEN, VERARBEITET, ABGELEHNT, VIRUS, DUPLIKAT
    error: str = ""
    invoice: Optional[Invoice] = None
    validation: Optional[ValidationReport] = None


class DuplicateDetector:
    """FR-120: Dublettenerkennung anhand Rechnungsnummer + Lieferant + Betrag + SHA-256"""

    def __init__(self):
        self._seen: dict[str, str] = {}       # composite_key → item_id
        self._hashes: dict[str, str] = {}     # sha256 → item_id
        self._numbers: dict[str, str] = {}    # invoice_number → item_id
        self._external_check = None           # Callback: invoice_number → bool

    def set_external_check(self, fn):
        """Setzt eine Callback-Funktion die prüft ob eine Rechnungsnummer
        bereits im Hauptsystem existiert (z.B. invoices-Dict)."""
        self._external_check = fn

    def _make_key(self, inv: Invoice) -> str:
        return f"{inv.seller.name}|{inv.invoice_number}|{inv.tax_inclusive_amount()}"

    def check(self, inv: Invoice, sha256: str = "") -> tuple[bool, str]:
        # 1. Exakte Datei-Dublette (gleicher Hash)
        if sha256 and sha256 in self._hashes:
            return True, f"Identische Datei bereits verarbeitet (SHA-256: {sha256[:16]}…)"

        # 2. Gleiche Rechnungsnummer
        if inv.invoice_number in self._numbers:
            return True, f"Rechnungsnummer {inv.invoice_number} bereits vorhanden."

        # 3. Komposit-Schlüssel (Lieferant + Nummer + Betrag)
        key = self._make_key(inv)
        if key in self._seen:
            return True, f"Dublettenverdacht: {inv.invoice_number} von {inv.seller.name} mit gleichem Betrag bereits verarbeitet."

        # 4. Externe Prüfung (gegen invoices-Dict)
        if self._external_check and self._external_check(inv.invoice_number):
            return True, f"Rechnungsnummer {inv.invoice_number} existiert bereits im System."

        return False, ""

    def register(self, inv: Invoice, item_id: str, sha256: str = ""):
        key = self._make_key(inv)
        self._seen[key] = item_id
        self._numbers[inv.invoice_number] = item_id
        if sha256:
            self._hashes[sha256] = item_id


# FR-120: Virencheck-Stub
BLOCKED_SIGNATURES = [b"X5O!P%@AP[4\\PZX54(P^)7CC)7}", b"EICAR"]

def virus_check(data: bytes) -> tuple[bool, str]:
    """Stub-Virenprüfung. In Produktion: ClamAV oder externen Scanner anbinden."""
    for sig in BLOCKED_SIGNATURES:
        if sig in data:
            return False, "Virenwarnung: Verdächtiges Muster erkannt."
    return True, ""


# FR-120: Dateityp-Erkennung
ALLOWED_TYPES = {
    ".xml": "application/xml",
    ".pdf": "application/pdf",
}

def detect_file_type(filename: str, data: bytes) -> tuple[str, str]:
    """Gibt (mime_type, error) zurück."""
    ext = Path(filename).suffix.lower()
    if ext in ALLOWED_TYPES:
        return ALLOWED_TYPES[ext], ""
    return "", f"Unzulässiger Dateityp: {ext}"


class Inbox:
    """Eingangspostfach für E-Rechnungen (FR-100..FR-130)"""

    def __init__(self):
        self.items: list[InboxItem] = []
        self.duplicates = DuplicateDetector()

    def receive_file(self, filename: str, data: bytes,
                     sender_email: str = "", subject: str = "",
                     message_id: str = "") -> InboxItem:
        """Verarbeitet eine eingehende Datei end-to-end."""

        item = InboxItem(
            filename=filename,
            file_size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            sender_email=sender_email,
            subject=subject,
            message_id=message_id,
        )

        # 1. Dateityp prüfen
        mime, err = detect_file_type(filename, data)
        if err:
            item.status = "ABGELEHNT"
            item.error = err
            self.items.append(item)
            return item
        item.mime_type = mime

        # 2. Virenprüfung
        safe, virus_msg = virus_check(data)
        if not safe:
            item.status = "VIRUS"
            item.error = virus_msg
            self.items.append(item)
            return item

        # 3. Format erkennen
        if mime == "application/xml":
            fmt = detect_format(data)
            item.format_type = fmt.format_type

            # 4. Parsen
            try:
                inv = parse_xrechnung(data, source_file=filename)
                inv._sender_email = sender_email
                inv._received_at = item.received_at
                inv._source_file = filename
                item.invoice = inv
            except Exception as e:
                item.status = "ABGELEHNT"
                item.error = f"Parse-Fehler: {str(e)}"
                self.items.append(item)
                return item

            # 5. Validierung
            report = validate_invoice(inv)
            item.validation = report

            # 6. Dubletten-Check
            is_dup, dup_msg = self.duplicates.check(inv, item.sha256)
            if is_dup:
                item.status = "DUPLIKAT"
                item.error = dup_msg
                inv.add_audit("DUPLIKAT_ERKANNT", comment=dup_msg)
            else:
                self.duplicates.register(inv, item.item_id, item.sha256)
                item.status = "VERARBEITET"
                inv.add_audit("EINGANG_VERARBEITET",
                              comment=f"Format: {item.format_type}, Validierung: {'OK' if report.is_valid else 'FEHLER'}")

        elif mime == "application/pdf":
            item.format_type = "PDF"
            item.status = "VERARBEITET"
            item.error = "PDF ohne strukturierten Teil – manuelle Prüfung erforderlich."

        self.items.append(item)
        return item

    def get_processed(self) -> list[InboxItem]:
        return [i for i in self.items if i.status == "VERARBEITET" and i.invoice]

    def get_errors(self) -> list[InboxItem]:
        return [i for i in self.items if i.status in ("ABGELEHNT", "VIRUS")]

    def get_duplicates(self) -> list[InboxItem]:
        return [i for i in self.items if i.status == "DUPLIKAT"]

    def summary(self) -> str:
        total = len(self.items)
        ok = len(self.get_processed())
        errs = len(self.get_errors())
        dups = len(self.get_duplicates())
        return (f"Inbox: {total} Eingänge | {ok} verarbeitet | {errs} abgelehnt | {dups} Dubletten")
