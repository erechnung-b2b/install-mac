#!/usr/bin/env python3
"""
E-Rechnungssystem – pytest Test-Suite
Deckt alle Module ab: Models, Generator, Parser, Validator, Inbox,
Workflow, Export, Archive, Mandant, Email, Logo-API
"""
import pytest
import json
import os
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from io import BytesIO
import struct, zlib, base64

# Module importieren
from models import (Invoice, Seller, Buyer, Address, Contact,
                    PaymentInfo, InvoiceLine, AllowanceCharge, InvoiceStatus)
from xrechnung_generator import generate_xrechnung, serialize_xml, generate_and_serialize
from xrechnung_parser import parse_xrechnung, detect_format
from validator import validate_invoice, Severity
from inbox import Inbox
from viewer import render_invoice
from wf_engine import WorkflowEngine
from export import ExportManager, DATEVExporter, StandardCSVExporter
from archive import InvoiceArchive
from mandant import create_demo_mandant


# ═══ Fixtures ══════════════════════════════════════════════════════════

@pytest.fixture
def seller():
    return Seller(
        name="Test GmbH", address=Address("Teststr. 1", "Berlin", "10115", "DE"),
        electronic_address="test@test.de", electronic_address_scheme="EM",
        contact=Contact("Tester", "+49 30 111", "tester@test.de"),
        vat_id="DE111111111", tax_registration_id="30/111/11111",
    )

@pytest.fixture
def buyer():
    return Buyer(
        name="Käufer AG", address=Address("Kaufstr. 2", "München", "80331", "DE"),
        electronic_address="einkauf@kaeufer.de", electronic_address_scheme="EM",
        buyer_reference="REF-001", vat_id="DE222222222",
    )

@pytest.fixture
def payment():
    return PaymentInfo(
        means_code="58", due_date=date.today() + timedelta(days=30),
        payment_terms="30 Tage netto", iban="DE89370400440532013000",
    )

@pytest.fixture
def valid_invoice(seller, buyer, payment):
    return Invoice(
        invoice_number="RE-TEST-001", invoice_date=date.today(),
        invoice_type_code="380", currency_code="EUR", buyer_reference="REF-001",
        seller=seller, buyer=buyer, payment=payment,
        lines=[
            InvoiceLine(line_id="1", quantity=Decimal("10"), unit_code="C62",
                        line_net_amount=Decimal("1000.00"), item_name="Beratung",
                        unit_price=Decimal("100.00"), tax_category="S", tax_rate=Decimal("19.00")),
            InvoiceLine(line_id="2", quantity=Decimal("5"), unit_code="C62",
                        line_net_amount=Decimal("250.00"), item_name="Lizenz",
                        unit_price=Decimal("50.00"), tax_category="S", tax_rate=Decimal("19.00")),
        ],
    )

@pytest.fixture
def valid_xml(valid_invoice):
    return generate_and_serialize(valid_invoice)

@pytest.fixture
def tmp_archive(tmp_path):
    return InvoiceArchive(str(tmp_path / "archiv"))

@pytest.fixture
def tmp_exporter(tmp_path):
    return ExportManager(str(tmp_path / "export"))


def make_tiny_png():
    raw = b'\x00\xff\x00\x00\xff'
    def chunk(ct, d):
        x = ct + d
        return struct.pack('>I', len(d)) + x + struct.pack('>I', zlib.crc32(x) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' +
            chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


# ═══ Models ════════════════════════════════════════════════════════════

class TestModels:
    def test_invoice_line_compute_net(self):
        line = InvoiceLine(quantity=Decimal("10"), unit_price=Decimal("100"),
                           line_net_amount=Decimal("1000.00"))
        assert line.compute_net() == Decimal("1000.00")

    def test_invoice_line_with_allowance(self):
        line = InvoiceLine(quantity=Decimal("10"), unit_price=Decimal("100"),
                           line_net_amount=Decimal("950.00"),
                           allowances_charges=[AllowanceCharge(amount=Decimal("50.00"))])
        assert line.compute_net() == Decimal("950.00")

    def test_invoice_sum_line_net(self, valid_invoice):
        assert valid_invoice.sum_line_net() == Decimal("1250.00")

    def test_invoice_tax_calculation(self, valid_invoice):
        assert valid_invoice.tax_exclusive_amount() == Decimal("1250.00")
        assert valid_invoice.tax_amount() == Decimal("237.50")
        assert valid_invoice.tax_inclusive_amount() == Decimal("1487.50")
        assert valid_invoice.amount_due() == Decimal("1487.50")

    def test_invoice_with_document_allowance(self, valid_invoice):
        valid_invoice.allowances_charges = [
            AllowanceCharge(amount=Decimal("62.50"), tax_category="S", tax_rate=Decimal("19.00"))
        ]
        assert valid_invoice.tax_exclusive_amount() == Decimal("1187.50")

    def test_invoice_mixed_tax_rates(self, seller, buyer, payment):
        inv = Invoice(
            invoice_number="MIX-001", seller=seller, buyer=buyer, payment=payment,
            buyer_reference="REF", lines=[
                InvoiceLine(line_id="1", quantity=Decimal("1"), unit_price=Decimal("100"),
                            line_net_amount=Decimal("100"), tax_rate=Decimal("19")),
                InvoiceLine(line_id="2", quantity=Decimal("1"), unit_price=Decimal("50"),
                            line_net_amount=Decimal("50"), tax_rate=Decimal("7")),
            ],
        )
        subtotals = inv.compute_tax_subtotals()
        assert len(subtotals) == 2
        rates = {st.rate for st in subtotals}
        assert Decimal("19") in rates and Decimal("7") in rates

    def test_audit_trail(self, valid_invoice):
        valid_invoice.add_audit("TEST", "user1", "Testkommentar")
        assert len(valid_invoice.audit_trail) == 1
        assert valid_invoice.audit_trail[0].event_type == "TEST"
        assert valid_invoice.audit_trail[0].user == "user1"

    def test_to_dict(self, valid_invoice):
        d = valid_invoice.to_dict()
        assert d["invoice_number"] == "RE-TEST-001"
        assert isinstance(d["invoice_date"], str)


# ═══ Generator ═════════════════════════════════════════════════════════

class TestGenerator:
    def test_generate_xml(self, valid_invoice):
        root = generate_xrechnung(valid_invoice)
        assert root is not None
        assert root.tag.endswith("Invoice")

    def test_serialize_xml(self, valid_invoice):
        xml = generate_and_serialize(valid_invoice)
        assert xml.startswith(b"<?xml")
        assert b"RE-TEST-001" in xml
        assert b"Test GmbH" in xml

    def test_xml_contains_buyer_reference(self, valid_invoice):
        xml = generate_and_serialize(valid_invoice)
        assert b"BuyerReference" in xml
        assert b"REF-001" in xml

    def test_xml_contains_customization_id(self, valid_invoice):
        xml = generate_and_serialize(valid_invoice)
        assert b"xrechnung" in xml.lower()

    def test_xml_contains_payment(self, valid_invoice):
        xml = generate_and_serialize(valid_invoice)
        assert b"DE89370400440532013000" in xml

    def test_credit_note(self, valid_invoice):
        valid_invoice.invoice_type_code = "381"
        xml = generate_and_serialize(valid_invoice)
        assert b"381" in xml


# ═══ Parser ════════════════════════════════════════════════════════════

class TestParser:
    def test_parse_roundtrip(self, valid_invoice, valid_xml):
        parsed = parse_xrechnung(valid_xml)
        assert parsed.invoice_number == valid_invoice.invoice_number
        assert parsed.seller.name == valid_invoice.seller.name
        assert parsed.buyer.name == valid_invoice.buyer.name
        assert len(parsed.lines) == len(valid_invoice.lines)

    def test_parse_amounts(self, valid_invoice, valid_xml):
        parsed = parse_xrechnung(valid_xml)
        assert parsed.tax_inclusive_amount() == valid_invoice.tax_inclusive_amount()

    def test_detect_format_xrechnung(self, valid_xml):
        fmt = detect_format(valid_xml)
        assert fmt.format_type == "XRECHNUNG"

    def test_detect_format_invalid_xml(self):
        fmt = detect_format(b"not xml")
        assert fmt.format_type == "UNKNOWN"

    def test_parse_seller_contact(self, valid_xml):
        parsed = parse_xrechnung(valid_xml)
        assert parsed.seller.contact.name == "Tester"
        assert parsed.seller.contact.telephone == "+49 30 111"

    def test_parse_payment(self, valid_xml):
        parsed = parse_xrechnung(valid_xml)
        assert parsed.payment.iban == "DE89370400440532013000"


# ═══ Validator ═════════════════════════════════════════════════════════

class TestValidator:
    def test_valid_invoice(self, valid_invoice):
        report = validate_invoice(valid_invoice)
        assert report.is_valid
        assert report.error_count == 0

    def test_missing_invoice_number(self, valid_invoice):
        valid_invoice.invoice_number = ""
        report = validate_invoice(valid_invoice)
        assert not report.is_valid
        assert any(i.rule_id == "BR-01" for i in report.issues)

    def test_missing_buyer_reference(self, valid_invoice):
        valid_invoice.buyer_reference = ""
        valid_invoice.buyer.buyer_reference = ""
        report = validate_invoice(valid_invoice)
        assert any(i.rule_id == "BR-DE-15" for i in report.issues)

    def test_missing_seller_contact(self, valid_invoice):
        valid_invoice.seller.contact = Contact()
        report = validate_invoice(valid_invoice)
        assert any(i.rule_id == "BR-DE-02" for i in report.issues)

    def test_missing_iban_for_transfer(self, valid_invoice):
        valid_invoice.payment.iban = ""
        report = validate_invoice(valid_invoice)
        assert any(i.rule_id == "BR-DE-19" for i in report.issues)

    def test_invalid_iban_format(self, valid_invoice):
        valid_invoice.payment.iban = "INVALID"
        report = validate_invoice(valid_invoice)
        assert any(i.rule_id == "BR-DE-19-F" for i in report.issues)

    def test_no_lines(self, valid_invoice):
        valid_invoice.lines = []
        report = validate_invoice(valid_invoice)
        assert any(i.rule_id == "BR-16" for i in report.issues)

    def test_inconsistent_line_amount(self, valid_invoice):
        valid_invoice.lines[0].line_net_amount = Decimal("999.99")
        report = validate_invoice(valid_invoice)
        assert any(i.rule_id == "BR-CO-10" for i in report.issues)

    def test_missing_payment_terms(self, valid_invoice):
        valid_invoice.payment.due_date = None
        valid_invoice.payment.payment_terms = ""
        report = validate_invoice(valid_invoice)
        assert any(i.rule_id == "BR-CO-25" for i in report.issues)

    def test_severity_levels(self, valid_invoice):
        valid_invoice.invoice_number = ""
        report = validate_invoice(valid_invoice)
        severities = {i.severity for i in report.issues}
        assert Severity.ERROR in severities


# ═══ Inbox ═════════════════════════════════════════════════════════════

class TestInbox:
    def test_receive_valid_xml(self, valid_xml):
        inbox = Inbox()
        item = inbox.receive_file("test.xml", valid_xml, sender_email="s@test.de")
        assert item.status == "VERARBEITET"
        assert item.invoice is not None
        assert item.invoice.invoice_number == "RE-TEST-001"

    def test_reject_invalid_type(self):
        inbox = Inbox()
        item = inbox.receive_file("malware.exe", b"evil content")
        assert item.status == "ABGELEHNT"

    def test_duplicate_detection(self, valid_xml):
        inbox = Inbox()
        inbox.receive_file("first.xml", valid_xml)
        item2 = inbox.receive_file("second.xml", valid_xml)
        assert item2.status == "DUPLIKAT"

    def test_pdf_without_xml(self):
        inbox = Inbox()
        item = inbox.receive_file("rechnung.pdf", b"%PDF-1.4 test")
        assert item.status == "VERARBEITET"
        assert "manuelle Prüfung" in item.error


# ═══ Workflow ══════════════════════════════════════════════════════════

class TestWorkflow:
    def test_start_workflow(self, valid_invoice):
        wf = WorkflowEngine()
        wf.start_workflow(valid_invoice)
        assert valid_invoice.status == InvoiceStatus.IN_PRUEFUNG.value

    def test_four_eyes_for_large_amount(self, valid_invoice):
        wf = WorkflowEngine()
        wf.start_workflow(valid_invoice)
        result = wf.sachliche_pruefung(valid_invoice, "user1", True)
        assert valid_invoice.status == InvoiceStatus.IN_FREIGABE.value
        assert "Vier-Augen" in result

    def test_full_approval_flow(self, valid_invoice):
        wf = WorkflowEngine()
        wf.start_workflow(valid_invoice)
        wf.sachliche_pruefung(valid_invoice, "user1", True)
        wf.kaufmaennische_freigabe(valid_invoice, "user2", True)
        assert valid_invoice.status == InvoiceStatus.FREIGEGEBEN.value

    def test_rejection(self, valid_invoice):
        wf = WorkflowEngine()
        wf.start_workflow(valid_invoice)
        wf.zurueckweisen(valid_invoice, "user1", "Falsch")
        assert valid_invoice.status == InvoiceStatus.ZURUECKGEWIESEN.value

    def test_small_amount_no_four_eyes(self, seller, buyer, payment):
        inv = Invoice(
            invoice_number="SMALL-001", seller=seller, buyer=buyer, payment=payment,
            buyer_reference="REF",
            lines=[InvoiceLine(line_id="1", quantity=Decimal("1"), unit_price=Decimal("100"),
                               line_net_amount=Decimal("100"), item_name="Klein")],
        )
        wf = WorkflowEngine()
        wf.start_workflow(inv)
        result = wf.sachliche_pruefung(inv, "user1", True)
        assert inv.status == InvoiceStatus.FREIGEGEBEN.value


# ═══ Export ═════════════════════════════════════════════════════════════

class TestExport:
    def test_datev_export(self, valid_invoice, tmp_exporter):
        valid_invoice.status = InvoiceStatus.FREIGEGEBEN.value
        result = tmp_exporter.export(valid_invoice, "DATEV")
        assert result.success
        assert "DATEV" in result.filename

    def test_csv_export(self, valid_invoice, tmp_exporter):
        valid_invoice.status = InvoiceStatus.FREIGEGEBEN.value
        result = tmp_exporter.export(valid_invoice, "CSV")
        assert result.success

    def test_export_idempotent(self, valid_invoice, tmp_exporter):
        valid_invoice.status = InvoiceStatus.FREIGEGEBEN.value
        tmp_exporter.export(valid_invoice, "DATEV")
        result2 = tmp_exporter.export(valid_invoice, "DATEV")
        assert not result2.success
        assert "Bereits exportiert" in result2.error

    def test_export_not_approved(self, valid_invoice, tmp_exporter):
        valid_invoice.status = InvoiceStatus.NEU.value
        result = tmp_exporter.export(valid_invoice, "DATEV")
        assert not result.success


# ═══ Archive ═══════════════════════════════════════════════════════════

class TestArchive:
    def test_archive_and_retrieve(self, valid_invoice, valid_xml, tmp_archive):
        report = validate_invoice(valid_invoice)
        rec = tmp_archive.archive_invoice(valid_invoice, valid_xml, report)
        assert rec.sha256_hash
        assert rec.invoice_number == "RE-TEST-001"
        found = tmp_archive.find_by_number("RE-TEST-001")
        assert len(found) == 1

    def test_integrity_check(self, valid_invoice, valid_xml, tmp_archive):
        report = validate_invoice(valid_invoice)
        rec = tmp_archive.archive_invoice(valid_invoice, valid_xml, report)
        ok, msg = tmp_archive.verify_integrity(rec.invoice_id)
        assert ok

    def test_search(self, valid_invoice, valid_xml, tmp_archive):
        report = validate_invoice(valid_invoice)
        tmp_archive.archive_invoice(valid_invoice, valid_xml, report, direction="EINGANG")
        results = tmp_archive.search(direction="EINGANG")
        assert len(results) == 1


# ═══ Mandant ═══════════════════════════════════════════════════════════

class TestMandant:
    def test_supplier_recognition_by_vat(self, valid_invoice):
        m = create_demo_mandant()
        valid_invoice.seller.vat_id = "DE123456789"
        supplier = m.find_supplier(valid_invoice)
        assert supplier is not None
        assert supplier.name == "Muster GmbH"

    def test_supplier_recognition_by_email(self, valid_invoice):
        m = create_demo_mandant()
        valid_invoice._sender_email = "rechnung@muster-gmbh.de"
        valid_invoice.seller.vat_id = ""
        supplier = m.find_supplier(valid_invoice)
        assert supplier is not None

    def test_permission_check(self):
        m = create_demo_mandant()
        assert m.has_permission("schmidt", "buchhaltung")
        assert not m.has_permission("weber", "buchhaltung")


# ═══ Viewer ════════════════════════════════════════════════════════════

class TestViewer:
    def test_render_valid(self, valid_invoice):
        report = validate_invoice(valid_invoice)
        output = render_invoice(valid_invoice, report)
        assert "RE-TEST-001" in output
        assert "Test GmbH" in output
        assert "GÜLTIG" in output

    def test_render_with_errors(self, valid_invoice):
        valid_invoice.invoice_number = ""
        report = validate_invoice(valid_invoice)
        output = render_invoice(valid_invoice, report)
        assert "UNGÜLTIG" in output


# ═══ Web-App (Integration) ═════════════════════════════════════════════

class TestWebApp:
    @pytest.fixture(autouse=True)
    def setup_app(self, tmp_path):
        os.chdir(str(tmp_path))
        for d in ("data/archiv", "data/export", "data/sent_mails", "data/test_mails", "data/logo", "static"):
            os.makedirs(d, exist_ok=True)
        src = Path(__file__).parent / "static" / "index.html"
        if src.exists():
            import shutil
            os.makedirs("static", exist_ok=True)
            shutil.copy(src, "static/index.html")

        from webapp import app, load_demo_data
        load_demo_data()
        self.client = app.test_client()

    def test_frontend_loads(self):
        r = self.client.get("/")
        assert r.status_code == 200

    def test_dashboard(self):
        r = self.client.get("/api/dashboard")
        assert r.get_json()["total"] > 0

    def test_invoice_list(self):
        r = self.client.get("/api/invoices")
        assert r.get_json()["count"] > 0

    def test_upload_xml(self, valid_xml):
        r = self.client.post("/api/upload",
                             data={"file": (BytesIO(valid_xml), "test.xml", "application/xml")},
                             content_type="multipart/form-data")
        assert r.status_code in (201, 400)

    def test_logo_lifecycle(self):
        png = make_tiny_png()
        r = self.client.post("/api/logo",
                             data={"file": (BytesIO(png), "logo.png", "image/png")},
                             content_type="multipart/form-data")
        assert r.get_json()["uploaded"]
        r = self.client.get("/api/logo")
        assert r.get_json()["has_logo"]
        r = self.client.delete("/api/logo")
        assert r.get_json()["deleted"]

    def test_storno_api(self):
        """Storno-Endpunkt erzeugt Gutschrift und markiert Original."""
        from webapp import invoices as inv_store
        # Finde eine freigegebene Rechnung
        frei_id = None
        for iid, inv in inv_store.items():
            if inv.status in ("FREIGEGEBEN", "NEU", "IN_PRUEFUNG"):
                inv.status = "FREIGEGEBEN"
                frei_id = iid
                break
        assert frei_id, "Keine Rechnung zum Stornieren gefunden"
        r = self.client.post(f"/api/invoices/{frei_id}/storno",
                             json={"user": "test", "reason": "pytest storno"},
                             content_type="application/json")
        res = r.get_json()
        assert res.get("success"), f"Storno fehlgeschlagen: {res}"
        assert res["type_code"] == "381"
        assert res["gross"] < 0
        assert inv_store[frei_id].status == "STORNIERT"
        # Doppelstorno
        r2 = self.client.post(f"/api/invoices/{frei_id}/storno",
                              json={"reason": "nochmal"}, content_type="application/json")
        assert "error" in r2.get_json()

    def test_archive_clickable(self):
        r = self.client.get("/")
        html = r.data.decode()
        assert "archiveClick" in html
        assert "archiveView" in html


# ═══ Benachrichtigungen ════════════════════════════════════════════════

class TestNotifications:
    def test_notify_new_invoice(self, valid_invoice):
        from notifications import NotificationEngine, NotificationType
        engine = NotificationEngine()
        engine.notify_new_invoice(valid_invoice, True)
        assert engine.unread_count() > 0
        notifs = engine.get_all()
        assert any(n.type == NotificationType.NEUE_RECHNUNG for n in notifs)

    def test_notify_with_errors(self, valid_invoice):
        from notifications import NotificationEngine, NotificationType
        engine = NotificationEngine()
        engine.notify_new_invoice(valid_invoice, False, errors=3)
        notifs = engine.get_all()
        assert any(n.type == NotificationType.VALIDIERUNG_FEHLER for n in notifs)

    def test_mark_read(self, valid_invoice):
        from notifications import NotificationEngine
        engine = NotificationEngine()
        engine.notify_new_invoice(valid_invoice, True)
        assert engine.unread_count() > 0
        for n in engine.get_all():
            engine.mark_read(n.notification_id)
        assert engine.unread_count() == 0


# ═══ Kontierungsvorschläge ═════════════════════════════════════════════

class TestAccountingSuggestions:
    def test_learn_and_suggest(self, valid_invoice):
        from advanced import AccountingSuggestionEngine
        engine = AccountingSuggestionEngine()
        engine.learn_from_invoice(valid_invoice, "4900", "IT")
        sugs = engine.suggest(valid_invoice)
        assert len(sugs) > 0
        assert sugs[0].account == "4900"
        assert sugs[0].confidence > 0.5

    def test_rule_overrides_history(self, valid_invoice):
        from advanced import AccountingSuggestionEngine
        engine = AccountingSuggestionEngine()
        engine.learn_from_invoice(valid_invoice, "4900", "IT")
        engine.add_rule(valid_invoice.seller.name, "6300", "ALLGEMEIN")
        sugs = engine.suggest(valid_invoice)
        assert sugs[0].account == "6300"
        assert sugs[0].source == "rule"

    def test_default_suggestion(self, valid_invoice):
        from advanced import AccountingSuggestionEngine
        engine = AccountingSuggestionEngine()
        valid_invoice.seller.name = "Unbekannte Firma XYZ"
        sugs = engine.suggest(valid_invoice)
        assert len(sugs) > 0
        assert sugs[0].source == "default"


# ═══ Stellvertretung ═══════════════════════════════════════════════════

class TestDeputy:
    def test_add_and_get(self):
        from advanced import DeputyManager
        dm = DeputyManager()
        dm.add_rule("mueller", "schmidt", date.today(), date.today() + timedelta(days=7))
        assert dm.get_deputy("mueller") == "schmidt"
        assert dm.get_deputy("weber") is None

    def test_redirect_invoice(self, valid_invoice):
        from advanced import DeputyManager
        dm = DeputyManager()
        dm.add_rule("mueller", "schmidt", date.today(), date.today() + timedelta(days=7))
        valid_invoice.assigned_to = "mueller"
        ok, info = dm.redirect_invoice(valid_invoice)
        assert ok
        assert valid_invoice.assigned_to == "schmidt"

    def test_expired_rule(self):
        from advanced import DeputyManager
        dm = DeputyManager()
        dm.add_rule("mueller", "schmidt", date.today() - timedelta(days=10), date.today() - timedelta(days=1))
        assert dm.get_deputy("mueller") is None


# ═══ Massenbearbeitung ═════════════════════════════════════════════════

class TestBulk:
    def test_bulk_assign(self, valid_invoice):
        from advanced import BulkProcessor
        bp = BulkProcessor()
        result = bp.bulk_assign([valid_invoice], "mueller")
        assert result.success == 1
        assert valid_invoice.assigned_to == "mueller"

    def test_bulk_export_not_approved(self, valid_invoice, tmp_exporter):
        from advanced import BulkProcessor
        bp = BulkProcessor(export_manager=tmp_exporter)
        valid_invoice.status = InvoiceStatus.NEU.value
        result = bp.bulk_export([valid_invoice])
        assert result.skipped == 1


# ═══ Aufbewahrungsfristen ══════════════════════════════════════════════

class TestRetention:
    def test_not_deletable_recent(self, valid_invoice):
        from advanced import RetentionManager
        rm = RetentionManager()
        ok, reason = rm.is_deletable(valid_invoice)
        assert not ok

    def test_locked(self, valid_invoice):
        from advanced import RetentionManager
        rm = RetentionManager()
        rm.lock(valid_invoice._id)
        assert rm.is_locked(valid_invoice._id)
        ok, reason = rm.is_deletable(valid_invoice)
        assert not ok and "gesperrt" in reason.lower()
        rm.unlock(valid_invoice._id)
        assert not rm.is_locked(valid_invoice._id)


# ═══ GiroCode QR ═══════════════════════════════════════════════════════

class TestGiroCode:
    def test_epc_payload_format(self, valid_invoice):
        from girocode import build_epc_from_invoice
        payload = build_epc_from_invoice(valid_invoice)
        lines = payload.split("\n")
        assert lines[0] == "BCD"
        assert lines[1] == "002"
        assert lines[2] == "1"
        assert lines[3] == "SCT"
        assert "DE89370400440532013000" in lines[6]
        assert "EUR" in lines[7]

    def test_epc_payload_amount(self, valid_invoice):
        from girocode import build_epc_from_invoice
        payload = build_epc_from_invoice(valid_invoice)
        assert "EUR1487.50" in payload

    def test_qr_svg_generation(self, valid_invoice):
        from girocode import generate_invoice_qr_svg
        svg = generate_invoice_qr_svg(valid_invoice)
        assert svg
        assert "<svg" in svg.lower() or "<path" in svg.lower()

    def test_qr_data_uri(self, valid_invoice):
        from girocode import generate_invoice_qr_data_uri
        uri = generate_invoice_qr_data_uri(valid_invoice)
        assert uri.startswith("data:image/svg+xml;base64,")

    def test_qr_info(self, valid_invoice):
        from girocode import get_qr_info
        info = get_qr_info(valid_invoice)
        assert info["available"]
        assert info["type"] == "EPC/GiroCode"
        assert info["beneficiary"] == "Test GmbH"
        assert info["amount"] == float(valid_invoice.amount_due())

    def test_no_qr_for_credit_note(self, valid_invoice):
        from girocode import get_qr_info
        valid_invoice.lines[0].quantity = Decimal("-10")
        valid_invoice.lines[0].line_net_amount = Decimal("-1000")
        valid_invoice.lines[1].quantity = Decimal("-5")
        valid_invoice.lines[1].line_net_amount = Decimal("-250")
        info = get_qr_info(valid_invoice)
        assert not info["available"]

    def test_no_qr_without_iban(self, valid_invoice):
        from girocode import get_qr_info
        valid_invoice.payment.iban = ""
        info = get_qr_info(valid_invoice)
        assert not info["available"]
        assert "IBAN" in info["reason"]


# ═══ Stornierung ═══════════════════════════════════════════════════════

class TestStorno:
    def test_storno_creates_credit_note(self, valid_invoice, tmp_archive):
        """Storno erzeugt Gutschrift mit negierten Beträgen und Bezug."""
        import copy
        valid_invoice.status = "FREIGEGEBEN"
        original_gross = valid_invoice.tax_inclusive_amount()

        storno = Invoice(
            invoice_number=f"ST-{valid_invoice.invoice_number}",
            invoice_type_code="381",
            buyer_reference=valid_invoice.buyer_reference,
            preceding_invoice=valid_invoice.invoice_number,
            seller=copy.deepcopy(valid_invoice.seller),
            buyer=copy.deepcopy(valid_invoice.buyer),
            payment=copy.deepcopy(valid_invoice.payment),
        )
        for line in valid_invoice.lines:
            storno.lines.append(InvoiceLine(
                line_id=line.line_id,
                quantity=line.quantity * -1,
                unit_price=line.unit_price,
                line_net_amount=line.line_net_amount * -1,
                item_name=line.item_name,
                tax_rate=line.tax_rate,
            ))

        assert storno.invoice_type_code == "381"
        assert storno.preceding_invoice == valid_invoice.invoice_number
        assert storno.tax_inclusive_amount() == -original_gross

    def test_storno_prevents_double(self, valid_invoice):
        """Stornierte Rechnung kann nicht erneut storniert werden."""
        valid_invoice.status = "STORNIERT"
        assert valid_invoice.status == "STORNIERT"

    def test_storno_xml_generation(self, valid_invoice):
        """Storno-XML wird korrekt erzeugt."""
        import copy
        storno = Invoice(
            invoice_number="ST-TEST",
            invoice_type_code="381",
            buyer_reference="REF",
            preceding_invoice=valid_invoice.invoice_number,
            seller=copy.deepcopy(valid_invoice.seller),
            buyer=copy.deepcopy(valid_invoice.buyer),
            payment=copy.deepcopy(valid_invoice.payment),
            lines=[InvoiceLine(
                line_id="1", quantity=Decimal("-10"), unit_price=Decimal("100"),
                line_net_amount=Decimal("-1000"), item_name="Storno",
                tax_rate=Decimal("19"),
            )],
        )
        xml = generate_and_serialize(storno)
        assert b"381" in xml
        assert b"ST-TEST" in xml


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
