"""
E-Rechnungssystem – Export
FR-400..FR-440: DATEV-naher CSV, Standard-CSV, Exportprotokoll
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import csv, io, json, uuid
from models import Invoice, InvoiceStatus


@dataclass
class ExportResult:
    export_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    invoice_number: str = ""
    format: str = ""
    target: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    success: bool = False
    error: str = ""
    filename: str = ""
    row_count: int = 0


class DATEVExporter:
    """
    FR-430: DATEV-naher Buchungsstapel-Export
    Erzeugt CSV im DATEV-Buchungsstapel-ähnlichen Format.
    """

    HEADER_FIELDS = [
        "Umsatz (ohne Soll/Haben-Kz)", "Soll/Haben-Kennzeichen",
        "WKZ Umsatz", "Kurs", "Basis-Umsatz", "WKZ Basis-Umsatz",
        "Konto", "Gegenkonto (ohne BU-Schlüssel)", "BU-Schlüssel",
        "Belegdatum", "Belegfeld 1", "Belegfeld 2",
        "Skonto", "Buchungstext", "Postensperre", "Diverse Adressnummer",
        "Geschäftspartnerbank", "Sachverhalt", "Zinssperre",
        "Beleglink", "Beleginfo - Art 1", "Beleginfo - Inhalt 1",
        "Beleginfo - Art 2", "Beleginfo - Inhalt 2",
    ]

    def export_invoice(self, inv: Invoice) -> ExportResult:
        """Exportiert eine freigegebene Rechnung als DATEV-CSV."""
        result = ExportResult(
            invoice_number=inv.invoice_number,
            format="DATEV",
        )

        if inv.status not in (InvoiceStatus.FREIGEGEBEN.value, InvoiceStatus.EXPORTIERT.value):
            result.error = f"Rechnung nicht freigegeben (Status: {inv.status})"
            return result

        try:
            buf = io.StringIO()
            writer = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_ALL)

            # Header
            writer.writerow(self.HEADER_FIELDS)

            # Buchungszeilen (eine pro Steuer-Kategorie)
            for st in inv.compute_tax_subtotals():
                brutto = st.taxable_amount + st.tax_amount
                bu_key = self._tax_rate_to_bu(st.rate)
                belegdatum = inv.invoice_date.strftime("%d%m") if inv.invoice_date else ""

                row = [
                    f"{brutto:.2f}".replace(".", ","),  # Umsatz
                    "S",  # Soll
                    inv.currency_code,
                    "",  # Kurs
                    "",  # Basis-Umsatz
                    "",  # WKZ Basis
                    "1200",  # Konto (Beispiel: Forderungen/Verbindlichkeiten)
                    self._get_kreditor_konto(inv),  # Gegenkonto
                    bu_key,
                    belegdatum,
                    inv.invoice_number,  # Belegfeld 1
                    inv.order_reference or "",  # Belegfeld 2
                    "",  # Skonto
                    f"{inv.seller.name[:30]}",  # Buchungstext
                    "",  # Postensperre
                    "",  # Diverse Adressnummer
                    "",  # Geschäftspartnerbank
                    "",  # Sachverhalt
                    "",  # Zinssperre
                    "",  # Beleglink
                    "Rechnungsnummer", inv.invoice_number,
                    "Leistungsdatum", str(inv.period_start or inv.invoice_date),
                ]
                writer.writerow(row)

            result.success = True
            result.filename = f"DATEV_{inv.invoice_number.replace('/', '_')}.csv"
            result.target = buf.getvalue()
            result.row_count = len(inv.compute_tax_subtotals())

        except Exception as e:
            result.error = str(e)

        return result

    @staticmethod
    def _tax_rate_to_bu(rate: Decimal) -> str:
        """Mappt USt-Satz auf DATEV BU-Schlüssel."""
        mapping = {
            Decimal("19.00"): "9",
            Decimal("7.00"): "8",
            Decimal("0.00"): "0",
        }
        return mapping.get(rate, "9")

    @staticmethod
    def _get_kreditor_konto(inv: Invoice) -> str:
        """Lieferanten-Konto (vereinfacht)."""
        return "70000"  # Standard-Kreditor; in Produktion: Stammdaten-Mapping

    def export_bulk(self, invoices: list) -> ExportResult:
        """
        FR-430 / Monats- und Jahresexport:
        Fasst mehrere freigegebene Rechnungen in einen gemeinsamen
        DATEV-Buchungsstapel zusammen (Header einmalig, dann alle
        Buchungszeilen aller Rechnungen hintereinander).

        Nicht-freigegebene Rechnungen werden übersprungen und in
        result.error zusammengefasst.
        """
        result = ExportResult(invoice_number="BULK", format="DATEV")
        skipped: list[str] = []
        included: list[str] = []

        try:
            buf = io.StringIO()
            writer = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_ALL)
            writer.writerow(self.HEADER_FIELDS)

            total_rows = 0
            for inv in invoices:
                if inv.status not in (InvoiceStatus.FREIGEGEBEN.value,
                                      InvoiceStatus.EXPORTIERT.value):
                    skipped.append(f"{inv.invoice_number} (Status: {inv.status})")
                    continue

                for st in inv.compute_tax_subtotals():
                    brutto = st.taxable_amount + st.tax_amount
                    bu_key = self._tax_rate_to_bu(st.rate)
                    belegdatum = inv.invoice_date.strftime("%d%m") if inv.invoice_date else ""
                    row = [
                        f"{brutto:.2f}".replace(".", ","),
                        "S",
                        inv.currency_code,
                        "", "", "",
                        "1200",
                        self._get_kreditor_konto(inv),
                        bu_key,
                        belegdatum,
                        inv.invoice_number,
                        inv.order_reference or "",
                        "",
                        f"{inv.seller.name[:30]}",
                        "", "", "", "", "", "",
                        "Rechnungsnummer", inv.invoice_number,
                        "Leistungsdatum", str(inv.period_start or inv.invoice_date),
                    ]
                    writer.writerow(row)
                    total_rows += 1
                included.append(inv.invoice_number)

            result.success = True
            result.target = buf.getvalue()
            result.row_count = total_rows
            if skipped:
                result.error = f"{len(skipped)} übersprungen: " + ", ".join(skipped[:10])
            # invoice_number wird von der Route gesetzt (mit Zeitraum-Kennung)
            result.filename = f"DATEV_Bulk_{len(included)}_Rechnungen.csv"
        except Exception as e:
            result.error = str(e)

        return result


class StandardCSVExporter:
    """FR-430: Standard-CSV-Export mit allen Belegmetadaten."""

    FIELDS = [
        "rechnungsnummer", "rechnungsdatum", "rechnungstyp", "waehrung",
        "verkaeufer_name", "verkaeufer_ust_id", "verkaeufer_adresse",
        "kaeufer_name", "kaeufer_ust_id",
        "buyer_reference", "bestellreferenz", "vertragsreferenz",
        "leistungszeitraum_von", "leistungszeitraum_bis",
        "netto_gesamt", "ust_gesamt", "brutto_gesamt", "zahlbetrag",
        "faelligkeit", "zahlungsbedingungen", "iban",
        "positionen_anzahl", "status",
    ]

    def export_invoice(self, inv: Invoice) -> ExportResult:
        result = ExportResult(
            invoice_number=inv.invoice_number,
            format="CSV",
        )

        try:
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=self.FIELDS, delimiter=";",
                                    quoting=csv.QUOTE_ALL, extrasaction="ignore")
            writer.writeheader()

            row = {
                "rechnungsnummer": inv.invoice_number,
                "rechnungsdatum": str(inv.invoice_date),
                "rechnungstyp": inv.invoice_type_code,
                "waehrung": inv.currency_code,
                "verkaeufer_name": inv.seller.name,
                "verkaeufer_ust_id": inv.seller.vat_id,
                "verkaeufer_adresse": f"{inv.seller.address.street}, {inv.seller.address.post_code} {inv.seller.address.city}",
                "kaeufer_name": inv.buyer.name,
                "kaeufer_ust_id": inv.buyer.vat_id,
                "buyer_reference": inv.buyer_reference or inv.buyer.buyer_reference,
                "bestellreferenz": inv.order_reference,
                "vertragsreferenz": inv.contract_reference,
                "leistungszeitraum_von": str(inv.period_start or ""),
                "leistungszeitraum_bis": str(inv.period_end or ""),
                "netto_gesamt": str(inv.tax_exclusive_amount()),
                "ust_gesamt": str(inv.tax_amount()),
                "brutto_gesamt": str(inv.tax_inclusive_amount()),
                "zahlbetrag": str(inv.amount_due()),
                "faelligkeit": str(inv.payment.due_date or ""),
                "zahlungsbedingungen": inv.payment.payment_terms,
                "iban": inv.payment.iban,
                "positionen_anzahl": str(len(inv.lines)),
                "status": inv.status,
            }
            writer.writerow(row)

            result.success = True
            result.filename = f"Export_{inv.invoice_number.replace('/', '_')}.csv"
            result.target = buf.getvalue()
            result.row_count = 1

        except Exception as e:
            result.error = str(e)

        return result


class ExportManager:
    """Verwaltet Exporte, Protokollierung und Fehlerbehandlung (FR-420)."""

    def __init__(self, output_dir: str = "./export"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log: list[dict] = []
        self.datev = DATEVExporter()
        self.csv = StandardCSVExporter()

    def export(self, inv: Invoice, format: str = "DATEV") -> ExportResult:
        """Exportiert und speichert. Idempotent: Mehrfach-Export wird erkannt."""

        # Idempotenz-Check
        for entry in self.log:
            if entry["invoice_number"] == inv.invoice_number and entry["format"] == format and entry["success"]:
                return ExportResult(
                    invoice_number=inv.invoice_number,
                    format=format,
                    success=False,
                    error=f"Bereits exportiert am {entry['timestamp']} (ID: {entry['export_id']})",
                )

        if format == "DATEV":
            result = self.datev.export_invoice(inv)
        elif format == "CSV":
            result = self.csv.export_invoice(inv)
        else:
            result = ExportResult(invoice_number=inv.invoice_number, error=f"Unbekanntes Format: {format}")

        if result.success and result.target:
            filepath = self.output_dir / result.filename
            filepath.write_text(result.target, encoding="utf-8")
            result.target = str(filepath)

            inv.add_audit("EXPORT_ERSTELLT", comment=f"Format: {format}, Datei: {result.filename}")

        self.log.append({
            "export_id": result.export_id,
            "invoice_number": result.invoice_number,
            "format": format,
            "timestamp": result.timestamp,
            "success": result.success,
            "error": result.error,
            "filename": result.filename,
        })

        return result

    def retry(self, inv: Invoice, format: str = "DATEV") -> ExportResult:
        """FR-420: Wiederholung eines fehlerhaften Exports."""
        # Alten Fehl-Eintrag entfernen
        self.log = [e for e in self.log
                    if not (e["invoice_number"] == inv.invoice_number and e["format"] == format)]
        return self.export(inv, format)

    def get_log(self) -> list[dict]:
        return list(self.log)
