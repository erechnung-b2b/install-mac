"""Tests: ZUGFeRD/Factur-X (CII, PDF/A-3) und Einlesen von CII-Rechnungen."""
import io
import re
import shutil
from decimal import Decimal
from pathlib import Path

import pikepdf
import pytest

import demo
from cii_generator import generate_cii_bytes, GUIDELINE_XRECHNUNG
from cii_parser import is_cii, parse_cii
from inbox import Inbox
from validator import validate_invoice
from xrechnung_parser import detect_format, parse_xrechnung
from zugferd import extract_xml_from_pdf
from zugferd_writer import generate_zugferd_pdf

FAELLE = [demo.test_a1_standard, demo.test_a2_nachlass, demo.test_a3_gutschrift]


def _kern(inv):
    return (inv.invoice_number, inv.invoice_type_code, inv.invoice_date, inv.seller.name,
            inv.seller.vat_id, inv.buyer.name, inv.buyer.address.post_code,
            (inv.payment.iban or "").replace(" ", ""), inv.payment.due_date,
            inv.buyer_reference, len(inv.lines), inv.tax_exclusive_amount(),
            inv.tax_amount(), inv.amount_due(),
            [(l.item_name, Decimal(l.quantity), Decimal(l.unit_price), l.line_net_amount,
              l.tax_category, Decimal(l.tax_rate)) for l in inv.lines])


@pytest.mark.parametrize("fall", FAELLE)
def test_cii_rundlauf(fall):
    inv = fall()
    xml = generate_cii_bytes(inv)
    assert is_cii(xml)
    assert GUIDELINE_XRECHNUNG.encode() in xml
    zurueck = parse_xrechnung(xml)            # Weiche erkennt CII selbst
    assert _kern(zurueck) == _kern(inv)


def test_formaterkennung_cii():
    fmt = detect_format(generate_cii_bytes(demo.test_a1_standard()))
    assert fmt.format_type == "XRECHNUNG"


def test_gutschrift_als_typ_381():
    xml = generate_cii_bytes(demo.test_a3_gutschrift())
    assert b"<ram:TypeCode>381</ram:TypeCode>" in xml
    assert parse_cii(xml).preceding_invoice == "RE-2026-0001"


@pytest.fixture(scope="module")
def zugferd_pdf():
    return generate_zugferd_pdf(demo.test_a1_standard())


def test_pdf_enthaelt_factur_x_cii(zugferd_pdf):
    with pikepdf.open(io.BytesIO(zugferd_pdf)) as pdf:
        assert "factur-x.xml" in pdf.attachments
        spec = pdf.Root.AF[0]
        assert spec.AFRelationship == pikepdf.Name.Alternative
    xml = extract_xml_from_pdf(zugferd_pdf)
    assert is_cii(xml)


def test_pdf_ist_pdfa3_mit_facturx_metadaten(zugferd_pdf):
    with pikepdf.open(io.BytesIO(zugferd_pdf)) as pdf:
        xmp = bytes(pdf.Root.Metadata.read_bytes()).decode("utf-8")
        assert "<pdfaid:part>3</pdfaid:part>" in xmp
        assert "<pdfaid:conformance>B</pdfaid:conformance>" in xmp
        assert "<fx:ConformanceLevel>XRECHNUNG</fx:ConformanceLevel>" in xmp
        assert "<fx:DocumentFileName>factur-x.xml</fx:DocumentFileName>" in xmp
        assert len(pdf.Root.OutputIntents) == 1


def test_pdf_alle_schriften_eingebettet(zugferd_pdf):
    with pikepdf.open(io.BytesIO(zugferd_pdf)) as pdf:
        schriften = 0
        for page in pdf.pages:
            for _, font in (page.Resources.get("/Font") or {}).items():
                schriften += 1
                desc = font.get("/FontDescriptor")
                if desc is None and "/DescendantFonts" in font:
                    desc = font.DescendantFonts[0].get("/FontDescriptor")
                assert desc is not None and any(k in desc for k in ("/FontFile", "/FontFile2", "/FontFile3")), \
                    f"Schrift nicht eingebettet: {font.get('/BaseFont')}"
        assert schriften > 0


def test_andere_pdfs_behalten_standardschrift(zugferd_pdf):
    from reportlab.pdfbase import pdfmetrics
    assert pdfmetrics.getFont("Helvetica").__class__.__name__ != "TTFont"


def test_eingang_verarbeitet_zugferd_pdf(zugferd_pdf):
    item = Inbox().receive_file("rechnung.pdf", zugferd_pdf, sender_email="a@example.com")
    assert item.status == "VERARBEITET"
    assert item.format_type == "ZUGFERD"
    assert item.invoice is not None and item.invoice.invoice_number == "RE-2026-0001"


def test_brde_regeln_nur_fuer_xrechnung():
    inv = demo.test_a1_standard()
    inv.buyer_reference = inv.buyer.buyer_reference = ""
    inv.seller.contact.telephone = ""
    assert not validate_invoice(inv).is_valid          # eigene XRechnung: streng
    inv._source_format = "ZUGFERD_CII"
    assert validate_invoice(inv).is_valid              # ZUGFeRD EN 16931: BR-DE nur Hinweis


KOSIT = Path(__file__).resolve().parent / "tools" / "kosit" / "validator.jar"


@pytest.mark.skipif(not KOSIT.exists() or not shutil.which("java"), reason="KoSIT-Validator nicht installiert")
@pytest.mark.parametrize("fall", FAELLE)
def test_cii_besteht_kosit(fall):
    from kosit_validator import validate_with_kosit
    r = validate_with_kosit(generate_cii_bytes(fall()), validator_path=str(KOSIT.parent), timeout_sec=180)
    assert r.available and r.valid, r.errors
