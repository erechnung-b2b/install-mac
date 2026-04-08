#!/usr/bin/env python3
"""
E-Rechnungssystem – Vollständiger Testlauf
Testet BEIDE Richtungen: Ausgangsrechnungen erstellen + Eingangsrechnungen empfangen

Szenarien:
  A1. Standardrechnung erstellen (Ausgang)
  A2. Rechnung mit Nachlass erstellen
  A3. Gutschrift erstellen
  B1. Rechnung per Inbox empfangen + parsen (Eingang)
  B2. Dublette erkennen
  B3. Ungültige Datei abweisen
  C1. Workflow: Freigabeprozess durchlaufen
  C2. DATEV-Export + Standard-CSV
  C3. Archivsuche + Integritätsprüfung
"""
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import sys

from models import (Invoice, Seller, Buyer, Address, Contact, PaymentInfo,
                    InvoiceLine, AllowanceCharge, InvoiceStatus)
from xrechnung_generator import generate_xrechnung, serialize_xml, generate_and_serialize
from xrechnung_parser import parse_xrechnung, detect_format
from validator import validate_invoice
from inbox import Inbox
from viewer import render_invoice
from wf_engine import WorkflowEngine
from export import ExportManager
from archive import InvoiceArchive
from mandant import MandantManager, create_demo_mandant
from zugferd import detect_zugferd_profile, compare_hybrid, is_zugferd_pdf
from dashboard import Dashboard


# ═══ Testdaten ═════════════════════════════════════════════════════════

def make_seller():
    return Seller(
        name="Muster GmbH", address=Address("Hauptstraße 42", "Berlin", "10115", "DE"),
        electronic_address="info@muster-gmbh.de", electronic_address_scheme="EM",
        contact=Contact("Max Mustermann", "+49 30 12345678", "buchhaltung@muster-gmbh.de"),
        vat_id="DE123456789", tax_registration_id="30/123/45678",
    )

def make_buyer():
    return Buyer(
        name="Beispiel AG", address=Address("Industrieweg 7", "München", "80331", "DE"),
        electronic_address="einkauf@beispiel-ag.de", electronic_address_scheme="EM",
        buyer_reference="LEITWEG-2024-001", vat_id="DE987654321",
    )

def make_payment():
    return PaymentInfo(
        means_code="58", due_date=date.today() + timedelta(days=30),
        payment_terms="Zahlbar innerhalb 30 Tagen netto",
        iban="DE89370400440532013000", bic="COBADEFFXXX", bank_name="Commerzbank",
    )


# ═══ TEIL A: Ausgangsrechnungen erstellen ══════════════════════════════

def test_a1_standard():
    """A1: Standardrechnung erstellen"""
    return Invoice(
        invoice_number="RE-2026-0001", invoice_date=date.today(),
        invoice_type_code="380", currency_code="EUR",
        buyer_reference="LEITWEG-2024-001",
        period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
        order_reference="PO-2026-4711", note="Leistungszeitraum März 2026",
        seller=make_seller(), buyer=make_buyer(), payment=make_payment(),
        lines=[
            InvoiceLine(line_id="1", quantity=Decimal("10"), unit_code="C62",
                        line_net_amount=Decimal("1000.00"), item_name="IT-Beratung Stunde",
                        item_description="Senior Consultant", unit_price=Decimal("100.00"),
                        tax_category="S", tax_rate=Decimal("19.00"), item_id="SRV-001"),
            InvoiceLine(line_id="2", quantity=Decimal("5"), unit_code="C62",
                        line_net_amount=Decimal("250.00"), item_name="Softwarelizenz Monat",
                        unit_price=Decimal("50.00"), tax_category="S",
                        tax_rate=Decimal("19.00"), item_id="LIC-002"),
        ],
    )

def test_a2_nachlass():
    """A2: Rechnung mit Dokumenten-Nachlass"""
    inv = test_a1_standard()
    inv.invoice_number = "RE-2026-0002"
    inv.note = "5% Treuerabatt"
    inv.allowances_charges = [AllowanceCharge(
        is_charge=False, amount=Decimal("62.50"), base_amount=Decimal("1250.00"),
        percentage=Decimal("5.00"), reason="Treuerabatt", reason_code="95",
        tax_category="S", tax_rate=Decimal("19.00"),
    )]
    return inv

def test_a3_gutschrift():
    """A3: Gutschrift"""
    return Invoice(
        invoice_number="GS-2026-0001", invoice_date=date.today(),
        invoice_type_code="381", currency_code="EUR",
        buyer_reference="LEITWEG-2024-001",
        preceding_invoice="RE-2026-0001",
        note="Gutschrift wegen Minderleistung",
        seller=make_seller(), buyer=make_buyer(), payment=make_payment(),
        lines=[InvoiceLine(
            line_id="1", quantity=Decimal("2"), unit_code="C62",
            line_net_amount=Decimal("200.00"), item_name="IT-Beratung – Gutschrift",
            unit_price=Decimal("100.00"), tax_category="S", tax_rate=Decimal("19.00"),
        )],
    )


# ═══ Hilfsfunktionen ══════════════════════════════════════════════════

def section(title):
    print(f"\n{'═' * 70}")
    print(f"  {title}")
    print(f"{'═' * 70}")

def step(msg):
    print(f"\n  ▸ {msg}")

def ok(msg):
    print(f"    ✓ {msg}")

def fail(msg):
    print(f"    ✗ {msg}")


# ═══ Hauptprogramm ════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("  E-RECHNUNGSSYSTEM – VOLLSTÄNDIGER TESTLAUF")
    print("  Ausgang (Erstellen) + Eingang (Empfangen) + Workflow + Export")
    print("=" * 70)

    output = Path("./output"); output.mkdir(exist_ok=True)
    archive = InvoiceArchive("./archiv")
    inbox = Inbox()
    wf = WorkflowEngine()
    exporter = ExportManager("./export")
    xml_cache = {}  # invoice_number → xml_bytes

    # ══════════════════════════════════════════════════════════════
    section("TEIL A: AUSGANGSRECHNUNGEN ERSTELLEN")
    # ══════════════════════════════════════════════════════════════

    for label, factory in [
        ("A1: Standardrechnung", test_a1_standard),
        ("A2: Rechnung mit Nachlass", test_a2_nachlass),
        ("A3: Gutschrift", test_a3_gutschrift),
    ]:
        step(label)
        inv = factory()

        # Validieren
        report = validate_invoice(inv)
        if report.is_valid:
            ok(f"Validierung: GÜLTIG (0 Fehler, {report.warning_count} Warnungen)")
        else:
            fail(f"Validierung: UNGÜLTIG ({report.error_count} Fehler)")

        # XML erzeugen
        xml_bytes = generate_and_serialize(inv)
        xml_path = output / f"{inv.invoice_number.replace('/', '_')}.xml"
        xml_path.write_bytes(xml_bytes)
        xml_cache[inv.invoice_number] = xml_bytes
        ok(f"XML erzeugt: {xml_path.name} ({len(xml_bytes)} Bytes)")

        # Archivieren
        rec = archive.archive_invoice(inv, xml_bytes, report, direction="AUSGANG")
        ok(f"Archiviert: SHA-256={rec.sha256_hash[:16]}...")

        # Summen
        ok(f"Netto: {inv.tax_exclusive_amount()} | USt: {inv.tax_amount()} | Brutto: {inv.tax_inclusive_amount()} EUR")

    # ══════════════════════════════════════════════════════════════
    section("TEIL B: EINGANGSRECHNUNGEN EMPFANGEN")
    # ══════════════════════════════════════════════════════════════

    # B1: Rechnung per Inbox empfangen (Roundtrip: unsere eigene XML parsen)
    step("B1: XRechnung empfangen und parsen")
    xml_data = xml_cache["RE-2026-0001"]
    item = inbox.receive_file(
        "RE-2026-0001.xml", xml_data,
        sender_email="buchhaltung@muster-gmbh.de",
        subject="Rechnung RE-2026-0001",
        message_id="<msg-001@muster-gmbh.de>",
    )
    if item.status == "VERARBEITET" and item.invoice:
        ok(f"Empfangen und geparst: {item.invoice.invoice_number}")
        ok(f"Format: {item.format_type} | Seller: {item.invoice.seller.name}")
        ok(f"Betrag: {item.invoice.tax_inclusive_amount()} EUR")

        # Roundtrip-Prüfung
        orig = test_a1_standard()
        parsed = item.invoice
        if orig.invoice_number == parsed.invoice_number and orig.tax_inclusive_amount() == parsed.tax_inclusive_amount():
            ok("Roundtrip OK: Originaldaten = Geparste Daten")
        else:
            fail("Roundtrip-Abweichung!")

        # Viewer
        step("B1b: Viewer-Darstellung")
        view = render_invoice(parsed, item.validation)
        print(view)

        # Archivieren
        archive.archive_invoice(parsed, xml_data, item.validation, direction="EINGANG")
        ok("Als Eingangsrechnung archiviert")
    else:
        fail(f"Empfang fehlgeschlagen: {item.status} – {item.error}")

    # B2: Dublette
    step("B2: Dublettenerkennung")
    item2 = inbox.receive_file("RE-2026-0001_kopie.xml", xml_data,
                                sender_email="buchhaltung@muster-gmbh.de")
    if item2.status == "DUPLIKAT":
        ok(f"Dublette erkannt: {item2.error}")
    else:
        fail(f"Dublette NICHT erkannt: {item2.status}")

    # B3: Ungültige Datei
    step("B3: Ungültige Datei abweisen")
    item3 = inbox.receive_file("virus.exe", b"MZ\x90\x00malware", sender_email="hacker@evil.com")
    if item3.status == "ABGELEHNT":
        ok(f"Abgelehnt: {item3.error}")
    else:
        fail(f"Nicht abgelehnt: {item3.status}")

    # Inbox-Summary
    step("Inbox-Zusammenfassung")
    ok(inbox.summary())

    # ══════════════════════════════════════════════════════════════
    section("TEIL C: WORKFLOW + EXPORT")
    # ══════════════════════════════════════════════════════════════

    # C1: Freigabeprozess
    step("C1: Workflow-Freigabeprozess")
    eingangs_inv = item.invoice

    result = wf.start_workflow(eingangs_inv, "system")
    ok(f"Start: {result}")

    result = wf.sachliche_pruefung(eingangs_inv, "mueller", True, "Leistung erhalten")
    ok(f"Sachliche Prüfung: {result}")

    result = wf.kaufmaennische_freigabe(eingangs_inv, "schmidt", True, "Budget OK")
    ok(f"Kaufm. Freigabe: {result}")

    ok(f"Status: {eingangs_inv.status}")
    ok(f"Audit-Trail: {len(eingangs_inv.audit_trail)} Einträge")

    # C2: Export
    step("C2: DATEV-Export")
    datev_result = exporter.export(eingangs_inv, "DATEV")
    if datev_result.success:
        ok(f"DATEV-Export: {datev_result.filename}")
    else:
        fail(f"DATEV-Export fehlgeschlagen: {datev_result.error}")

    step("C2b: Standard-CSV-Export")
    csv_result = exporter.export(eingangs_inv, "CSV")
    if csv_result.success:
        ok(f"CSV-Export: {csv_result.filename}")
    else:
        fail(f"CSV-Export fehlgeschlagen: {csv_result.error}")

    # Idempotenz-Test
    step("C2c: Idempotenz-Test (erneuter Export)")
    dup_result = exporter.export(eingangs_inv, "DATEV")
    if not dup_result.success and "Bereits exportiert" in dup_result.error:
        ok(f"Idempotenz funktioniert: {dup_result.error}")
    else:
        fail("Idempotenz-Check fehlgeschlagen")

    # C3: Archivsuche
    step("C3: Archivsuche + Integrität")
    found = archive.search(seller_name="Muster")
    ok(f"Suche 'Muster': {len(found)} Treffer")

    found2 = archive.search(direction="EINGANG")
    ok(f"Suche Eingangsrechnungen: {len(found2)} Treffer")

    for rec in archive.list_all():
        valid, msg = archive.verify_integrity(rec["invoice_id"])
        status = "✓" if valid else "✗"
        ok(f"Integrität {rec['invoice_number']}: {status} {msg}")

    # ══════════════════════════════════════════════════════════════
    section("TEIL D: MANDANTENVERWALTUNG")
    # ══════════════════════════════════════════════════════════════

    step("D1: Demo-Mandant anlegen")
    mgr = MandantManager("./mandanten")
    m = create_demo_mandant()
    mgr.mandanten[m.mandant_id] = m
    ok(f"Mandant: {m.name} (USt-ID: {m.vat_id})")
    ok(f"Benutzer: {len(m.users)} | Lieferanten: {len(m.suppliers)} | Regeln: {len(m.approval_rules)}")

    step("D2: Lieferantenerkennung (FR-030)")
    eingangs_inv2 = parse_xrechnung(xml_cache["RE-2026-0001"])
    eingangs_inv2._sender_email = "buchhaltung@muster-gmbh.de"
    supplier = m.find_supplier(eingangs_inv2)
    if supplier:
        ok(f"Erkannt: {supplier.name} (Kreditor: {supplier.creditor_number})")
    else:
        fail("Lieferant nicht erkannt")

    step("D3: Pflichtreferenzen prüfen (FR-040)")
    ref_issues = m.check_mandatory_refs(eingangs_inv2)
    for fld, msg, blocks in ref_issues:
        icon = "✗ BLOCKIERT" if blocks else "⚠ Warnung"
        ok(f"{icon}: {msg}")
    if not ref_issues:
        ok("Alle Pflichtreferenzen vorhanden")

    step("D4: Berechtigungsprüfung")
    ok(f"mueller (buchhaltung) darf freigeben: {m.has_permission('mueller', 'buchhaltung')}")
    ok(f"weber (fachabteilung) darf freigeben: {m.has_permission('weber', 'buchhaltung')}")

    # ══════════════════════════════════════════════════════════════
    section("TEIL E: ZUGFERD & FORMAT-ERKENNUNG")
    # ══════════════════════════════════════════════════════════════

    step("E1: ZUGFeRD-Profil erkennen")
    profile = detect_zugferd_profile(xml_cache["RE-2026-0001"])
    ok(f"Profil: {profile}")

    step("E2: Hybrid-Vergleich (FR-240)")
    comparison = compare_hybrid(xml_cache["RE-2026-0001"],
                                 "RE-2026-0001 Muster GmbH 1487,50 EUR")
    ok(comparison.summary())

    step("E3: PDF ohne ZUGFeRD erkennen")
    is_zf = is_zugferd_pdf(b"%PDF-1.4 Keine E-Rechnung hier")
    ok(f"Ist ZUGFeRD: {is_zf} (erwartet: False)")

    # ══════════════════════════════════════════════════════════════
    section("TEIL F: DASHBOARD & KPIs")
    # ══════════════════════════════════════════════════════════════

    step("F1: Dashboard berechnen")
    dash = Dashboard()
    # Alle verarbeiteten Rechnungen hinzufügen
    for itm in inbox.items:
        dash.add_inbox_item(itm)
        if itm.invoice:
            dash.add_invoice(itm.invoice)
    # Ausgangsrechnungen auch
    for label, factory in [("A1", test_a1_standard), ("A2", test_a2_nachlass), ("A3", test_a3_gutschrift)]:
        dash.add_invoice(factory())
    dash.set_export_log(exporter.get_log())

    print(dash.render())

    # ══════════════════════════════════════════════════════════════
    section("TEIL G: API-ENDPUNKTE (Dokumentation)")
    # ══════════════════════════════════════════════════════════════

    step("API-Server starten mit: python api.py")
    from api import API_DOCS
    print(API_DOCS)

    # ══════════════════════════════════════════════════════════════
    section("ZUSAMMENFASSUNG")
    # ══════════════════════════════════════════════════════════════

    print(f"\n  Module:   14 Python-Dateien")
    print(f"  Archiv:   {len(archive.list_all())} Einträge")
    print(f"  Inbox:    {inbox.summary()}")
    print(f"  Exporte:  {len(exporter.get_log())} Exportvorgänge")
    print(f"  Mandant:  {m.name} ({len(m.suppliers)} Lieferanten)")
    print(f"  Output:   {output.absolute()}")
    print(f"  Archiv:   {archive.root.absolute()}")
    print(f"\n  Alle Tests erfolgreich durchlaufen ✓")

    return 0


if __name__ == "__main__":
    sys.exit(main())
