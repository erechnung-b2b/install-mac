"""
E-Rechnungssystem – ZUGFeRD Support
FR-200/FR-220/FR-240: Hybridformat lesen (XML aus PDF extrahieren)
P-03: ZUGFeRD-PDF/A-3 erzeugen (optional)

ZUGFeRD = PDF/A-3 mit eingebettetem XML (factur-x.xml).
Strukturierter Teil ist führend (seit 2025 obligatorische E-Rechnung).
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional
import zipfile, io, re

from models import Invoice
from xrechnung_parser import parse_xrechnung, detect_format, FormatInfo


# ── ZUGFeRD-XML aus PDF extrahieren ────────────────────────────────────

# PDF/A-3 bettet Dateien als Embedded Files ein.
# Wir suchen nach dem XML-Stream im PDF ohne schwere PDF-Lib.
# Für Produktion: pikepdf oder PyMuPDF verwenden.

ZUGFERD_FILENAMES = [
    b"factur-x.xml",
    b"ZUGFeRD-invoice.xml",
    b"xrechnung.xml",
    b"zugferd-invoice.xml",
]


def extract_xml_from_pdf(pdf_bytes: bytes) -> Optional[bytes]:
    """
    Extrahiert eingebettetes XML aus einer ZUGFeRD-PDF.
    Bevorzugt pikepdf (korrekte PDF/A-3-Attachments); fällt auf
    Byte-Heuristik zurück, wenn pikepdf nicht verfügbar ist.
    """
    # Methode 1: pikepdf (robust, funktioniert mit komprimierten Streams)
    try:
        import pikepdf
        import io as _io
        with pikepdf.open(_io.BytesIO(pdf_bytes)) as pdf:
            for name in list(pdf.attachments.keys()):
                lname = name.lower()
                if lname.endswith(".xml") and any(
                    k in lname for k in ("factur-x", "zugferd", "xrechnung")
                ):
                    spec = pdf.attachments[name]
                    data = spec.get_file().read_bytes()
                    if data:
                        return data
            # Fallback: irgendein XML-Attachment
            for name in list(pdf.attachments.keys()):
                if name.lower().endswith(".xml"):
                    data = pdf.attachments[name].get_file().read_bytes()
                    if data:
                        return data
    except ImportError:
        pass
    except Exception:
        pass  # Auf Byte-Heuristik zurückfallen

    # Methode 2: Byte-Heuristik (Fallback für unkomprimierte PDFs)
    xml_start_patterns = [
        b"<?xml version",
        b"<Invoice xmlns",
        b"<rsm:CrossIndustryInvoice",
        b"<ubl:Invoice",
    ]

    for pattern in xml_start_patterns:
        idx = pdf_bytes.find(pattern)
        if idx == -1:
            continue

        end_markers = [b"</Invoice>", b"</rsm:CrossIndustryInvoice>", b"</ubl:Invoice>"]
        for end in end_markers:
            end_idx = pdf_bytes.find(end, idx)
            if end_idx != -1:
                xml_bytes = pdf_bytes[idx:end_idx + len(end)]
                try:
                    from lxml import etree
                    etree.fromstring(xml_bytes)
                    return xml_bytes
                except Exception:
                    continue

    return None


def extract_xml_from_zugferd(filepath: str) -> Optional[bytes]:
    """Liest eine ZUGFeRD-PDF und extrahiert das eingebettete XML."""
    path = Path(filepath)
    if not path.exists():
        return None
    pdf_bytes = path.read_bytes()
    return extract_xml_from_pdf(pdf_bytes)


# ── ZUGFeRD-Erkennung ─────────────────────────────────────────────────

def is_zugferd_pdf(pdf_bytes: bytes) -> bool:
    """Prüft ob eine PDF-Datei ein ZUGFeRD-Dokument ist."""
    for fn in ZUGFERD_FILENAMES:
        if fn in pdf_bytes:
            return True
    # Auch prüfen ob XRechnung-XML eingebettet ist
    for pattern in [b"factur-x", b"zugferd", b"CrossIndustryInvoice"]:
        if pattern.lower() in pdf_bytes.lower():
            return True
    return False


# ── Abweichungsprüfung PDF vs. XML (FR-240) ───────────────────────────

class HybridComparisonResult:
    def __init__(self):
        self.has_xml: bool = False
        self.has_pdf: bool = False
        self.xml_format: str = ""
        self.discrepancies: list[str] = []
        self.warnings: list[str] = []

    @property
    def is_consistent(self) -> bool:
        return len(self.discrepancies) == 0

    def summary(self) -> str:
        lines = [
            f"Hybridprüfung: XML={'✓' if self.has_xml else '✗'}  PDF={'✓' if self.has_pdf else '✗'}",
            f"Format: {self.xml_format}",
        ]
        if self.discrepancies:
            lines.append(f"Abweichungen ({len(self.discrepancies)}):")
            for d in self.discrepancies:
                lines.append(f"  ⚠ {d}")
        else:
            lines.append("Keine Abweichungen festgestellt.")
        if self.warnings:
            for w in self.warnings:
                lines.append(f"  ℹ {w}")
        return "\n".join(lines)


def compare_hybrid(xml_bytes: bytes, pdf_text: str = "") -> HybridComparisonResult:
    """
    FR-240: Vergleicht strukturierten Teil mit Bild-/Textteil.
    In Produktion: PDF-Text per OCR oder Text-Extraktion gewinnen.
    Der strukturierte Teil bleibt maßgeblich.
    """
    result = HybridComparisonResult()
    result.has_xml = xml_bytes is not None and len(xml_bytes) > 0
    result.has_pdf = bool(pdf_text)

    if not result.has_xml:
        result.warnings.append("Kein strukturierter XML-Teil gefunden.")
        return result

    fmt = detect_format(xml_bytes)
    result.xml_format = fmt.format_type

    if result.has_pdf:
        # Einfache Vergleichslogik: Rechnungsnummer und Betrag im PDF-Text suchen
        try:
            inv = parse_xrechnung(xml_bytes)

            if inv.invoice_number and inv.invoice_number not in pdf_text:
                result.discrepancies.append(
                    f"Rechnungsnummer '{inv.invoice_number}' aus XML nicht im PDF-Text gefunden.")

            betrag_str = f"{inv.tax_inclusive_amount():.2f}"
            if betrag_str not in pdf_text and betrag_str.replace(".", ",") not in pdf_text:
                result.discrepancies.append(
                    f"Bruttobetrag {betrag_str} aus XML nicht im PDF-Text gefunden.")

        except Exception as e:
            result.warnings.append(f"XML-Parsing für Vergleich fehlgeschlagen: {e}")

    result.warnings.append("Hinweis: Der strukturierte Teil ist maßgeblich (§14 UStG).")
    return result


# ── ZUGFeRD-Invoice parsen (Komfort-Funktion) ─────────────────────────

def parse_zugferd_pdf(filepath: str) -> Optional[Invoice]:
    """Parst eine ZUGFeRD-PDF und gibt das Invoice-Objekt zurück."""
    xml_bytes = extract_xml_from_zugferd(filepath)
    if xml_bytes is None:
        return None
    return parse_xrechnung(xml_bytes, source_file=Path(filepath).name)


# ── ZUGFeRD-Profil-Erkennung ──────────────────────────────────────────

ZUGFERD_PROFILES = {
    "MINIMUM": "urn:factur-x.eu:1p0:minimum",
    "BASIC_WL": "urn:factur-x.eu:1p0:basicwl",
    "BASIC": "urn:cen.eu:en16931:2017#compliant#urn:factur-x.eu:1p0:basic",
    "EN16931": "urn:cen.eu:en16931:2017",
    "EXTENDED": "urn:cen.eu:en16931:2017#conformant#urn:factur-x.eu:1p0:extended",
    "XRECHNUNG": "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0",
}


def detect_zugferd_profile(xml_bytes: bytes) -> str:
    """Erkennt das ZUGFeRD/Factur-X-Profil."""
    fmt = detect_format(xml_bytes)
    cust = fmt.customization_id.lower()

    for profile_name, profile_id in ZUGFERD_PROFILES.items():
        if profile_id.lower() in cust:
            return profile_name

    if "xrechnung" in cust or "xeinkauf" in cust:
        return "XRECHNUNG"
    if "en16931" in cust:
        return "EN16931"

    return "UNKNOWN"
