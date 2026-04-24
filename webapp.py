#!/usr/bin/env python3
"""
E-Rechnungssystem – Web-Server
Flask-basiertes Frontend + REST-API
Verbindet alle Backend-Module mit dem HTML-Frontend.
"""
from __future__ import annotations
import json, os, sys, hashlib, base64
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, abort

# Basispfad: PyInstaller-Bundle oder normaler Betrieb
if getattr(sys, 'frozen', False):
    _BASE = Path(sys._MEIPASS)  # PyInstaller entpackt hierhin
    _DATA = Path(os.path.dirname(sys.executable))  # .exe-Verzeichnis für Daten
else:
    _BASE = Path(__file__).parent
    _DATA = _BASE

# Backend-Module
from models import (Invoice, Seller, Buyer, Address, Contact,
                    PaymentInfo, InvoiceLine, AllowanceCharge, InvoiceStatus)
from xrechnung_generator import generate_and_serialize
from xrechnung_parser import parse_xrechnung, detect_format
from validator import validate_invoice
from inbox import Inbox
from viewer import render_invoice
from wf_engine import WorkflowEngine
from export import ExportManager
from archive import InvoiceArchive
from mandant import MandantManager, create_demo_mandant, Supplier
from dashboard import Dashboard
from email_handler import (EmailConfig, EmailManager, EmailSender, EmailReceiver,
                           MockEmailSender, MockEmailReceiver, EmailSendLog)
from notifications import NotificationEngine, NotificationType
from advanced import (AccountingSuggestionEngine, DeputyManager, BulkProcessor,
                      RetentionManager, BackgroundPoller, DeputyRule)
from girocode import (generate_invoice_qr_data_uri, generate_invoice_qr_svg,
                       get_qr_info, build_epc_from_invoice, HAS_QRCODE)
from persistence import InvoiceStore
from licensing import LicenseManager
from pdf_import import parse_energieberatung_pdf
from zugferd_writer import generate_zugferd_pdf
from kosit_validator import validate_with_kosit, is_available as kosit_available
from suppliers import SupplierManager
from transactions import (TransactionManager, STEP_KEYS, STEP_LABELS,
                           DOC_PREFIXES, make_position, calc_step_totals)
from doc_generator import DocumentGenerator
from dunning import DunningManager
from products import ProductManager, create_demo_products

# ── App ────────────────────────────────────────────────────────────────

app = Flask(__name__,
            static_folder=str(_BASE / "static"),
            static_url_path="/static")


# JSON-Fehlerseiten statt HTML für API-Routen
@app.errorhandler(404)
def handle_404(e):
    if request.path.startswith("/api/"):
        return _json({"error": "Endpoint nicht gefunden", "path": request.path}, 404)
    return send_from_directory(str(_BASE / "static"), "index.html")


@app.errorhandler(500)
def handle_500(e):
    return _json({"error": f"Interner Serverfehler: {e}"}, 500)

# Globaler State
inbox = Inbox()
archive = InvoiceArchive(str(_DATA / "data" / "archiv"))
exporter = ExportManager(str(_DATA / "data" / "export"))
wf_engine = WorkflowEngine()
dash = Dashboard()
mandant_mgr = MandantManager(str(_DATA / "data" / "mandanten"))
invoices: dict[str, Invoice] = {}  # _id → Invoice

# Demo-Mandant
demo_mandant = create_demo_mandant()
mandant_mgr.mandanten[demo_mandant.mandant_id] = demo_mandant

# E-Mail (Mock für Demo, echte Konfiguration über /api/email/config)
email_config = EmailConfig(
    imap_host="imap.example.com", imap_user="rechnungen@demo-gmbh.de",
    smtp_host="smtp.example.com", smtp_from_address="rechnungen@demo-gmbh.de",
    smtp_from_name="EBRK UG Rechnungswesen", mandant_name="EBRK UG",
)
email_sender = MockEmailSender(email_config, str(_DATA / "data" / "sent_mails"))
email_receiver = MockEmailReceiver(email_config, inbox, str(_DATA / "data" / "test_mails"))

# Erweiterte Module
notifier = NotificationEngine()
accounting = AccountingSuggestionEngine()
deputies = DeputyManager()
bulk = BulkProcessor(wf_engine, exporter)
retention = RetentionManager()
poller = BackgroundPoller(email_receiver)

# Persistenz
store = InvoiceStore(str(_DATA / "data"))
lic_mgr = LicenseManager(str(_DATA / "data"))

# Auftragsmanagement (Phase 1)
supplier_mgr = SupplierManager(str(_DATA / "data"))
txn_mgr = TransactionManager(str(_DATA / "data"))
doc_gen = DocumentGenerator(str(_DATA / "data"))
dunning_mgr = DunningManager(str(_DATA / "data"))
product_mgr = ProductManager(str(_DATA / "data"))


def auto_save():
    """Speichert alle Rechnungen auf die Festplatte. Wird nach jeder Aenderung aufgerufen."""
    try:
        store.save(invoices)
    except Exception as e:
        print(f"  WARNUNG: Auto-Save fehlgeschlagen: {e}")


def _decimal_default(obj):
    if isinstance(obj, Decimal): return float(obj)
    if isinstance(obj, date): return obj.isoformat()
    raise TypeError(f"Cannot serialize {type(obj)}")


def _json(data, status=200):
    return app.response_class(
        json.dumps(data, default=_decimal_default, ensure_ascii=False),
        status=status, mimetype="application/json"
    )


# ── Daten laden (gespeichert oder Demo) ──────────────────────────────

def load_data():
    """Laedt gespeicherte Daten von der Festplatte, oder erzeugt Demo-Daten."""
    global email_config, email_sender, email_receiver

    # DuplicateDetector mit externem Check verbinden
    inbox.duplicates.set_external_check(
        lambda nr: any(inv.invoice_number == nr for inv in invoices.values())
    )

    # Gespeicherte Daten vorhanden?
    if store.has_saved_data():
        loaded = store.load()
        if loaded:
            invoices.update(loaded)
            for inv in invoices.values():
                dash.add_invoice(inv)
                inbox.duplicates.register(inv, inv._id)
            print(f"  {len(invoices)} Rechnungen von Festplatte geladen")
    else:
        # Keine gespeicherten Daten → Demo-Daten erzeugen
        _create_demo_data()
        auto_save()
        print(f"  {len(invoices)} Demo-Rechnungen erzeugt")

    # Gespeicherte E-Mail-Konfiguration laden
    _email_cfg_path = _DATA / "data" / "email_config.json"
    if _email_cfg_path.exists():
        try:
            _saved = json.loads(_email_cfg_path.read_text("utf-8"))
            for k, v in _saved.items():
                if hasattr(email_config, k) and not k.startswith("_"):
                    setattr(email_config, k, v)
            if email_config.smtp_host and email_config.smtp_password:
                email_sender = EmailSender(email_config)
                print(f"  SMTP: {email_config.smtp_host} (echtes Senden aktiv)")
            else:
                print(f"  SMTP: nicht konfiguriert (Mock-Modus)")
            if email_config.imap_host and email_config.imap_password:
                email_receiver = EmailReceiver(email_config, inbox)
                poller.receiver = email_receiver
                print(f"  IMAP: {email_config.imap_host} (echter Empfang aktiv)")
            else:
                print(f"  IMAP: nicht konfiguriert (Mock-Modus)")
        except Exception:
            pass


def _create_demo_data():
    """Erzeugt Demo-Rechnungen fuer den Erststart."""
    from demo import test_a1_standard, test_a2_nachlass, test_a3_gutschrift
    import shutil

    # Archiv-Index zuruecksetzen
    archive._index.clear()
    archive._save_index()
    for child in archive.root.iterdir():
        if child.is_dir():
            shutil.rmtree(child)

    for factory in [test_a1_standard, test_a2_nachlass, test_a3_gutschrift]:
        inv = factory()
        inv._direction = "AUSGANG"
        report = validate_invoice(inv)
        xml = generate_and_serialize(inv)
        archive.archive_invoice(inv, xml, report, direction="AUSGANG")
        invoices[inv._id] = inv
        dash.add_invoice(inv)
        inbox.duplicates.register(inv, inv._id)

    inv_0001 = [v for v in invoices.values() if v.invoice_number == "RE-2026-0001"][0]
    inv_0001.status = InvoiceStatus.IN_FREIGABE.value
    inv_0001.assigned_to = "buchhaltung"
    inv_0001.add_audit("WORKFLOW_GESTARTET", "system", "Regel: Standard")
    inv_0001.add_audit("SACHLICHE_PRUEFUNG_OK", "mueller", "Leistung erhalten")

    extras = [
        ("RE-2026-0088", "Bürobedarf Schmidt", "342.00", "19.00", InvoiceStatus.NEU.value),
        ("RE-2026-0089", "CloudHost AG", "890.00", "19.00", InvoiceStatus.IN_PRUEFUNG.value),
        ("RE-2026-0091", "TechParts GmbH", "4200.00", "19.00", InvoiceStatus.IN_FREIGABE.value),
    ]
    for nr, seller_name, netto, rate, status in extras:
        inv = Invoice(
            invoice_number=nr, invoice_date=date.today() - timedelta(days=1),
            invoice_type_code="380", currency_code="EUR",
            buyer_reference="LEITWEG-2024-001",
            seller=Seller(name=seller_name, address=Address("Musterstr. 1", "Berlin", "10115"),
                          electronic_address=f"info@{seller_name.lower().replace(' ','-')}.de",
                          electronic_address_scheme="EM",
                          contact=Contact("Kontakt", "+49 30 1234", "info@example.de"),
                          vat_id="DE999888777"),
            buyer=Buyer(name="Demo GmbH", address=Address("Musterweg 1", "Hamburg", "20095"),
                        electronic_address="eingang@demo-gmbh.de", electronic_address_scheme="EM",
                        buyer_reference="LEITWEG-2024-001", vat_id="DE111222333"),
            payment=PaymentInfo(means_code="58", due_date=date.today() + timedelta(days=30),
                                payment_terms="30 Tage netto", iban="DE89370400440532013000"),
            lines=[InvoiceLine(line_id="1", quantity=Decimal("1"), unit_code="C62",
                               line_net_amount=Decimal(netto), item_name="Dienstleistung",
                               unit_price=Decimal(netto), tax_category="S", tax_rate=Decimal(rate))],
            status=status,
        )
        inv._direction = "EINGANG"
        if status == InvoiceStatus.IN_FREIGABE.value:
            inv.assigned_to = "geschaeftsfuehrung"
            inv.add_audit("WORKFLOW_GESTARTET", "system")
            inv.add_audit("SACHLICHE_PRUEFUNG_OK", "weber", "OK")
        elif status == InvoiceStatus.IN_PRUEFUNG.value:
            inv.assigned_to = "buchhaltung"
            inv.add_audit("WORKFLOW_GESTARTET", "system")
        invoices[inv._id] = inv
        dash.add_invoice(inv)
        inbox.duplicates.register(inv, inv._id)
        report = validate_invoice(inv)
        xml = generate_and_serialize(inv)
        archive.archive_invoice(inv, xml, report, direction="EINGANG")


# Alias fuer Abwaertskompatibilitaet (Tests verwenden load_demo_data)
def load_demo_data():
    load_data()


# ── Seiten ─────────────────────────────────────────────────────────────

@app.after_request
def _after_request(response):
    """Auto-Save nach jeder erfolgreichen Schreiboperation."""
    if request.method in ("POST", "PUT", "DELETE") and response.status_code < 400:
        auto_save()
    return response


# Lizenzpruefung fuer schreibende Operationen
WRITE_ENDPOINTS = {
    "/api/upload", "/api/generate", "/api/invoices/", "/api/email/check",
    "/api/bulk/", "/api/invoices"
}

@app.before_request
def _check_license():
    """Blockiert schreibende Operationen ohne gueltige Lizenz."""
    if request.method != "POST":
        return None
    path = request.path
    # Lizenz-Aktivierung immer erlauben
    if path == "/api/license/activate":
        return None
    # Einstellungen erlauben
    if path.startswith("/api/email/config") or path.startswith("/api/logo"):
        return None
    if path.startswith("/api/notifications"):
        return None
    # Produkte, Lieferanten, Vorgänge, Mahnungen, Dokumente erlauben (eigene Schreiblogik)
    if any(path.startswith(p) for p in ("/api/suppliers", "/api/transactions",
           "/api/products", "/api/dunning", "/api/documents", "/api/mandant")):
        return None
    # Schreibende Operationen pruefen
    try:
        block = lic_mgr.check_or_block()
        if block:
            return app.response_class(
                json.dumps(block, ensure_ascii=False),
                status=403, mimetype="application/json"
            )
    except Exception as e:
        print(f"  WARNUNG: Lizenzprüfung fehlgeschlagen: {e}")
    return None


# ── API: Stammdaten / Mandant ─────────────────────────────────────────

_MANDANT_FILE = _DATA / "data" / "mandant_settings.json"

def _load_mandant_settings() -> dict:
    if _MANDANT_FILE.exists():
        try:
            return json.loads(_MANDANT_FILE.read_text("utf-8"))
        except Exception:
            pass
    return {}

def _save_mandant_settings(data: dict):
    _MANDANT_FILE.parent.mkdir(parents=True, exist_ok=True)
    _MANDANT_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")

def _next_invoice_number(advance: bool = False) -> str:
    """Erzeugt die naechste Rechnungsnummer RE-YYYY-XXXXX.
    advance=False: Nur Vorschau (Zaehler bleibt stehen)
    advance=True:  Zaehler wird hochgezaehlt und gespeichert
    """
    settings = _load_mandant_settings()
    year = date.today().year
    stored_year = settings.get("invoice_number_year", year)
    counter = settings.get("invoice_number_counter", 0)

    # Jahreswechsel: Zaehler zuruecksetzen
    if stored_year != year:
        counter = 0

    next_counter = counter + 1
    number = f"RE-{year}-{next_counter:05d}"

    # Sicherstellen dass die Nummer noch nicht vergeben ist
    while any(inv.invoice_number == number for inv in invoices.values()):
        next_counter += 1
        number = f"RE-{year}-{next_counter:05d}"

    if advance:
        settings["invoice_number_year"] = year
        settings["invoice_number_counter"] = next_counter
        _save_mandant_settings(settings)

    return number


@app.route("/api/mandant/settings")
def api_mandant_settings_get():
    return _json(_load_mandant_settings())

@app.route("/api/mandant/next-number")
def api_mandant_next_number():
    """Gibt die naechste freie Rechnungsnummer als Vorschau zurueck (ohne hochzuzaehlen)."""
    number = _next_invoice_number(advance=False)
    return _json({"number": number})

@app.route("/api/mandant/settings", methods=["POST"])
def api_mandant_settings_save():
    data = request.get_json(silent=True) or {}
    settings = _load_mandant_settings()  # Bestehende Settings laden (inkl. Zaehler)
    allowed = ["name", "vat_id", "tax_registration_id", "street", "post_code",
               "city", "email", "contact_name", "contact_phone", "iban", "bic", "currency",
               "stb_email", "stb_name"]
    for k in allowed:
        if k in data:
            settings[k] = data[k]
    _save_mandant_settings(settings)
    return _json({"saved": True})


@app.route("/api/mandant/templates")
def api_mandant_templates():
    """Gibt Dokumentvorlagen zurück."""
    settings = _load_mandant_settings()
    return _json(settings.get("doc_templates", {}))


@app.route("/api/mandant/templates", methods=["POST"])
def api_mandant_templates_save():
    """Speichert Dokumentvorlagen."""
    data = request.get_json(silent=True) or {}
    settings = _load_mandant_settings()
    settings["doc_templates"] = data
    _save_mandant_settings(settings)
    return _json({"saved": True})


# ── API: Rechnungsempfaenger / Kunden ─────────────────────────────────

_BUYERS_FILE = _DATA / "data" / "buyers.json"

def _load_buyers() -> list[dict]:
    if _BUYERS_FILE.exists():
        try:
            return json.loads(_BUYERS_FILE.read_text("utf-8"))
        except Exception:
            pass
    return []

def _save_buyers(buyers: list[dict]):
    _BUYERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _BUYERS_FILE.write_text(json.dumps(buyers, indent=2, ensure_ascii=False), "utf-8")


def _generate_buyer_id(name: str) -> str:
    """Erzeugt eine 5-stellige Kunden-ID aus dem Firmennamen."""
    import hashlib
    h = hashlib.md5(name.strip().encode("utf-8")).hexdigest()
    digits = "".join(c for c in h if c.isdigit())
    if len(digits) >= 5:
        return digits[:5]
    return digits.ljust(5, "0")


def _save_buyer_if_new(name: str, street: str = "", post_code: str = "",
                       city: str = "", email: str = "", reference: str = "",
                       vat_id: str = "", salutation: str = "",
                       contact_name: str = "") -> dict | None:
    """Speichert einen Kaeufer wenn er noch nicht existiert.
    name = Firma (kann leer sein bei Privatpersonen)
    contact_name = Ansprechpartner (Vor- und Nachname)
    """
    display = name.strip() if name else contact_name.strip() if contact_name else ""
    if not display:
        return None

    buyers = _load_buyers()
    # Duplikat? Gleicher Anzeigename
    for b in buyers:
        b_display = b.get("name", "").strip() or b.get("contact_name", "").strip()
        if b_display.lower() == display.lower():
            return b
    buyer = {
        "id": _generate_buyer_id(display),
        "name": name.strip(),           # Firma (leer bei Privatpersonen)
        "street": street.strip(),
        "post_code": post_code.strip(),
        "city": city.strip(),
        "email": email.strip(),
        "reference": reference.strip(),
        "vat_id": vat_id.strip(),
        "salutation": salutation.strip(),
        "contact_name": contact_name.strip(),
    }
    buyers.append(buyer)
    _save_buyers(buyers)
    return buyer


@app.route("/api/buyers")
def api_buyers_list():
    """Gibt alle gespeicherten Rechnungsempfaenger zurueck."""
    return _json({"buyers": _load_buyers()})


@app.route("/api/buyers", methods=["POST"])
def api_buyers_add():
    """Fuegt einen Rechnungsempfaenger hinzu."""
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    contact_name = data.get("contact_name", "").strip()
    if not name and not contact_name:
        return _json({"error": "Firma oder Ansprechpartner erforderlich"}, 400)
    buyer = _save_buyer_if_new(
        name=name,
        street=data.get("street", ""),
        post_code=data.get("post_code", ""),
        city=data.get("city", ""),
        email=data.get("email", ""),
        reference=data.get("reference", ""),
        vat_id=data.get("vat_id", ""),
        salutation=data.get("salutation", ""),
        contact_name=contact_name,
    )
    return _json({"saved": True, "buyer": buyer})


@app.route("/api/buyers/<buyer_id>", methods=["PUT"])
def api_buyers_update(buyer_id):
    """Aktualisiert einen bestehenden Rechnungsempfaenger."""
    data = request.get_json(silent=True) or {}
    buyers = _load_buyers()
    found = False
    for b in buyers:
        if b.get("id") == buyer_id:
            for key in ["name", "street", "post_code", "city", "email",
                        "reference", "vat_id", "salutation", "contact_name"]:
                if key in data:
                    b[key] = data[key].strip() if isinstance(data[key], str) else data[key]
            found = True
            _save_buyers(buyers)
            return _json({"saved": True, "buyer": b})
    if not found:
        return _json({"error": "Empfaenger nicht gefunden"}, 404)


@app.route("/api/buyers/<buyer_id>", methods=["DELETE"])
def api_buyers_delete(buyer_id):
    """Loescht einen Rechnungsempfaenger."""
    buyers = _load_buyers()
    buyers = [b for b in buyers if b.get("id") != buyer_id]
    _save_buyers(buyers)
    return _json({"deleted": True})


@app.route("/api/buyers/all", methods=["DELETE"])
def api_buyers_delete_all():
    """Loescht alle Rechnungsempfaenger."""
    _save_buyers([])
    return _json({"deleted": True})


@app.route("/api/buyers/csv", methods=["POST"])
def api_buyers_csv_upload():
    """Importiert Rechnungsempfaenger aus einer CSV-Datei.
    Erwartetes Format (Semikolon-getrennt, mit oder ohne Header):
    Firma;Strasse;PLZ;Ort;E-Mail;Referenz;USt-ID
    """
    import csv, io

    file = request.files.get("file")
    if not file:
        return _json({"error": "Keine Datei hochgeladen"}, 400)

    try:
        text = file.read().decode("utf-8-sig")  # BOM-sicher
    except UnicodeDecodeError:
        try:
            file.seek(0)
            text = file.read().decode("latin-1")
        except Exception:
            return _json({"error": "Datei konnte nicht gelesen werden"}, 400)

    # Zeilenumbrüche normalisieren (Mac \r, Windows \r\n → Unix \n)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    buyers = _load_buyers()
    added = 0

    # Trennzeichen erkennen
    delimiter = ";" if ";" in text else ","

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header_map = None

    for row_num, row in enumerate(reader):
        if not row or all(not c.strip() for c in row):
            continue

        # Erste Zeile: Header erkennen?
        if row_num == 0:
            first_lower = [c.strip().lower() for c in row]
            known_headers = ["firma", "name", "strasse", "street", "plz", "post_code",
                             "ort", "city", "email", "e-mail", "referenz", "reference",
                             "buyer_reference", "ust-id", "vat_id", "ust_id",
                             "anrede", "salutation", "ansprechpartner", "contact_name", "kontakt"]
            if any(h in known_headers for h in first_lower):
                # Header-Zeile erkannt
                header_map = {}
                for i, h in enumerate(first_lower):
                    if h in ("firma", "name", "firmenname", "company"):
                        header_map["name"] = i
                    elif h in ("strasse", "street", "str"):
                        header_map["street"] = i
                    elif h in ("plz", "post_code", "postleitzahl", "zip"):
                        header_map["post_code"] = i
                    elif h in ("ort", "city", "stadt"):
                        header_map["city"] = i
                    elif h in ("email", "e-mail", "mail"):
                        header_map["email"] = i
                    elif h in ("referenz", "reference", "buyer_reference", "ref", "leitweg"):
                        header_map["reference"] = i
                    elif h in ("ust-id", "vat_id", "ust_id", "ustid", "vat"):
                        header_map["vat_id"] = i
                    elif h in ("anrede", "salutation", "titel"):
                        header_map["salutation"] = i
                    elif h in ("ansprechpartner", "contact_name", "kontakt", "contact"):
                        header_map["contact_name"] = i
                continue

        # Daten-Zeile
        def col(idx):
            return row[idx].strip() if idx is not None and idx < len(row) else ""

        if header_map:
            buyer = {
                "id": _generate_buyer_id(col(header_map.get("name"))),
                "name": col(header_map.get("name")),
                "street": col(header_map.get("street")),
                "post_code": col(header_map.get("post_code")),
                "city": col(header_map.get("city")),
                "email": col(header_map.get("email")),
                "reference": col(header_map.get("reference")),
                "vat_id": col(header_map.get("vat_id")),
                "salutation": col(header_map.get("salutation")),
                "contact_name": col(header_map.get("contact_name")),
            }
        else:
            # Ohne Header: Firma;Strasse;PLZ;Ort;Email;Referenz;USt-ID;Anrede;Ansprechpartner
            buyer = {
                "id": _generate_buyer_id(col(0)),
                "name": col(0),
                "street": col(1),
                "post_code": col(2),
                "city": col(3),
                "email": col(4),
                "reference": col(5),
                "vat_id": col(6),
                "salutation": col(7),
                "contact_name": col(8),
            }

        # Anzeigename fuer Duplikatpruefung: Firma oder Ansprechpartner
        display = buyer.get("name", "").strip() or buyer.get("contact_name", "").strip()
        if display:
            if not any(
                (b.get("name","").strip() or b.get("contact_name","").strip()).lower() == display.lower()
                for b in buyers
            ):
                if not buyer.get("id") or buyer["id"] == _generate_buyer_id(""):
                    buyer["id"] = _generate_buyer_id(display)
                buyers.append(buyer)
                added += 1

    _save_buyers(buyers)
    return _json({"imported": added, "total": len(buyers)})


@app.route("/api/buyers/csv/export")
def api_buyers_csv_export():
    """Exportiert alle Kunden als CSV."""
    import csv, io
    buyers = _load_buyers()
    out = io.StringIO()
    out.write("\ufeff")
    w = csv.writer(out, delimiter=";")
    w.writerow(["Firma", "Strasse", "PLZ", "Ort", "E-Mail", "Referenz", "USt-ID", "Anrede", "Ansprechpartner"])
    for b in buyers:
        w.writerow([b.get("name",""), b.get("street",""), b.get("post_code",""),
                    b.get("city",""), b.get("email",""), b.get("reference",""),
                    b.get("vat_id",""), b.get("salutation",""), b.get("contact_name","")])
    return app.response_class(out.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=kunden_export.csv"})


# ── API: Lieferanten (Auftragsmanagement) ─────────────────────────────

@app.route("/api/suppliers")
def api_suppliers_list():
    return _json(supplier_mgr.list_all())


@app.route("/api/suppliers", methods=["POST"])
def api_suppliers_add():
    data = request.json or {}
    try:
        supplier = supplier_mgr.add(data)
        return _json({"saved": True, "supplier": supplier})
    except ValueError as e:
        return _json({"error": str(e)}, 400)


@app.route("/api/suppliers/<sid>", methods=["PUT"])
def api_suppliers_update(sid):
    data = request.json or {}
    result = supplier_mgr.update(sid, data)
    if result:
        return _json({"saved": True, "supplier": result})
    return _json({"error": "Lieferant nicht gefunden"}, 404)


@app.route("/api/suppliers/<sid>", methods=["DELETE"])
def api_suppliers_delete(sid):
    if supplier_mgr.delete(sid):
        return _json({"deleted": True})
    return _json({"error": "Lieferant nicht gefunden"}, 404)


@app.route("/api/suppliers/all", methods=["DELETE"])
def api_suppliers_delete_all():
    count = supplier_mgr.delete_all()
    return _json({"deleted": count})


@app.route("/api/suppliers/<sid>/approve", methods=["POST"])
def api_suppliers_approve(sid):
    data = request.json or {}
    result = supplier_mgr.approve(sid, data.get("user", "system"), data.get("comment", ""))
    if result:
        return _json({"approved": True, "supplier": result})
    return _json({"error": "Lieferant nicht gefunden"}, 404)


@app.route("/api/suppliers/<sid>/unapprove", methods=["POST"])
def api_suppliers_unapprove(sid):
    result = supplier_mgr.unapprove(sid)
    if result:
        return _json({"approved": False, "supplier": result})
    return _json({"error": "Lieferant nicht gefunden"}, 404)


@app.route("/api/suppliers/csv", methods=["POST"])
def api_suppliers_csv_upload():
    file = request.files.get("file")
    if not file:
        return _json({"error": "Keine Datei hochgeladen"}, 400)
    result = supplier_mgr.import_csv(file.read())
    return _json(result)


@app.route("/api/suppliers/csv/export")
def api_suppliers_csv_export():
    csv_str = supplier_mgr.export_csv()
    return app.response_class(csv_str, mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=lieferanten_export.csv"})


# ── API: Lieferanten-Dokumente ────────────────────────────────────────

@app.route("/api/suppliers/<sid>/documents", methods=["POST"])
def api_supplier_doc_upload(sid):
    """Lädt ein Dokument hoch und verknüpft es mit einem Lieferanten."""
    sup = supplier_mgr.get(sid)
    if not sup:
        return _json({"error": "Lieferant nicht gefunden"}, 404)

    file = request.files.get("file")
    doc_type = request.form.get("type", "sonstiges")
    if not file or not file.filename:
        return _json({"error": "Keine Datei"}, 400)

    import hashlib
    from werkzeug.utils import secure_filename

    doc_dir = _DATA / "data" / "supplier_docs" / sid / doc_type
    doc_dir.mkdir(parents=True, exist_ok=True)

    filename = secure_filename(file.filename) or "dokument.pdf"
    filepath = doc_dir / filename
    counter = 1
    stem, ext = (filename.rsplit(".", 1) + [""])[:2]
    while filepath.exists():
        filename = f"{stem}_{counter}.{ext}" if ext else f"{stem}_{counter}"
        filepath = doc_dir / filename
        counter += 1

    file_bytes = file.read()
    filepath.write_bytes(file_bytes)
    sha = hashlib.sha256(file_bytes).hexdigest()[:16]

    # Index aktualisieren
    idx_file = _DATA / "data" / "supplier_docs" / sid / "index.json"
    idx = []
    if idx_file.exists():
        try:
            idx = json.loads(idx_file.read_text("utf-8"))
        except Exception:
            pass

    entry = {
        "filename": filename,
        "original_name": file.filename,
        "type": doc_type,
        "size": len(file_bytes),
        "hash": sha,
        "uploaded_at": date.today().isoformat(),
        "supplier_id": sid,
        "supplier_name": sup.get("name", ""),
    }
    idx.append(entry)
    idx_file.write_text(json.dumps(idx, indent=2, ensure_ascii=False), "utf-8")

    return _json({"uploaded": True, "document": entry})


@app.route("/api/suppliers/<sid>/documents")
def api_supplier_docs_list(sid):
    """Liste aller Dokumente eines Lieferanten."""
    idx_file = _DATA / "data" / "supplier_docs" / sid / "index.json"
    if not idx_file.exists():
        return _json([])
    try:
        return _json(json.loads(idx_file.read_text("utf-8")))
    except Exception:
        return _json([])


@app.route("/api/suppliers/<sid>/documents/<doc_type>/<filename>")
def api_supplier_doc_download(sid, doc_type, filename):
    """Download eines Lieferanten-Dokuments."""
    from flask import send_from_directory
    doc_dir = _DATA / "data" / "supplier_docs" / sid / doc_type
    filepath = doc_dir / filename
    if not filepath.exists():
        return _json({"error": "Datei nicht gefunden"}, 404)
    return send_from_directory(str(doc_dir), filename)


@app.route("/api/suppliers/<sid>/documents/<doc_type>/<filename>", methods=["DELETE"])
def api_supplier_doc_delete(sid, doc_type, filename):
    """Löscht ein Lieferanten-Dokument."""
    doc_dir = _DATA / "data" / "supplier_docs" / sid / doc_type
    filepath = doc_dir / filename
    if filepath.exists():
        filepath.unlink()
    idx_file = _DATA / "data" / "supplier_docs" / sid / "index.json"
    if idx_file.exists():
        try:
            idx = json.loads(idx_file.read_text("utf-8"))
            idx = [d for d in idx if not (d["filename"] == filename and d["type"] == doc_type)]
            idx_file.write_text(json.dumps(idx, indent=2, ensure_ascii=False), "utf-8")
        except Exception:
            pass
    return _json({"deleted": True})


# ── API: Vorgänge / Auftragsmanagement ────────────────────────────────

@app.route("/api/transactions")
def api_txn_list():
    return _json(txn_mgr.list_all(
        status=request.args.get("status"),
        supplier_id=request.args.get("supplier_id"),
        buyer_id=request.args.get("buyer_id"),
    ))


@app.route("/api/transactions/stats")
def api_txn_stats():
    return _json(txn_mgr.stats())


@app.route("/api/transactions", methods=["POST"])
def api_txn_create():
    data = request.json or {}
    txn = txn_mgr.create(data)
    return _json({"created": True, "transaction": txn})


@app.route("/api/transactions/<tid>")
def api_txn_get(tid):
    txn = txn_mgr.get(tid)
    if txn:
        txn["_current_step"] = txn_mgr.current_step(txn)
        return _json(txn)
    return _json({"error": "Vorgang nicht gefunden"}, 404)


@app.route("/api/transactions/<tid>", methods=["PUT"])
def api_txn_update(tid):
    data = request.json or {}
    result = txn_mgr.update(tid, data)
    if result:
        return _json({"saved": True, "transaction": result})
    return _json({"error": "Vorgang nicht gefunden"}, 404)


@app.route("/api/transactions/<tid>", methods=["DELETE"])
def api_txn_delete(tid):
    if txn_mgr.delete(tid):
        return _json({"deleted": True})
    return _json({"error": "Vorgang nicht gefunden"}, 404)


@app.route("/api/transactions/<tid>/steps/<step_key>", methods=["PUT"])
def api_txn_step_update(tid, step_key):
    data = request.json or {}
    try:
        txn = txn_mgr.update_step(tid, step_key, data, data.get("_user", "system"))
        return _json({"saved": True, "transaction": txn})
    except ValueError as e:
        return _json({"error": str(e)}, 400)


@app.route("/api/transactions/<tid>/steps/<step_key>/approve", methods=["POST"])
def api_txn_step_approve(tid, step_key):
    data = request.json or {}
    try:
        txn = txn_mgr.approve_step(tid, step_key,
                                    data.get("user", "system"),
                                    data.get("comment", ""))
        return _json({"approved": True, "transaction": txn})
    except ValueError as e:
        return _json({"error": str(e)}, 400)


@app.route("/api/transactions/<tid>/steps/<step_key>/unapprove", methods=["POST"])
def api_txn_step_unapprove(tid, step_key):
    data = request.json or {}
    try:
        txn = txn_mgr.unapprove_step(tid, step_key, data.get("user", "system"))
        return _json({"unapproved": True, "transaction": txn})
    except ValueError as e:
        return _json({"error": str(e)}, 400)


@app.route("/api/transactions/<tid>/steps/<step_key>/skip", methods=["POST"])
def api_txn_step_skip(tid, step_key):
    data = request.json or {}
    try:
        txn = txn_mgr.skip_step(tid, step_key,
                                 data.get("user", "system"),
                                 data.get("reason", ""))
        return _json({"skipped": True, "transaction": txn})
    except ValueError as e:
        return _json({"error": str(e)}, 400)


@app.route("/api/transactions/<tid>/deliveries", methods=["POST"])
def api_txn_add_delivery(tid):
    data = request.json or {}
    try:
        txn = txn_mgr.add_delivery(tid, data, data.get("_user", "system"))
        return _json({"added": True, "transaction": txn})
    except ValueError as e:
        return _json({"error": str(e)}, 400)


@app.route("/api/transactions/<tid>/deliveries/<did>/approve", methods=["POST"])
def api_txn_approve_delivery(tid, did):
    data = request.json or {}
    try:
        txn = txn_mgr.approve_delivery(tid, did, data.get("user", "system"))
        return _json({"approved": True, "transaction": txn})
    except ValueError as e:
        return _json({"error": str(e)}, 400)


@app.route("/api/transactions/<tid>/timeline")
def api_txn_timeline(tid):
    return _json(txn_mgr.get_timeline(tid))


@app.route("/api/transactions/step-labels")
def api_txn_step_labels():
    return _json({"keys": STEP_KEYS, "labels": STEP_LABELS, "prefixes": DOC_PREFIXES})


# ── API: Dokument-Erzeugung (Phase 2) ────────────────────────────────

@app.route("/api/transactions/<tid>/steps/<step_key>/generate-pdf", methods=["POST"])
def api_txn_generate_pdf(tid, step_key):
    """Erzeugt ein PDF-Dokument für einen Workflow-Step."""
    txn = txn_mgr.get(tid)
    if not txn:
        return _json({"error": "Vorgang nicht gefunden"}, 404)

    step = txn["steps"].get(step_key, {})
    data = request.json or {}

    # Empfänger bestimmen
    if step_key in ("supplier_quote", "purchase_order"):
        # Dokumente an Lieferant
        sup = supplier_mgr.get(txn.get("supplier_id", ""))
        recipient = sup if sup else {"name": txn.get("supplier_name", "")}
    else:
        # Dokumente an Kunde
        buyers = _load_buyers()
        buyer = next((b for b in buyers if b.get("id") == txn.get("buyer_id")), None)
        recipient = buyer if buyer else {"name": txn.get("buyer_name", "")}

    # Referenz – entweder schon vorhanden oder neu generieren
    ref = step.get("reference") or ""
    if not ref:
        prefix = DOC_PREFIXES.get(step_key, "DOK")
        ref = txn_mgr.numbers.next(prefix)
        # Step aktualisieren
        txn_mgr.update_step(tid, step_key, {"reference": ref})

    # Dunning-Sonderdaten
    dunning_level = data.get("dunning_level", 1)
    inv_ref = data.get("original_invoice_ref", "")
    inv_amount = float(data.get("original_invoice_amount", 0))
    dunning_fee = float(data.get("dunning_fee", 0))

    # Dokumentvorlagen laden
    templates = _load_mandant_settings().get("doc_templates", {})
    tpl_key = f"dunning_{dunning_level}" if step_key == "dunning" else step_key
    intro = data.get("intro_text", "") or templates.get(f"{tpl_key}_intro", "")
    closing = data.get("closing_text", "") or templates.get(f"{tpl_key}_closing", "")

    try:
        doc_entry = doc_gen.generate(
            doc_type=step_key if step_key != "dunning" else "dunning",
            recipient=recipient,
            positions=step.get("positions", []),
            step=step,
            reference=ref,
            subject=txn.get("subject", ""),
            doc_date=step.get("date") or data.get("date", ""),
            due_date=step.get("due_date") or data.get("due_date", ""),
            transaction_id=tid,
            step_key=step_key,
            dunning_level=dunning_level,
            original_invoice_ref=inv_ref,
            original_invoice_amount=inv_amount,
            dunning_fee=dunning_fee,
            intro_text=intro,
            closing_text=closing,
        )

        # Step mit Dokument-ID verknüpfen
        txn_mgr.update_step(tid, step_key, {
            "document_id": doc_entry["id"],
            "reference": ref,
        })

        return _json({
            "generated": True,
            "document": doc_entry,
            "download_url": f"/api/documents/{doc_entry['filename']}",
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return _json({"error": f"PDF-Erzeugung fehlgeschlagen: {e}"}, 500)


@app.route("/api/documents/<filename>")
def api_document_download(filename):
    """Liefert eine erzeugte PDF-Datei zum Download."""
    from flask import send_from_directory
    docs_dir = _DATA / "data" / "documents"
    filepath = docs_dir / filename
    if not filepath.exists():
        return _json({"error": "Dokument nicht gefunden"}, 404)
    return send_from_directory(str(docs_dir), filename, as_attachment=False)


@app.route("/api/documents")
def api_documents_list():
    """Liste aller erzeugten Dokumente, optional nach Vorgang gefiltert."""
    tid = request.args.get("transaction_id", "")
    return _json(doc_gen.list_docs(tid))


# ── API: Dokument-Upload pro Step ─────────────────────────────────────

@app.route("/api/transactions/<tid>/steps/<step_key>/attachments", methods=["POST"])
def api_txn_step_upload(tid, step_key):
    """Lädt ein Dokument hoch und verknüpft es mit einem Step."""
    txn = txn_mgr.get(tid)
    if not txn:
        return _json({"error": "Vorgang nicht gefunden"}, 404)

    file = request.files.get("file")
    if not file or not file.filename:
        return _json({"error": "Keine Datei hochgeladen"}, 400)

    import hashlib
    # Speichern
    att_dir = _DATA / "data" / "attachments" / tid / step_key
    att_dir.mkdir(parents=True, exist_ok=True)

    # Sicherer Dateiname
    from werkzeug.utils import secure_filename
    filename = secure_filename(file.filename) or "dokument.pdf"
    filepath = att_dir / filename
    # Bei Duplikat: Nummer anhängen
    counter = 1
    stem, ext = (filename.rsplit(".", 1) + [""])[:2]
    while filepath.exists():
        filename = f"{stem}_{counter}.{ext}" if ext else f"{stem}_{counter}"
        filepath = att_dir / filename
        counter += 1

    file_bytes = file.read()
    filepath.write_bytes(file_bytes)
    sha = hashlib.sha256(file_bytes).hexdigest()[:16]

    # In Step-Metadaten speichern
    step = txn["steps"].get(step_key, {})
    if "attachments" not in step:
        step["attachments"] = []
    att_entry = {
        "filename": filename,
        "original_name": file.filename,
        "size": len(file_bytes),
        "hash": sha,
        "uploaded_at": date.today().isoformat(),
        "path": str(filepath),
    }
    step["attachments"].append(att_entry)
    txn_mgr.update_step(tid, step_key, {"attachments": step["attachments"]})

    return _json({"uploaded": True, "attachment": att_entry})


@app.route("/api/transactions/<tid>/steps/<step_key>/attachments")
def api_txn_step_attachments(tid, step_key):
    """Liste der Anhänge eines Steps."""
    txn = txn_mgr.get(tid)
    if not txn:
        return _json({"error": "Vorgang nicht gefunden"}, 404)
    step = txn["steps"].get(step_key, {})
    return _json(step.get("attachments", []))


@app.route("/api/transactions/<tid>/steps/<step_key>/attachments/<filename>")
def api_txn_step_attachment_download(tid, step_key, filename):
    """Download eines Anhangs."""
    att_dir = _DATA / "data" / "attachments" / tid / step_key
    filepath = att_dir / filename
    if not filepath.exists():
        return _json({"error": "Datei nicht gefunden"}, 404)
    from flask import send_from_directory
    return send_from_directory(str(att_dir), filename)


@app.route("/api/transactions/<tid>/steps/<step_key>/attachments/<filename>", methods=["DELETE"])
def api_txn_step_attachment_delete(tid, step_key, filename):
    """Löscht einen Anhang."""
    txn = txn_mgr.get(tid)
    if not txn:
        return _json({"error": "Vorgang nicht gefunden"}, 404)
    att_dir = _DATA / "data" / "attachments" / tid / step_key
    filepath = att_dir / filename
    if filepath.exists():
        filepath.unlink()
    step = txn["steps"].get(step_key, {})
    atts = step.get("attachments", [])
    atts = [a for a in atts if a["filename"] != filename]
    txn_mgr.update_step(tid, step_key, {"attachments": atts})
    return _json({"deleted": True})


# ── API: E-Rechnung aus Vorgang erzeugen (Phase 3) ───────────────────

@app.route("/api/transactions/<tid>/generate-invoice", methods=["POST"])
def api_txn_generate_invoice(tid):
    """Erzeugt eine XRechnung aus den Daten eines Vorgangs (Stufe 7)."""
    txn = txn_mgr.get(tid)
    if not txn:
        return _json({"error": "Vorgang nicht gefunden"}, 404)

    inv_step = txn["steps"].get("invoice", {})
    data = request.json or {}

    # Verkäufer aus Mandanteneinstellungen
    ms = _load_mandant_settings()

    # Käufer aus buyers.json
    buyers = _load_buyers()
    buyer_data = next((b for b in buyers if b.get("id") == txn.get("buyer_id")), None)
    if not buyer_data:
        buyer_data = {"name": txn.get("buyer_name", ""), "street": "", "post_code": "", "city": "", "email": "", "reference": ""}

    # Rechnungsnummer
    number = inv_step.get("reference", "")
    if not number:
        number = _next_invoice_number(advance=True)

    # Positionen aus Step
    positions = inv_step.get("positions", [])
    if not positions:
        return _json({"error": "Keine Positionen in Stufe 7 (E-Rechnung) erfasst."}, 400)

    # Rechnungsdatum / Fälligkeit
    inv_date_str = inv_step.get("date") or data.get("date", date.today().isoformat())
    inv_date = date.fromisoformat(inv_date_str[:10])
    payment_terms = data.get("payment_terms", "Zahlbar innerhalb von 30 Tagen")
    import re as _re
    _m = _re.search(r'(\d+)\s*Tag', payment_terms)
    due_date = inv_date + timedelta(days=int(_m.group(1))) if _m else inv_date + timedelta(days=30)

    try:
        inv = Invoice(
            invoice_number=number,
            invoice_date=inv_date,
            invoice_type_code=data.get("type", "380"),
            currency_code="EUR",
            buyer_reference=buyer_data.get("reference", "") or txn.get("buyer_name", ""),
            note=data.get("note", f"Vorgang {txn['id']}: {txn.get('subject', '')}"),
            seller=Seller(
                name=ms.get("name", ""),
                address=Address(ms.get("street", ""), ms.get("city", ""), ms.get("post_code", "")),
                electronic_address=ms.get("email", ""),
                electronic_address_scheme="EM",
                contact=Contact(ms.get("contact_name", ""), ms.get("contact_phone", ""), ms.get("email", "")),
                vat_id=ms.get("vat_id", ""),
                tax_registration_id=ms.get("tax_registration_id", ""),
            ),
            buyer=Buyer(
                name=buyer_data.get("name", ""),
                address=Address(buyer_data.get("street", ""), buyer_data.get("city", ""), buyer_data.get("post_code", "")),
                electronic_address=buyer_data.get("email", "") or "buyer@example.com",
                electronic_address_scheme="EM",
                buyer_reference=buyer_data.get("reference", ""),
                vat_id=buyer_data.get("vat_id", ""),
            ),
            payment=PaymentInfo(
                means_code=data.get("payment_code", "58"),
                iban=ms.get("iban", ""),
                payment_terms=payment_terms,
                due_date=due_date,
            ),
        )

        for p in positions:
            inv.lines.append(InvoiceLine(
                line_id=str(p.get("pos_nr", 1)),
                quantity=Decimal(str(p.get("quantity", 1))),
                item_name=p.get("description", ""),
                unit_price=Decimal(str(p.get("unit_price", 0))),
                line_net_amount=Decimal(str(p.get("net_amount", 0))),
                tax_rate=Decimal(str(p.get("tax_rate", 19))),
            ))

        # Duplikat-Check
        if any(v.invoice_number == inv.invoice_number for v in invoices.values()):
            return _json({"error": f"Rechnungsnummer {inv.invoice_number} existiert bereits."}, 409)

        # Validieren + XML erzeugen
        report = validate_invoice(inv)
        xml_bytes = generate_and_serialize(inv)
        inv._direction = "AUSGANG"

        # Im Rechnungssystem registrieren
        invoices[inv._id] = inv
        inbox.duplicates.register(inv, inv._id)
        archive.archive_invoice(inv, xml_bytes, report, direction="AUSGANG")
        auto_save()

        # Step im Vorgang aktualisieren
        txn_mgr.update_step(tid, "invoice", {
            "reference": number,
            "date": inv_date_str,
            "due_date": due_date.isoformat(),
            "amount": float(inv.tax_inclusive_amount()),
            "document_id": inv._id,
        })

        # ZUGFeRD optional
        zugferd_bytes = None
        try:
            zugferd_bytes = generate_zugferd_pdf(inv, xml_bytes)
        except Exception:
            pass

        result = {
            "generated": True,
            "invoice_id": inv._id,
            "number": inv.invoice_number,
            "transaction_id": tid,
            "valid": report.is_valid,
            "errors": report.error_count,
            "gross": float(inv.tax_inclusive_amount()),
            "xml_size": len(xml_bytes),
            "xml_b64": __import__("base64").b64encode(xml_bytes).decode(),
        }
        if zugferd_bytes:
            result["zugferd_size"] = len(zugferd_bytes)

        return _json(result)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return _json({"error": str(e)}, 400)


# ── API: Mahnwesen (Phase 3) ─────────────────────────────────────────

@app.route("/api/dunning/rules")
def api_dunning_rules():
    return _json(dunning_mgr.get_rules())


@app.route("/api/dunning/rules", methods=["PUT"])
def api_dunning_rules_save():
    data = request.json or {}
    dunning_mgr.save_rules(data)
    return _json({"saved": True})


@app.route("/api/dunning/check", methods=["POST"])
def api_dunning_check():
    """Prüft alle Vorgänge auf überfällige Rechnungen."""
    txns = txn_mgr.list_all()
    summary = dunning_mgr.get_overdue_summary(txns)
    return _json(summary)


@app.route("/api/dunning/overdue")
def api_dunning_overdue():
    """Gibt alle überfälligen Rechnungen zurück."""
    txns = txn_mgr.list_all()
    invoices_data = dunning_mgr.collect_invoices_from_transactions(txns)
    overdue = dunning_mgr.check_overdue(invoices_data)
    return _json(overdue)


# ── API: Produktkatalog ───────────────────────────────────────────────

@app.route("/api/products")
def api_products_list():
    category = request.args.get("category", "")
    return _json(product_mgr.list_all(category=category, active_only=False))


@app.route("/api/products/categories")
def api_products_categories():
    return _json(product_mgr.get_categories())


@app.route("/api/products", methods=["POST"])
def api_products_add():
    data = request.json or {}
    try:
        product = product_mgr.add(data)
        return _json({"saved": True, "product": product})
    except ValueError as e:
        return _json({"error": str(e)}, 400)


@app.route("/api/products/<art_nr>", methods=["PUT"])
def api_products_update(art_nr):
    data = request.json or {}
    result = product_mgr.update(art_nr, data)
    if result:
        return _json({"saved": True, "product": result})
    return _json({"error": "Produkt nicht gefunden"}, 404)


@app.route("/api/products/<art_nr>", methods=["DELETE"])
def api_products_delete(art_nr):
    if product_mgr.delete(art_nr):
        return _json({"deleted": True})
    return _json({"error": "Produkt nicht gefunden"}, 404)


@app.route("/api/products/all", methods=["DELETE"])
def api_products_delete_all():
    count = product_mgr.delete_all()
    return _json({"deleted": count})


@app.route("/api/products/<art_nr>/stock", methods=["POST"])
def api_products_stock(art_nr):
    data = request.json or {}
    delta = int(data.get("delta", 0))
    result = product_mgr.adjust_stock(art_nr, delta)
    if result:
        return _json({"adjusted": True, "product": result})
    return _json({"error": "Produkt nicht gefunden"}, 404)


@app.route("/api/products/csv", methods=["POST"])
def api_products_csv():
    file = request.files.get("file")
    if not file:
        return _json({"error": "Keine Datei"}, 400)
    result = product_mgr.import_csv(file.read())
    return _json(result)


@app.route("/api/products/csv/export")
def api_products_csv_export():
    csv_str = product_mgr.export_csv()
    return app.response_class(csv_str, mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=produkte_export.csv"})


@app.route("/api/products/demo", methods=["POST"])
def api_products_demo():
    count = create_demo_products(str(_DATA / "data"))
    return _json({"created": count})


# ── API: Freigabe + Mail senden ──────────────────────────────────────

@app.route("/api/transactions/<tid>/steps/<step_key>/approve-and-send", methods=["POST"])
def api_txn_approve_and_send(tid, step_key):
    """Freigeben + PDF erzeugen + per E-Mail an Empfänger senden."""
    import smtplib, ssl
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.mime.application import MIMEApplication

    data = request.json or {}

    # 1. Freigeben
    try:
        txn = txn_mgr.approve_step(tid, step_key,
                                    data.get("user", "system"),
                                    data.get("comment", ""))
    except ValueError as e:
        return _json({"error": str(e)}, 400)

    # 2. PDF erzeugen
    step = txn["steps"].get(step_key, {})
    if step_key in ("supplier_quote", "purchase_order"):
        sup = supplier_mgr.get(txn.get("supplier_id", ""))
        recipient = sup if sup else {"name": txn.get("supplier_name", "")}
    else:
        buyers = _load_buyers()
        buyer = next((b for b in buyers if b.get("id") == txn.get("buyer_id")), None)
        recipient = buyer if buyer else {"name": txn.get("buyer_name", "")}

    ref = step.get("reference", "")
    # Dokumentvorlagen laden
    templates = _load_mandant_settings().get("doc_templates", {})
    tpl_key = step_key
    intro = step.get("intro_text", "") or templates.get(f"{tpl_key}_intro", "")
    closing = step.get("closing_text", "") or templates.get(f"{tpl_key}_closing", "")
    try:
        doc_type = step_key if step_key != "dunning" else "dunning"
        doc_entry = doc_gen.generate(
            doc_type=doc_type, recipient=recipient,
            positions=step.get("positions", []), step=step,
            reference=ref, subject=txn.get("subject", ""),
            doc_date=step.get("date", ""), due_date=step.get("due_date", ""),
            transaction_id=tid, step_key=step_key,
            intro_text=intro, closing_text=closing,
        )
    except Exception as e:
        return _json({"approved": True, "sent": False, "error": f"PDF-Fehler: {e}"})

    # 3. E-Mail senden
    to_email = data.get("to_email", "") or recipient.get("email", "")
    if not to_email:
        return _json({"approved": True, "sent": False,
                       "error": "Keine E-Mail-Adresse beim Empfänger hinterlegt.",
                       "document": doc_entry})

    ms = _load_mandant_settings()
    _email_cfg_path = _DATA / "data" / "email_config.json"
    smtp_cfg = {}
    if _email_cfg_path.exists():
        try:
            smtp_cfg = json.loads(_email_cfg_path.read_text("utf-8"))
        except Exception:
            pass

    smtp_host = smtp_cfg.get("smtp_host", "")
    smtp_port = int(smtp_cfg.get("smtp_port", 587))
    smtp_user = smtp_cfg.get("smtp_user", "")
    smtp_pass = smtp_cfg.get("smtp_password", "")
    from_addr = smtp_cfg.get("smtp_from_address", ms.get("email", ""))
    from_name = smtp_cfg.get("smtp_from_name", ms.get("name", "E-Rechnungssystem"))

    if not smtp_host or not smtp_user:
        return _json({"approved": True, "sent": False,
                       "error": "SMTP nicht konfiguriert. PDF wurde erzeugt.",
                       "document": doc_entry})

    # Mail bauen
    doc_titles = {
        "supplier_quote": "Angebotsanfrage",
        "purchase_order": "Bestellung",
        "customer_quote": "Angebot",
        "order_intake": "Auftragsbestätigung",
        "delivery_note": "Lieferschein",
        "invoice": "Rechnung",
        "dunning": "Zahlungserinnerung",
    }
    doc_title = doc_titles.get(step_key, "Dokument")
    r_name = recipient.get("name", "")

    msg = MIMEMultipart()
    msg["From"] = f"{from_name} <{from_addr}>"
    msg["To"] = to_email
    msg["Subject"] = f"{doc_title} {ref} – {ms.get('name', '')}"

    body = (f"Sehr geehrte Damen und Herren,\n\n"
            f"anbei erhalten Sie unsere {doc_title} {ref}.\n\n"
            f"Bei Fragen stehen wir Ihnen gerne zur Verfügung.\n\n"
            f"Mit freundlichen Grüßen\n{ms.get('name', '')}")
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # PDF anhängen
    pdf_path = doc_gen.get_filepath(doc_entry["filename"])
    if pdf_path:
        with open(pdf_path, "rb") as f:
            att = MIMEApplication(f.read(), _subtype="pdf")
            att.add_header("Content-Disposition", "attachment",
                           filename=doc_entry["filename"])
            msg.attach(att)

    try:
        if smtp_port == 465:
            ctx = ssl.create_default_context()
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15, context=ctx)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=15)
            server.ehlo()
            server.starttls()
            server.ehlo()
        if smtp_pass:
            server.login(smtp_user, smtp_pass)
        server.send_message(msg)
        server.quit()

        return _json({"approved": True, "sent": True, "sent_to": to_email,
                       "document": doc_entry})
    except Exception as e:
        return _json({"approved": True, "sent": False,
                       "error": f"Mail-Versand fehlgeschlagen: {e}",
                       "document": doc_entry})


# ── API: Bestandsbuchung bei Wareneingang/Lieferschein ────────────────

@app.route("/api/transactions/<tid>/stock-in", methods=["POST"])
def api_txn_stock_in(tid):
    """Wareneingang: Bestand für alle Positionen mit art_nr erhöhen."""
    txn = txn_mgr.get(tid)
    if not txn:
        return _json({"error": "Vorgang nicht gefunden"}, 404)
    step = txn["steps"].get("purchase_order", {})
    items = [{"art_nr": p.get("art_nr",""), "quantity": p.get("quantity",0)}
             for p in step.get("positions", []) if p.get("art_nr")]
    if items:
        product_mgr.bulk_stock_in(items)
    return _json({"adjusted": len(items)})


@app.route("/api/transactions/<tid>/stock-out", methods=["POST"])
def api_txn_stock_out(tid):
    """Warenausgang bei Lieferschein: Bestand reduzieren."""
    txn = txn_mgr.get(tid)
    if not txn:
        return _json({"error": "Vorgang nicht gefunden"}, 404)
    # Positionen aus delivery_note oder invoice
    step = txn["steps"].get("delivery_note", {})
    positions = step.get("positions", [])
    if not positions:
        step = txn["steps"].get("invoice", {})
        positions = step.get("positions", [])
    items = [{"art_nr": p.get("art_nr",""), "quantity": p.get("quantity",0)}
             for p in positions if p.get("art_nr")]
    if items:
        product_mgr.bulk_stock_out(items)
    return _json({"adjusted": len(items)})


# ── API: Steuerberater-Export ──────────────────────────────────────────

@app.route("/api/steuerberater/belege")
def api_stb_belege():
    """Sammelt alle Rechnungen für einen Zeitraum."""
    von = request.args.get("von", "")
    bis = request.args.get("bis", "")

    belege = []

    # 1. Eingangsrechnungen aus Archiv
    archiv_dir = _DATA / "data" / "archiv"
    if archiv_dir.exists():
        idx_file = archiv_dir / "index.json"
        if idx_file.exists():
            try:
                idx = json.loads(idx_file.read_text("utf-8"))
                for entry in idx:
                    inv_date = entry.get("invoice_date", entry.get("archived_at", ""))[:10]
                    if von and inv_date < von:
                        continue
                    if bis and inv_date > bis:
                        continue
                    # PDF suchen
                    inv_id = entry.get("id", "")
                    inv_dir = archiv_dir / inv_id
                    pdf_file = None
                    xml_file = None
                    if inv_dir.exists():
                        for f in inv_dir.iterdir():
                            if f.suffix.lower() == ".pdf":
                                pdf_file = str(f)
                            elif f.suffix.lower() == ".xml":
                                xml_file = str(f)
                    belege.append({
                        "typ": "Eingang",
                        "nummer": entry.get("invoice_number", ""),
                        "datum": inv_date,
                        "lieferant": entry.get("seller_name", ""),
                        "kunde": entry.get("buyer_name", ""),
                        "betrag": entry.get("total_gross", 0),
                        "pdf": pdf_file,
                        "xml": xml_file,
                        "id": inv_id,
                    })
            except Exception:
                pass

    # 2. Ausgangsrechnungen aus Dokumenten-Index
    docs_dir = _DATA / "data" / "documents"
    doc_idx_file = docs_dir / "index.json"
    if doc_idx_file.exists():
        try:
            doc_idx = json.loads(doc_idx_file.read_text("utf-8"))
            for doc in doc_idx:
                if doc.get("type") != "invoice":
                    continue
                doc_date = doc.get("created_at", "")[:10]
                if von and doc_date < von:
                    continue
                if bis and doc_date > bis:
                    continue
                belege.append({
                    "typ": "Ausgang",
                    "nummer": doc.get("reference", ""),
                    "datum": doc_date,
                    "lieferant": "",
                    "kunde": doc.get("transaction_id", ""),
                    "betrag": 0,
                    "pdf": doc.get("filepath", ""),
                    "xml": None,
                    "id": doc.get("id", ""),
                })
        except Exception:
            pass

    # 3. Workflow-Rechnungen (invoice-Steps mit Anhängen)
    try:
        txns = txn_mgr.list_all()
        for txn in txns:
            inv_step = txn.get("steps", {}).get("invoice", {})
            if not inv_step.get("approved"):
                continue
            inv_date = inv_step.get("date", "")[:10]
            if von and inv_date < von:
                continue
            if bis and inv_date > bis:
                continue
            ref = inv_step.get("reference", "")
            # Prüfen ob schon in der Liste
            if any(b["nummer"] == ref and b["typ"] == "Ausgang" for b in belege):
                continue
            amount = inv_step.get("amount", 0)
            pdf_path = str(docs_dir / f"{ref}.pdf") if ref else ""
            belege.append({
                "typ": "Ausgang",
                "nummer": ref,
                "datum": inv_date,
                "lieferant": "",
                "kunde": txn.get("buyer_name", ""),
                "betrag": amount,
                "pdf": pdf_path if Path(pdf_path).exists() else "",
                "xml": None,
                "id": txn.get("id", ""),
            })
    except Exception:
        pass

    belege.sort(key=lambda b: b.get("datum", ""))
    return _json({"belege": belege, "von": von, "bis": bis, "count": len(belege)})


@app.route("/api/steuerberater/export")
def api_stb_export():
    """Erzeugt ZIP mit allen PDFs + CSV-Übersicht für einen Zeitraum."""
    import csv, io, zipfile

    von = request.args.get("von", "")
    bis = request.args.get("bis", "")

    # Belege sammeln
    with app.test_request_context(f"/api/steuerberater/belege?von={von}&bis={bis}"):
        result = json.loads(api_stb_belege().get_data())
    belege = result.get("belege", [])

    if not belege:
        return _json({"error": "Keine Belege im gewählten Zeitraum"}, 404)

    # ZIP erstellen
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # CSV-Übersicht
        csv_buffer = io.StringIO()
        csv_buffer.write("\ufeff")
        w = csv.writer(csv_buffer, delimiter=";")
        w.writerow(["Typ", "Belegnummer", "Datum", "Lieferant/Kunde", "Betrag", "Datei"])
        for b in belege:
            partner = b.get("lieferant") or b.get("kunde") or ""
            filename = ""
            if b.get("pdf") and Path(b["pdf"]).exists():
                fname = Path(b["pdf"]).name
                sub = "Eingang" if b["typ"] == "Eingang" else "Ausgang"
                arcname = f"{sub}/{fname}"
                zf.write(b["pdf"], arcname)
                filename = arcname
            if b.get("xml") and Path(b["xml"]).exists():
                fname = Path(b["xml"]).name
                sub = "Eingang" if b["typ"] == "Eingang" else "Ausgang"
                arcname = f"{sub}/{fname}"
                zf.write(b["xml"], arcname)
            w.writerow([b["typ"], b.get("nummer",""), b.get("datum",""),
                        partner, f'{b.get("betrag",0):.2f}'.replace(".",","), filename])
        zf.writestr("Belegübersicht.csv", csv_buffer.getvalue().encode("utf-8-sig"))

    zip_buffer.seek(0)
    period = f"{von}_bis_{bis}" if von and bis else "gesamt"
    filename = f"Belege_Steuerberater_{period}.zip"

    return app.response_class(
        zip_buffer.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.route("/api/steuerberater/send", methods=["POST"])
def api_stb_send():
    """Sendet das Belegpaket per Mail an den Steuerberater."""
    import smtplib, ssl
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.mime.application import MIMEApplication

    data = request.json or {}
    stb_email = data.get("email", "")
    von = data.get("von", "")
    bis = data.get("bis", "")

    if not stb_email:
        return _json({"error": "Keine E-Mail-Adresse angegeben"}, 400)

    # ZIP erzeugen
    with app.test_request_context(f"/api/steuerberater/export?von={von}&bis={bis}"):
        zip_resp = api_stb_export()
    if hasattr(zip_resp, 'status_code') and zip_resp.status_code != 200:
        return _json({"error": "Keine Belege im Zeitraum"}, 400)
    zip_bytes = zip_resp.get_data()

    # SMTP
    ms = _load_mandant_settings()
    _email_cfg_path = _DATA / "data" / "email_config.json"
    smtp_cfg = {}
    if _email_cfg_path.exists():
        try:
            smtp_cfg = json.loads(_email_cfg_path.read_text("utf-8"))
        except Exception:
            pass

    smtp_host = smtp_cfg.get("smtp_host", "")
    smtp_port = int(smtp_cfg.get("smtp_port", 587))
    smtp_user = smtp_cfg.get("smtp_user", "")
    smtp_pass = smtp_cfg.get("smtp_password", "")
    from_addr = smtp_cfg.get("smtp_from_address", ms.get("email", ""))
    from_name = smtp_cfg.get("smtp_from_name", ms.get("name", ""))

    if not smtp_host:
        return _json({"error": "SMTP nicht konfiguriert"}, 400)

    period = f"{von} bis {bis}" if von and bis else "gesamt"
    msg = MIMEMultipart()
    msg["From"] = f"{from_name} <{from_addr}>"
    msg["To"] = stb_email
    msg["Subject"] = f"Belege {period} – {ms.get('name', '')}"
    body = (f"Sehr geehrte Damen und Herren,\n\n"
            f"anbei erhalten Sie die Belege für den Zeitraum {period}.\n"
            f"Das ZIP enthält alle Eingangs- und Ausgangsrechnungen als PDF\n"
            f"sowie eine CSV-Übersicht.\n\n"
            f"Mit freundlichen Grüßen\n{ms.get('name', '')}")
    msg.attach(MIMEText(body, "plain", "utf-8"))
    att = MIMEApplication(zip_bytes, _subtype="zip")
    att.add_header("Content-Disposition", "attachment",
                   filename=f"Belege_{period.replace(' ','_')}.zip")
    msg.attach(att)

    try:
        if smtp_port == 465:
            ctx = ssl.create_default_context()
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15, context=ctx)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=15)
            server.ehlo()
            server.starttls()
            server.ehlo()
        if smtp_pass:
            server.login(smtp_user, smtp_pass)
        server.send_message(msg)
        server.quit()
        return _json({"sent": True, "to": stb_email, "size": len(zip_bytes)})
    except Exception as e:
        return _json({"error": f"Mail-Versand fehlgeschlagen: {e}"}, 500)


# ── API: Daten zuruecksetzen ──────────────────────────────────────────

@app.route("/api/reset-data", methods=["POST"])
def api_reset_data():
    """Loescht alle Rechnungen, Archiveintraege und Inbox-Daten."""
    import shutil

    # Rechnungen leeren
    invoices.clear()
    inbox.items.clear()
    inbox.duplicates._seen.clear()
    inbox.duplicates._hashes.clear()
    inbox.duplicates._numbers.clear()
    dash.invoices.clear()

    # Archiv leeren
    archive._index.clear()
    archive._save_index()
    for child in archive.root.iterdir():
        if child.is_dir():
            shutil.rmtree(child)

    # Export-Dateien leeren
    export_dir = _DATA / "data" / "export"
    if export_dir.exists():
        for f in export_dir.iterdir():
            f.unlink()

    # Speichern (leere invoices.json)
    auto_save()

    # Rechnungszaehler zuruecksetzen
    settings = _load_mandant_settings()
    settings["invoice_number_counter"] = 0
    _save_mandant_settings(settings)

    return _json({"success": True, "message": "Alle Daten geloescht"})


# ── API: Lizenz ───────────────────────────────────────────────────────

@app.route("/api/license")
def api_license():
    """Gibt den aktuellen Lizenzstatus zurueck."""
    try:
        return _json(lic_mgr.get_info().to_dict())
    except Exception as e:
        import traceback
        traceback.print_exc()
        # Fallback: Trial-Modus wenn Lizenzprüfung fehlschlägt
        return _json({
            "status": "TRIAL",
            "is_active": True,
            "is_trial": True,
            "trial_days_left": 28,
            "days_remaining": 28,
            "status_text": f"Testversion (Lizenzfehler: {e})",
            "device_id": "unknown",
            "customer_name": "",
            "license_key_short": "",
            "valid_until": "",
            "trial_start": "",
        })


@app.route("/api/license/activate", methods=["POST"])
def api_license_activate():
    """Aktiviert eine Lizenz mit dem gegebenen Code."""
    data = request.get_json(silent=True) or {}
    key = data.get("key", "")
    if not key:
        return _json({"error": "Kein Lizenzcode angegeben"}, 400)
    success, message = lic_mgr.activate(key)
    if success:
        return _json({"success": True, "message": message, "license": lic_mgr.get_info().to_dict()})
    return _json({"error": message}, 400)


@app.route("/")
def index():
    return send_from_directory(str(_BASE / "static"), "index.html")


# ── API: Dashboard ─────────────────────────────────────────────────────

@app.route("/api/dashboard")
def api_dashboard():
    all_inv = list(invoices.values())
    total = len(all_inv)
    offene = sum(1 for i in all_inv if i.status in ("IN_PRUEFUNG", "IN_FREIGABE", "NEU"))
    freigegeben = sum(1 for i in all_inv if i.status == "FREIGEGEBEN")
    exportiert = sum(1 for i in all_inv if i.status == "EXPORTIERT")
    volumen = sum(float(i.tax_inclusive_amount()) for i in all_inv)
    touchless = sum(1 for i in all_inv if not any(
        e.event_type in ("MANUELLE_KORREKTUR", "FELD_GEAENDERT") for e in i.audit_trail))
    touchless_rate = (touchless / total * 100) if total > 0 else 0

    # Auftragsmanagement-KPIs
    txn_stats = txn_mgr.stats()
    txns_all = txn_mgr.list_all()

    # Umsatz aus abgeschlossenen Vorgängen (Stufe 7 approved)
    umsatz = 0
    pipeline = 0
    for t in txns_all:
        inv_step = t.get("steps", {}).get("invoice", {})
        if inv_step.get("approved"):
            umsatz += inv_step.get("amount", 0)
        elif inv_step.get("amount", 0) > 0:
            pipeline += inv_step.get("amount", 0)
        else:
            # Pipeline aus letztem Step mit Betrag
            for key in reversed(STEP_KEYS):
                s = t.get("steps", {}).get(key, {})
                if s.get("amount", 0) > 0:
                    pipeline += s["amount"]
                    break

    # Mahnungen
    dunning_summary = dunning_mgr.get_overdue_summary(txns_all)

    return _json({
        "total": total, "offene": offene, "freigegeben": freigegeben,
        "exportiert": exportiert, "volumen": round(volumen, 2),
        "touchless_rate": round(touchless_rate, 1),
        "inbox_count": len(inbox.items),
        "export_count": len(exporter.get_log()),
        # Auftragsmanagement
        "txn_total": txn_stats["total"],
        "txn_offen": txn_stats["in_bearbeitung"] + txn_stats["neu"],
        "txn_abgeschlossen": txn_stats["abgeschlossen"],
        "txn_umsatz": round(umsatz, 2),
        "txn_pipeline": round(pipeline, 2),
        # Mahnungen
        "dunning_overdue": dunning_summary["total_overdue"],
        "dunning_action": dunning_summary["needs_action"],
        "dunning_amount": dunning_summary["total_overdue_amount"],
    })


# ── API: Bestellungs-Matching (Phase 4) ──────────────────────────────

@app.route("/api/invoices/<inv_id>/match-order", methods=["POST"])
def api_match_order(inv_id):
    """Sucht offene Bestellungen die zu einer Eingangsrechnung passen."""
    inv = invoices.get(inv_id)
    if not inv:
        return _json({"error": "Rechnung nicht gefunden"}, 404)

    if (inv._direction or "") != "EINGANG":
        return _json({"error": "Nur Eingangsrechnungen können gematcht werden"}, 400)

    txns = txn_mgr.list_all()
    inv_amount = float(inv.tax_inclusive_amount())
    inv_seller = inv.seller.name.lower().strip() if inv.seller else ""
    inv_order_ref = (inv.order_reference or "").strip()

    matches = []
    for txn in txns:
        po_step = txn.get("steps", {}).get("purchase_order", {})
        if not po_step.get("approved"):
            continue  # Nur freigegebene Bestellungen

        score = 0
        reasons = []

        # Lieferantenname
        txn_supplier = txn.get("supplier_name", "").lower().strip()
        if inv_seller and txn_supplier and (
            inv_seller in txn_supplier or txn_supplier in inv_seller):
            score += 40
            reasons.append("Lieferant passt")

        # Betrag (±5% Toleranz)
        po_amount = po_step.get("amount", 0)
        if po_amount > 0 and inv_amount > 0:
            diff_pct = abs(inv_amount - po_amount) / po_amount * 100
            if diff_pct < 1:
                score += 40
                reasons.append(f"Betrag exakt ({diff_pct:.1f}%)")
            elif diff_pct < 5:
                score += 25
                reasons.append(f"Betrag ähnlich ({diff_pct:.1f}%)")

        # Bestellnummer-Referenz
        po_ref = po_step.get("reference", "")
        if inv_order_ref and po_ref and inv_order_ref.lower() == po_ref.lower():
            score += 50
            reasons.append("Bestellnummer matcht")

        if score >= 40:
            matches.append({
                "transaction_id": txn["id"],
                "subject": txn.get("subject", ""),
                "supplier_name": txn.get("supplier_name", ""),
                "po_reference": po_ref,
                "po_amount": po_amount,
                "score": score,
                "reasons": reasons,
            })

    matches.sort(key=lambda m: -m["score"])
    return _json({"invoice_id": inv_id, "matches": matches})


@app.route("/api/invoices/<inv_id>/link-order", methods=["POST"])
def api_link_order(inv_id):
    """Verknüpft eine Eingangsrechnung mit einem Vorgang."""
    inv = invoices.get(inv_id)
    if not inv:
        return _json({"error": "Rechnung nicht gefunden"}, 404)

    data = request.json or {}
    txn_id = data.get("transaction_id", "")
    if not txn_id:
        return _json({"error": "transaction_id erforderlich"}, 400)

    txn = txn_mgr.get(txn_id)
    if not txn:
        return _json({"error": f"Vorgang {txn_id} nicht gefunden"}, 404)

    # Verknüpfung: Rechnungsnummer in Audit-Trail und Note
    inv.add_audit("BESTELLUNG_VERKNUEPFT", "system",
                  f"Verknüpft mit Vorgang {txn_id} (Bestellung {txn['steps'].get('purchase_order', {}).get('reference', '')})")
    if not inv.note:
        inv.note = f"Vorgang: {txn_id}"
    else:
        inv.note += f" | Vorgang: {txn_id}"

    auto_save()

    return _json({
        "linked": True,
        "invoice_id": inv_id,
        "transaction_id": txn_id,
    })


# ── API: Rechnungsliste ───────────────────────────────────────────────

@app.route("/api/invoices")
def api_invoices():
    status_filter = request.args.get("status", "")
    direction_filter = request.args.get("direction", "")
    search = request.args.get("q", "").lower()

    result = []
    for inv in invoices.values():
        if status_filter and inv.status != status_filter:
            continue
        if direction_filter and (inv._direction or "AUSGANG") != direction_filter:
            continue
        text = f"{inv.invoice_number} {inv.seller.name} {inv.buyer.name}".lower()
        if search and search not in text:
            continue
        report = validate_invoice(inv)
        result.append({
            "id": inv._id,
            "number": inv.invoice_number,
            "date": inv.invoice_date.isoformat(),
            "seller": inv.seller.name,
            "buyer": inv.buyer.name,
            "net": float(inv.tax_exclusive_amount()),
            "tax": float(inv.tax_amount()),
            "gross": float(inv.tax_inclusive_amount()),
            "due": float(inv.amount_due()),
            "currency": inv.currency_code,
            "status": inv.status,
            "assigned_to": inv.assigned_to,
            "type_code": inv.invoice_type_code,
            "valid": report.is_valid,
            "error_count": report.error_count,
            "format": inv._source_format or "XRechnung",
            "direction": inv._direction or "AUSGANG",
            "buyer_reference": inv.buyer_reference or inv.buyer.buyer_reference,
        })
    result.sort(key=lambda x: x["date"], reverse=True)
    return _json({"invoices": result, "count": len(result)})


# ── API: Rechnungsdetail ──────────────────────────────────────────────

@app.route("/api/invoices/<inv_id>")
def api_invoice_detail(inv_id):
    inv = invoices.get(inv_id)
    if not inv:
        return _json({"error": "Rechnung nicht gefunden"}, 404)

    report = validate_invoice(inv)
    lines = []
    for l in inv.lines:
        lines.append({
            "id": l.line_id, "name": l.item_name, "description": l.item_description,
            "quantity": float(l.quantity), "unit": l.unit_code,
            "unit_price": float(l.unit_price), "net": float(l.line_net_amount),
            "tax_rate": float(l.tax_rate), "tax_category": l.tax_category,
            "item_id": l.item_id,
        })

    tax_breakdown = [{"category": t.category_code, "rate": float(t.rate),
                       "taxable": float(t.taxable_amount), "tax": float(t.tax_amount)}
                      for t in inv.compute_tax_subtotals()]

    audit = [{"type": e.event_type, "user": e.user, "timestamp": e.timestamp,
              "comment": e.comment, "old": e.old_value, "new": e.new_value}
             for e in inv.audit_trail]

    return _json({
        "id": inv._id,
        "number": inv.invoice_number,
        "date": inv.invoice_date.isoformat(),
        "type_code": inv.invoice_type_code,
        "currency": inv.currency_code,
        "note": inv.note,
        "buyer_reference": inv.buyer_reference or inv.buyer.buyer_reference,
        "order_reference": inv.order_reference,
        "contract_reference": inv.contract_reference,
        "project_reference": inv.project_reference,
        "preceding_invoice": inv.preceding_invoice,
        "period_start": inv.period_start.isoformat() if inv.period_start else None,
        "period_end": inv.period_end.isoformat() if inv.period_end else None,
        "seller": {
            "name": inv.seller.name,
            "street": inv.seller.address.street, "city": inv.seller.address.city,
            "post_code": inv.seller.address.post_code, "country": inv.seller.address.country_code,
            "vat_id": inv.seller.vat_id, "tax_reg": inv.seller.tax_registration_id,
            "email": inv.seller.electronic_address,
            "contact_name": inv.seller.contact.name,
            "contact_phone": inv.seller.contact.telephone,
            "contact_email": inv.seller.contact.email,
        },
        "buyer": {
            "name": inv.buyer.name,
            "street": inv.buyer.address.street, "city": inv.buyer.address.city,
            "post_code": inv.buyer.address.post_code, "country": inv.buyer.address.country_code,
            "vat_id": inv.buyer.vat_id, "email": inv.buyer.electronic_address,
        },
        "payment": {
            "means_code": inv.payment.means_code,
            "iban": inv.payment.iban, "bic": inv.payment.bic,
            "due_date": inv.payment.due_date.isoformat() if inv.payment.due_date else None,
            "terms": inv.payment.payment_terms,
        },
        "lines": lines,
        "allowances": [{"charge": ac.is_charge, "amount": float(ac.amount),
                         "reason": ac.reason, "rate": float(ac.tax_rate)}
                        for ac in inv.allowances_charges],
        "totals": {
            "line_net": float(inv.sum_line_net()),
            "allowances": float(inv.sum_allowances()),
            "charges": float(inv.sum_charges()),
            "net": float(inv.tax_exclusive_amount()),
            "tax": float(inv.tax_amount()),
            "gross": float(inv.tax_inclusive_amount()),
            "due": float(inv.amount_due()),
        },
        "tax_breakdown": tax_breakdown,
        "status": inv.status,
        "assigned_to": inv.assigned_to,
        "validation": {"valid": report.is_valid, "errors": report.error_count,
                        "warnings": report.warning_count,
                        "issues": [{"rule": i.rule_id, "severity": i.severity.value,
                                    "message": i.message, "field": i.field} for i in report.issues]},
        "audit_trail": audit,
        "format": inv._source_format or "XRechnung",
        "direction": inv._direction or "AUSGANG",
    })


# ── API: Rechnungs-Viewer (HTML) ───────────────────────────────────────

@app.route("/view/<inv_id>")
def view_invoice_html(inv_id):
    """Rendert eine Rechnung als eigenständige druckbare HTML-Seite."""
    inv = invoices.get(inv_id)
    if not inv:
        abort(404)

    report = validate_invoice(inv)
    s = inv.seller
    b = inv.buyer
    p = inv.payment
    type_label = "Gutschrift" if inv.invoice_type_code == "381" else "Rechnung"

    def f2(v):
        return f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    lines_html = ""
    for i, l in enumerate(inv.lines, 1):
        unit = {"C62": "Stk.", "HUR": "Std.", "DAY": "Tag(e)"}.get(l.unit_code, l.unit_code)
        lines_html += f"""<tr>
            <td style="color:#999;width:40px">{l.line_id or i}</td>
            <td class="name">{l.item_name}{('<div class="desc">' + l.item_description + '</div>') if l.item_description else ''}</td>
            <td class="r">{l.quantity} {unit}</td>
            <td class="r">{f2(l.unit_price)}</td>
            <td class="r">{l.tax_rate}%</td>
            <td class="r" style="font-weight:500">{f2(l.line_net_amount)}</td>
        </tr>"""

    allow_html = ""
    for ac in inv.allowances_charges:
        sign = "+" if ac.is_charge else "-"
        label = "Zuschlag" if ac.is_charge else "Nachlass"
        allow_html += f'<div class="sum-row"><span class="k">{label}: {ac.reason}</span><span>{sign} {f2(ac.amount)} EUR</span></div>'

    tax_html = ""
    for st in inv.compute_tax_subtotals():
        tax_html += f'<div class="sum-row"><span class="k">USt {st.rate}% auf {f2(st.taxable_amount)}</span><span>{f2(st.tax_amount)} EUR</span></div>'

    val_badge = ""
    val_block = ""
    if report.is_valid:
        val_badge = '<div class="val-overlay val-ok">&#10003; Validierung OK</div>'
    else:
        val_badge = f'<div class="val-overlay val-err">&#10007; {report.error_count} Fehler</div>'
        issues = "".join(
            f'<div style="padding:2px 0;color:{"#A32D2D" if i.severity.value=="ERROR" else "#854F0B" if i.severity.value=="WARNING" else "#666"}">'
            f'{"&#10007;" if i.severity.value=="ERROR" else "&#9888;"} {i.message}</div>'
            for i in report.issues
        )
        val_block = f'<div class="val-issues has-err"><div style="font-weight:500;margin-bottom:4px">Validierungsfehler</div>{issues}</div>'

    iban_fmt = " ".join([p.iban[i:i+4] for i in range(0, len(p.iban), 4)]) if p.iban else "—"
    due_fmt = p.due_date.strftime("%d.%m.%Y") if p.due_date else "—"
    date_fmt = inv.invoice_date.strftime("%d.%m.%Y") if inv.invoice_date else "—"
    period_fmt = ""
    if inv.period_start:
        period_fmt = f'<div class="meta-row"><span class="k">Leistungszeitraum</span><span>{inv.period_start.strftime("%d.%m.%Y")} – {inv.period_end.strftime("%d.%m.%Y") if inv.period_end else ""}</span></div>'

    pay_label = {"30": "Überweisung", "48": "Kreditkarte", "58": "SEPA-Überweisung", "59": "SEPA-Lastschrift"}.get(p.means_code, p.means_code)

    # Storno-Banner
    storno_banner = ""
    if inv.status == "STORNIERT":
        storno_evt = [e for e in inv.audit_trail if e.event_type == "STORNIERT"]
        storno_comment = storno_evt[-1].comment if storno_evt else ""
        storno_banner += f'<div style="padding:10px 16px;background:#FCEBEB;border:1px solid #F09595;border-radius:6px;margin-bottom:16px;font-size:13px;color:#A32D2D"><strong>&#10007; Diese Rechnung wurde storniert.</strong> {storno_comment}</div>'
    if inv.preceding_invoice:
        ref_label = "Gutschrift/Storno" if inv.invoice_type_code == "381" else "Korrektur"
        storno_banner += f'<div style="padding:10px 16px;background:#FAEEDA;border:1px solid #FAC775;border-radius:6px;margin-bottom:16px;font-size:13px;color:#854F0B"><strong>Bezug:</strong> {ref_label} zu Rechnung {inv.preceding_invoice}</div>'

    # QR Code generieren
    qr_data_uri = generate_invoice_qr_data_uri(inv, box_size=6)
    qr_block = ""
    if qr_data_uri:
        qr_block = f"""<div style="margin-top:24px;display:flex;gap:20px;align-items:flex-start;padding:16px;background:#f8f8f6;border-radius:8px">
      <img src="{qr_data_uri}" style="width:140px;height:140px;flex-shrink:0" alt="GiroCode QR">
      <div style="font-size:11px;color:#666">
        <div style="font-weight:500;font-size:12px;color:#333;margin-bottom:6px">GiroCode – SEPA-Überweisung per QR</div>
        <p style="margin:0 0 4px">Scannen Sie den QR-Code mit Ihrer Banking-App, um die Zahlung automatisch auszufüllen.</p>
        <p style="margin:0;font-size:10px;color:#999">Standard: EPC069-12 v2.1 · Empfänger: {s.name} · IBAN: {iban_fmt} · Betrag: {f2(inv.amount_due())} EUR</p>
      </div>
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="de"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{type_label} {inv.invoice_number}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f0eeea;color:#1a1a1a;padding:24px}}
.inv-doc{{background:#fff;max-width:800px;margin:0 auto;padding:48px 56px;border-radius:8px;font-size:13px;line-height:1.6;position:relative;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
.doc-header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:32px;padding-bottom:20px;border-bottom:2px solid #1a1a1a}}
.seller-name{{font-size:20px;font-weight:600}} .seller-sub{{font-size:12px;color:#666;margin:2px 0}}
.doc-type{{font-size:24px;font-weight:500;text-align:right}} .doc-type .nr{{font-size:14px;color:#666;font-weight:400}}
.addr-block{{margin-bottom:24px}} .addr-label{{font-size:10px;text-transform:uppercase;letter-spacing:1px;color:#999;margin-bottom:4px}} .addr-to{{font-size:14px;font-weight:500}}
.meta-grid{{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:28px}}
.meta-box{{background:#f8f8f6;padding:14px 16px;border-radius:6px}}
.meta-box .label{{font-size:10px;text-transform:uppercase;letter-spacing:.5px;color:#999;margin-bottom:4px}}
.meta-row{{display:flex;justify-content:space-between;font-size:12px;padding:2px 0}} .meta-row .k{{color:#666}}
.pos-table{{width:100%;border-collapse:collapse;margin-bottom:24px}}
.pos-table thead th{{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:#999;padding:8px 10px;border-bottom:1px solid #ddd;text-align:left}}
.pos-table thead th.r{{text-align:right}} .pos-table tbody td{{padding:10px;border-bottom:1px solid #eee;vertical-align:top}}
.pos-table tbody td.r{{text-align:right}} .pos-table tbody td.name{{font-weight:500}} .pos-table tbody td .desc{{font-size:11px;color:#888;margin-top:2px}}
.sum-block{{display:flex;justify-content:flex-end}} .sum-inner{{min-width:280px}}
.sum-row{{display:flex;justify-content:space-between;padding:4px 0;font-size:13px}} .sum-row .k{{color:#666}}
.sum-total{{border-top:2px solid #1a1a1a;margin-top:8px;padding-top:8px;font-size:16px;font-weight:600}}
.pay-block{{margin-top:28px;padding-top:20px;border-top:1px solid #eee}}
.pay-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}
.pay-item .label{{font-size:10px;text-transform:uppercase;letter-spacing:.5px;color:#999}} .pay-item .val{{font-size:13px;font-weight:500;margin-top:2px}}
.note-block{{margin:20px 0;padding:12px 16px;background:#f8f8f6;border-radius:6px;font-size:12px;color:#666}}
.footer-block{{margin-top:32px;padding-top:16px;border-top:1px solid #ddd;display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;font-size:11px;color:#999}}
.footer-block .col-title{{font-weight:500;color:#666;margin-bottom:2px}}
.val-overlay{{position:absolute;top:12px;right:12px;padding:6px 14px;border-radius:6px;font-size:12px;font-weight:500}}
.val-ok{{background:#E1F5EE;color:#0F6E56}} .val-err{{background:#FCEBEB;color:#A32D2D}}
.val-issues{{margin-top:20px;padding:14px 16px;border-radius:6px;font-size:12px}}
.val-issues.has-err{{background:#FCEBEB;border:1px solid #F09595}}
.toolbar{{max-width:800px;margin:0 auto 16px;display:flex;gap:8px;justify-content:flex-end}}
.toolbar button{{font-family:inherit;font-size:13px;padding:6px 14px;border:1px solid #ddd;background:#fff;border-radius:6px;cursor:pointer}}
.toolbar button:hover{{background:#f0eeea}}
@media print{{body{{background:#fff;padding:0}} .inv-doc{{box-shadow:none;border-radius:0;padding:32px}} .toolbar,.val-overlay,.val-issues{{display:none !important}}}}
</style></head><body>
<div class="toolbar">
  <button onclick="window.print()">&#128424; Drucken</button>
  <button onclick="window.close()">Schließen</button>
</div>
<div class="inv-doc">
  {val_badge}
  <div class="doc-header">
    <div style="display:flex;gap:16px;align-items:flex-start">
      {('<img src="' + get_logo_data_uri() + '" style="height:52px;width:auto;object-fit:contain;flex-shrink:0">') if get_logo_path() else ''}
      <div>
        <div class="seller-name">{s.name}</div>
        <div class="seller-sub">{s.address.street}, {s.address.post_code} {s.address.city}</div>
        <div class="seller-sub">USt-ID: {s.vat_id}{(' · Steuer-Nr.: ' + s.tax_registration_id) if s.tax_registration_id else ''}</div>
      </div>
    </div>
    <div class="doc-type">{type_label}<div class="nr">{inv.invoice_number}</div>
      <div style="font-size:11px;margin-top:4px"><span style="background:{'#E8F4FD' if inv._direction == 'EINGANG' else '#E8FDE8'};color:{'#1A6FB5' if inv._direction == 'EINGANG' else '#1A7F1A'};padding:2px 8px;border-radius:4px">{'← Eingang' if inv._direction == 'EINGANG' else '→ Ausgang'}</span></div>
    </div>
  </div>
  {storno_banner}
  <div class="addr-block">
    <div class="addr-label">Empfänger</div>
    <div class="addr-to">{b.name}</div>
    <div>{b.address.street}</div>
    <div>{b.address.post_code} {b.address.city}</div>
    {'<div style="margin-top:4px;font-size:12px;color:#666">USt-ID: ' + b.vat_id + '</div>' if b.vat_id else ''}
  </div>
  <div class="meta-grid">
    <div class="meta-box">
      <div class="meta-row"><span class="k">Rechnungsdatum</span><span>{date_fmt}</span></div>
      {period_fmt}
      {'<div class="meta-row"><span class="k">Fällig am</span><span style="font-weight:500">' + due_fmt + '</span></div>' if p.due_date else ''}
    </div>
    <div class="meta-box">
      <div class="meta-row"><span class="k">Buyer Reference</span><span>{inv.buyer_reference or inv.buyer.buyer_reference or '—'}</span></div>
      {'<div class="meta-row"><span class="k">Bestellnummer</span><span>' + inv.order_reference + '</span></div>' if inv.order_reference else ''}
      {'<div class="meta-row"><span class="k">Vertrag</span><span>' + inv.contract_reference + '</span></div>' if inv.contract_reference else ''}
    </div>
  </div>
  {'<div class="note-block"><strong>Bemerkung:</strong> ' + inv.note + '</div>' if inv.note else ''}
  <table class="pos-table">
    <thead><tr><th style="width:40px">Pos.</th><th>Bezeichnung</th><th class="r" style="width:80px">Menge</th>
      <th class="r" style="width:90px">EP netto</th><th class="r" style="width:60px">USt</th>
      <th class="r" style="width:100px">Netto</th></tr></thead>
    <tbody>{lines_html}</tbody>
  </table>
  <div class="sum-block"><div class="sum-inner">
    <div class="sum-row"><span class="k">Summe Positionen netto</span><span>{f2(inv.sum_line_net())} EUR</span></div>
    {allow_html}
    {('<div class="sum-row"><span class="k">Nettobetrag</span><span>' + f2(inv.tax_exclusive_amount()) + ' EUR</span></div>') if inv.allowances_charges else ''}
    {tax_html}
    <div class="sum-row" style="border-top:1px solid #ddd;padding-top:6px;margin-top:4px"><span class="k">Bruttobetrag</span><span>{f2(inv.tax_inclusive_amount())} EUR</span></div>
    <div class="sum-row sum-total"><span>Zahlbetrag</span><span>{f2(inv.amount_due())} EUR</span></div>
  </div></div>
  <div class="pay-block"><div class="pay-grid">
    <div class="pay-item"><div class="label">Zahlungsart</div><div class="val">{pay_label}</div></div>
    <div class="pay-item"><div class="label">IBAN</div><div class="val">{iban_fmt}</div></div>
    <div class="pay-item"><div class="label">BIC</div><div class="val">{p.bic or '—'}</div></div>
    <div class="pay-item"><div class="label">Zahlungsziel</div><div class="val">{p.payment_terms or '—'}</div></div>
  </div></div>
  {qr_block}
  <div class="footer-block">
    <div><div class="col-title">{s.name}</div>{s.address.street}<br>{s.address.post_code} {s.address.city}</div>
    <div><div class="col-title">Kontakt</div>{s.contact.name}<br>{s.contact.telephone}<br>{s.contact.email}</div>
    <div><div class="col-title">Steuerdaten</div>USt-ID: {s.vat_id}<br>{('St.-Nr.: ' + s.tax_registration_id + '<br>') if s.tax_registration_id else ''}Format: XRechnung (EN 16931)</div>
  </div>
  {val_block}
</div></body></html>"""


# ── API: Upload ────────────────────────────────────────────────────────

@app.route("/api/upload", methods=["POST"])
def api_upload():
    if "file" in request.files:
        f = request.files["file"]
        data = f.read()
        filename = f.filename or "upload.xml"
    else:
        data = request.get_data()
        filename = request.headers.get("X-Filename", "upload.xml")

    if not data:
        return _json({"error": "Keine Daten"}, 400)

    item = inbox.receive_file(filename, data, sender_email=request.headers.get("X-Sender", ""))

    if item.invoice and item.status == "VERARBEITET":
        item.invoice._direction = "EINGANG"
        invoices[item.invoice._id] = item.invoice
        dash.add_invoice(item.invoice)
        wf_engine.start_workflow(item.invoice)
        # Archivieren (FR-500: Eingangsrechnung + Validierung)
        report = item.validation or validate_invoice(item.invoice)
        archive.archive_invoice(item.invoice, data, report, direction="EINGANG")
        # Stellvertreter prüfen
        deputies.redirect_invoice(item.invoice)
        # Benachrichtigung
        valid = item.validation.is_valid if item.validation else True
        notifier.notify_new_invoice(item.invoice, valid, item.validation.error_count if item.validation else 0)
        if item.invoice.assigned_to:
            notifier.notify_approval_needed(item.invoice, item.invoice.assigned_to)
    dash.add_inbox_item(item)
    if item.status == "DUPLIKAT" and item.invoice:
        notifier.notify_duplicate(item.invoice, item.error)

    return _json({
        "status": item.status,
        "invoice_id": item.invoice._id if item.invoice else None,
        "invoice_number": item.invoice.invoice_number if item.invoice else None,
        "valid": item.validation.is_valid if item.validation else None,
        "errors": item.validation.error_count if item.validation else 0,
        "format": item.format_type,
        "error": item.error,
    }, 201 if item.status == "VERARBEITET" else 400)


# ── API: PDF-Import für Ausgangsrechnungen (FR-700) ───────────────────

@app.route("/api/upload-pdf-ausgang", methods=["POST"])
def api_upload_pdf_ausgang():
    """
    Importiert eine PDF-Rechnung im Energieberatungs-Layout als
    Ausgangsrechnung. Erzeugt ein Invoice-Objekt, speichert XRechnung-XML
    und archiviert Original + Strukturdaten (FR-500, FR-700).
    """
    import tempfile
    from pdf_import import parse_energieberatung_pdf
    from xrechnung_generator import generate_and_serialize

    if "file" not in request.files:
        return _json({"error": "Keine Datei übergeben"}, 400)
    f = request.files["file"]
    if not (f.filename or "").lower().endswith(".pdf"):
        return _json({"error": "Nur PDF-Dateien erlaubt"}, 400)

    pdf_bytes = f.read()
    if not pdf_bytes:
        return _json({"error": "Leere Datei"}, 400)

    # Seller-Overrides aus mandant_settings übernehmen, falls gepflegt
    ms = _load_mandant_settings()
    overrides = {}
    if ms:
        if ms.get("company_name"):  overrides["name"]         = ms["company_name"]
        if ms.get("street"):        overrides["street"]       = ms["street"]
        if ms.get("post_code"):     overrides["post_code"]    = ms["post_code"]
        if ms.get("city"):          overrides["city"]         = ms["city"]
        if ms.get("email"):         overrides["email"]        = ms["email"]
        if ms.get("iban"):          overrides["iban"]         = ms["iban"].replace(" ", "")
        if ms.get("bic"):           overrides["bic"]          = ms["bic"].replace(" ", "")
        if ms.get("bank_name"):     overrides["bank_name"]    = ms["bank_name"]
        if ms.get("contact_name"):  overrides["contact_name"] = ms["contact_name"]

    # PDF in Temp-Datei schreiben (pdfplumber braucht Pfad)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        inv = parse_energieberatung_pdf(tmp_path, seller_overrides=overrides)
    except ValueError as e:
        return _json({"error": str(e)}, 422)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    # XRechnung-XML erzeugen
    try:
        xml_bytes = generate_and_serialize(inv)
    except Exception as e:
        return _json({"error": f"XRechnung-Erzeugung fehlgeschlagen: {e}"}, 500)

    # Validieren (FR-210) — interner Validator
    report = validate_invoice(inv)

    # KoSIT-Validator zusätzlich anstoßen, wenn verfügbar (P-04)
    kosit_path = ms.get("kosit_validator_path") if ms else None
    kosit = validate_with_kosit(xml_bytes, validator_path=kosit_path)

    # Im globalen Invoice-Store ablegen
    invoices[inv._id] = inv
    dash.add_invoice(inv)

    # Archivieren als Ausgangsrechnung (FR-500)
    archive.archive_invoice(inv, xml_bytes, report, direction="AUSGANG")

    return _json({
        "status": "VERARBEITET",
        "invoice_id": inv._id,
        "invoice_number": inv.invoice_number,
        "buyer_name": inv.buyer.name,
        "total_gross": str(sum(l.line_net_amount for l in inv.lines)),
        "line_count": len(inv.lines),
        "valid": report.is_valid,
        "errors": report.error_count,
        "warnings": report.warning_count,
        "kosit": {
            "available": kosit.available,
            "valid": kosit.valid,
            "errors": kosit.error_count,
            "warnings": kosit.warning_count,
            "version": kosit.validator_version,
            "reason": kosit.unavailable_reason,
        },
        "direction": "AUSGANG",
        "source_file": f.filename,
    }, 201)


@app.route("/api/invoices/<inv_id>/zugferd", methods=["GET"])
def api_download_zugferd(inv_id):
    """
    Erzeugt on-the-fly ein ZUGFeRD-PDF/A-3 für eine gespeicherte Rechnung
    und liefert es als Download aus (FR-700, P-03).
    """
    from flask import Response
    inv = invoices.get(inv_id)
    if not inv:
        return _json({"error": "Rechnung nicht gefunden"}, 404)
    try:
        xml_bytes = generate_and_serialize(inv)
        pdf_bytes = generate_zugferd_pdf(inv, xml_bytes)
    except Exception as e:
        return _json({"error": f"ZUGFeRD-Erzeugung fehlgeschlagen: {e}"}, 500)

    filename = f"{inv.invoice_number}_zugferd.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/api/invoices/<inv_id>/kosit", methods=["POST"])
def api_validate_kosit(inv_id):
    """
    Führt den KoSIT-Validator gegen eine bereits gespeicherte Rechnung aus.
    Nützlich zur nachträglichen Prüfung importierter Eingangsrechnungen.
    """
    inv = invoices.get(inv_id)
    if not inv:
        return _json({"error": "Rechnung nicht gefunden"}, 404)
    try:
        xml_bytes = generate_and_serialize(inv)
    except Exception as e:
        return _json({"error": f"XML-Erzeugung fehlgeschlagen: {e}"}, 500)

    ms = _load_mandant_settings()
    kosit_path = ms.get("kosit_validator_path") if ms else None
    result = validate_with_kosit(xml_bytes, validator_path=kosit_path)
    return _json({
        "available": result.available,
        "valid": result.valid,
        "errors": result.error_count,
        "warnings": result.warning_count,
        "error_details": result.errors[:20],
        "warning_details": result.warnings[:20],
        "version": result.validator_version,
        "scenario": result.scenario,
        "reason": result.unavailable_reason,
    })


@app.route("/api/datev-bulk-export", methods=["GET"])
def api_datev_bulk_export():
    """
    FR-430: DATEV-Monats- oder Jahresexport.
    Query-Parameter:
      year  (Pflicht) — 4-stelliges Jahr
      month (optional) — 1-12; fehlt => Jahresexport
      direction (optional) — AUSGANG | EINGANG | ALLE (default ALLE)

    Sammelt alle Rechnungen des Zeitraums aus dem Archiv und erzeugt
    einen zusammengefassten DATEV-Buchungsstapel als CSV-Download.
    """
    from flask import Response
    try:
        year = int(request.args.get("year", ""))
    except ValueError:
        return _json({"error": "year erforderlich (YYYY)"}, 400)
    month_arg = request.args.get("month", "").strip()
    direction = request.args.get("direction", "ALLE").upper()

    try:
        month = int(month_arg) if month_arg else None
        if month is not None and not (1 <= month <= 12):
            raise ValueError
    except ValueError:
        return _json({"error": "month muss 1-12 sein"}, 400)

    # Archiv nach Zeitraum filtern
    matching_ids: list[str] = []
    for rec in archive.list_all():
        meta = rec.get("invoice_metadata", {})
        date_str = str(meta.get("invoice_date", ""))
        if not date_str:
            continue
        # Erwartetes Format: YYYY-MM-DD
        try:
            rec_year = int(date_str[:4])
            rec_month = int(date_str[5:7])
        except (ValueError, IndexError):
            continue
        if rec_year != year:
            continue
        if month is not None and rec_month != month:
            continue
        if direction != "ALLE" and rec.get("direction", "") != direction:
            continue
        matching_ids.append(rec["invoice_id"])

    if not matching_ids:
        label = f"{year}-{month:02d}" if month else str(year)
        return _json({
            "error": f"Keine Rechnungen im Archiv für Zeitraum {label} "
                     f"(Richtung: {direction})",
            "matching_count": 0,
        }, 404)

    # Invoice-Objekte aus dem globalen Store holen,
    # für nicht im Store befindliche Rechnungen aus archivierter XML re-parsen
    inv_list = []
    reparsed = 0
    for rec in archive.list_all():
        if rec["invoice_id"] not in matching_ids:
            continue
        if rec["invoice_id"] in invoices:
            inv_list.append(invoices[rec["invoice_id"]])
            continue
        # Aus Archiv re-parsen
        xml_path = Path(archive.root) / rec["invoice_id"] / rec["xml_filename"]
        if not xml_path.exists():
            continue
        try:
            parsed = parse_xrechnung(xml_path.read_bytes(), source_file=rec["xml_filename"])
            # Status aus archivierten Metadaten übernehmen, damit der
            # DATEV-Exporter sie als freigegeben anerkennt
            parsed.status = rec.get("invoice_metadata", {}).get(
                "status", InvoiceStatus.FREIGEGEBEN.value
            )
            parsed._direction = rec.get("direction", "")
            inv_list.append(parsed)
            reparsed += 1
        except Exception as e:
            # Eine kaputte Datei darf den ganzen Bulk nicht stoppen
            print(f"[bulk-export] Re-Parse fehlgeschlagen für {rec['invoice_number']}: {e}")
            continue

    if not inv_list:
        return _json({
            "error": "Keine Rechnungen ladbar (weder im Store noch im Archiv).",
            "archived_count": len(matching_ids),
        }, 410)

    # Bulk-DATEV-CSV erzeugen
    result = exporter.datev.export_bulk(inv_list)
    if not result.success:
        return _json({"error": result.error or "Export fehlgeschlagen"}, 500)

    # Dateiname mit Zeitraum
    period = f"{year}-{month:02d}" if month else str(year)
    filename = f"DATEV_Buchungsstapel_{period}.csv"

    # Protokoll
    for inv in inv_list:
        if inv.status == InvoiceStatus.FREIGEGEBEN.value:
            inv.add_audit("BULK_EXPORT", comment=f"Zeitraum {period}, Datei {filename}")

    return Response(
        result.target,
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Invoice-Count": str(len(inv_list)),
            "X-Row-Count": str(result.row_count),
            "X-Reparsed-Count": str(reparsed),
            "X-Skipped": result.error or "",
        },
    )


@app.route("/api/datev-bulk-preview", methods=["GET"])
def api_datev_bulk_preview():
    """
    Liefert eine Vorschau, wie viele Rechnungen ein Bulk-Export
    erfassen würde — ohne CSV-Erzeugung. Für Frontend-Anzeige.
    """
    try:
        year = int(request.args.get("year", ""))
    except ValueError:
        return _json({"error": "year erforderlich"}, 400)
    month_arg = request.args.get("month", "").strip()
    month = int(month_arg) if month_arg.isdigit() else None
    direction = request.args.get("direction", "ALLE").upper()

    matches: list[dict] = []
    for rec in archive.list_all():
        meta = rec.get("invoice_metadata", {})
        date_str = str(meta.get("invoice_date", ""))
        if not date_str or len(date_str) < 7:
            continue
        try:
            rec_year = int(date_str[:4])
            rec_month = int(date_str[5:7])
        except ValueError:
            continue
        if rec_year != year:
            continue
        if month is not None and rec_month != month:
            continue
        if direction != "ALLE" and rec.get("direction", "") != direction:
            continue
        matches.append({
            "invoice_number": rec.get("invoice_number"),
            "invoice_date": date_str,
            "direction": rec.get("direction", ""),
            "buyer_name": meta.get("buyer_name", ""),
            "seller_name": meta.get("seller_name", ""),
            "gross_amount": meta.get("gross_amount", ""),
            "status": meta.get("status", ""),
            "in_store": rec["invoice_id"] in invoices,
            "in_archive": True,  # rec stammt aus dem Archiv-Index
        })
    return _json({
        "year": year,
        "month": month,
        "direction": direction,
        "count": len(matches),
        # exportierbar = im Store ODER aus Archiv-XML re-parsbar
        "exportable": sum(1 for m in matches
                          if m["status"] in ("FREIGEGEBEN", "EXPORTIERT")),
        "invoices": matches,
    })


# ── API: Workflow-Aktionen ─────────────────────────────────────────────

@app.route("/api/invoices/<inv_id>/approve", methods=["POST"])
def api_approve(inv_id):
    inv = invoices.get(inv_id)
    if not inv:
        return _json({"error": "Nicht gefunden"}, 404)

    data = request.get_json(silent=True) or {}
    user = data.get("user", "web-user")
    comment = data.get("comment", "")

    if inv.status == "NEU":
        wf_engine.start_workflow(inv, user)

    if inv.status == "IN_PRUEFUNG":
        msg = wf_engine.sachliche_pruefung(inv, user, True, comment)
    elif inv.status == "IN_FREIGABE":
        msg = wf_engine.kaufmaennische_freigabe(inv, user, True, comment)
    else:
        msg = f"Kein Freigabe-Schritt für Status {inv.status}"

    # Benachrichtigungen
    if inv.status == "FREIGEGEBEN":
        notifier.notify_approved(inv, user)
    elif inv.status == "IN_FREIGABE":
        deputies.redirect_invoice(inv)
        notifier.notify_approval_needed(inv, inv.assigned_to or "geschaeftsfuehrung")

    return _json({"status": inv.status, "message": msg})


@app.route("/api/invoices/<inv_id>/reject", methods=["POST"])
def api_reject(inv_id):
    inv = invoices.get(inv_id)
    if not inv:
        return _json({"error": "Nicht gefunden"}, 404)

    data = request.get_json(silent=True) or {}
    user = data.get("user", "web-user")
    comment = data.get("comment", "Zurückgewiesen via Web")
    msg = wf_engine.zurueckweisen(inv, user, comment)
    notifier.notify_rejected(inv, user, comment)
    return _json({"status": inv.status, "message": msg})


# ── API: Export ────────────────────────────────────────────────────────

@app.route("/api/invoices/<inv_id>/export", methods=["POST"])
def api_export(inv_id):
    inv = invoices.get(inv_id)
    if not inv:
        return _json({"error": "Nicht gefunden"}, 404)

    data = request.get_json(silent=True) or {}
    fmt = data.get("format", "DATEV")

    if inv.status != "FREIGEGEBEN":
        return _json({"error": f"Nur freigegebene Rechnungen exportierbar (Status: {inv.status})"}, 400)

    result = exporter.export(inv, fmt)
    if result.success:
        wf_engine.mark_exported(inv, fmt)
        notifier.notify_exported(inv, fmt, result.filename)
    else:
        notifier.notify_export_error(inv, result.error)
    dash.set_export_log(exporter.get_log())

    return _json({
        "success": result.success,
        "filename": result.filename,
        "format": fmt,
        "error": result.error,
    })


# ── API: XRechnung erzeugen ───────────────────────────────────────────

@app.route("/api/generate", methods=["POST"])
def api_generate():
    """Erzeugt eine XRechnung aus JSON-Daten."""
    data = request.get_json(silent=True)
    if not data:
        return _json({"error": "JSON-Body erforderlich"}, 400)

    try:
        # Rechnungsnummer: automatisch vergeben wenn leer
        number = data.get("number", "").strip()
        if not number:
            number = _next_invoice_number(advance=True)
        else:
            # Zaehler hochzaehlen wenn die Nummer mit RE-YYYY-XXXXX beginnt
            import re
            if re.match(r'^RE-\d{4}-\d{5}', number):
                _next_invoice_number(advance=True)

        # Buyer-Name: Firma oder Anrede + Ansprechpartner
        _buyer_firma = data.get("buyer_name", "").strip()
        _buyer_contact = data.get("buyer_contact_name", "").strip()
        _buyer_salut = data.get("buyer_salutation", "").strip()
        if _buyer_firma:
            _buyer_display = _buyer_firma
        elif _buyer_salut and _buyer_contact:
            _buyer_display = f"{_buyer_salut} {_buyer_contact}"
        elif _buyer_contact:
            _buyer_display = _buyer_contact
        else:
            _buyer_display = ""

        # Faelligkeitsdatum aus Zahlungsziel berechnen
        _inv_date = date.fromisoformat(data["date"]) if data.get("date") else date.today()
        _terms = data.get("payment_terms", "")
        _due_date = None
        import re as _re
        _m = _re.search(r'(\d+)\s*Tag', _terms)
        if _m:
            _due_date = _inv_date + timedelta(days=int(_m.group(1)))
        elif "sofort" in _terms.lower():
            _due_date = _inv_date

        inv = Invoice(
            invoice_number=number,
            invoice_date=_inv_date,
            tax_point_date=date.fromisoformat(data["delivery_date"]) if data.get("delivery_date") else None,
            invoice_type_code=data.get("type", "380"),
            currency_code=data.get("currency", "EUR"),
            buyer_reference=data.get("buyer_reference", ""),
            note=data.get("note", ""),
            seller=Seller(
                name=data.get("seller_name", ""),
                address=Address(data.get("seller_street", ""), data.get("seller_city", ""),
                                data.get("seller_postcode", "")),
                electronic_address=data.get("seller_email", ""),
                electronic_address_scheme="EM",
                contact=Contact(data.get("seller_contact", ""),
                                data.get("seller_phone", ""),
                                data.get("seller_contact_email", "")),
                vat_id=data.get("seller_vat", ""),
            ),
            buyer=Buyer(
                name=_buyer_display,
                address=Address(data.get("buyer_street", ""), data.get("buyer_city", ""),
                                data.get("buyer_postcode", "")),
                electronic_address=data.get("buyer_email", ""),
                electronic_address_scheme="EM",
                buyer_reference=data.get("buyer_reference", ""),
                vat_id=data.get("buyer_vat", ""),
            ),
            payment=PaymentInfo(
                means_code=data.get("payment_code", "58"),
                iban=data.get("iban", ""),
                payment_terms=_terms,
                due_date=_due_date,
            ),
        )

        for i, line_data in enumerate(data.get("lines", []), 1):
            inv.lines.append(InvoiceLine(
                line_id=str(i),
                quantity=Decimal(str(line_data.get("quantity", 1))),
                item_name=line_data.get("name", ""),
                unit_price=Decimal(str(line_data.get("price", 0))),
                line_net_amount=Decimal(str(line_data.get("net", 0))),
                tax_rate=Decimal(str(line_data.get("tax_rate", 19))),
            ))

        # Dubletten-Check: Rechnungsnummer bereits vergeben?
        existing = [v for v in invoices.values() if v.invoice_number == inv.invoice_number]
        if existing:
            return _json({"error": f"Rechnungsnummer {inv.invoice_number} existiert bereits im System."}, 409)

        report = validate_invoice(inv)
        xml_bytes = generate_and_serialize(inv)
        inv._direction = "AUSGANG"
        invoices[inv._id] = inv
        inbox.duplicates.register(inv, inv._id)

        # Archivieren
        archive.archive_invoice(inv, xml_bytes, report, direction="AUSGANG")

        # Kaeufer automatisch speichern (falls neu)
        _save_buyer_if_new(
            name=data.get("buyer_name", ""),
            street=data.get("buyer_street", ""),
            post_code=data.get("buyer_postcode", ""),
            city=data.get("buyer_city", ""),
            email=data.get("buyer_email", ""),
            reference=data.get("buyer_reference", ""),
            salutation=data.get("buyer_salutation", ""),
            contact_name=data.get("buyer_contact_name", ""),
        )

        return _json({
            "id": inv._id,
            "number": inv.invoice_number,
            "valid": report.is_valid,
            "errors": report.error_count,
            "xml_size": len(xml_bytes),
            "gross": float(inv.tax_inclusive_amount()),
            "xml_b64": __import__("base64").b64encode(xml_bytes).decode(),
        })

    except Exception as e:
        return _json({"error": str(e)}, 400)


# ── API: Stornierung / Korrekturrechnung ──────────────────────────────

@app.route("/api/invoices/<inv_id>/storno", methods=["POST"])
def api_storno(inv_id):
    """
    Erzeugt eine Stornorechnung (Gutschrift Typ 381) die auf die
    Originalrechnung verweist. Negiert alle Beträge und archiviert
    beides mit gegenseitigem Bezug.

    Voraussetzungen:
    - Nur Ausgangsrechnungen (Typ 380) können storniert werden
    - Status muss FREIGEGEBEN oder EXPORTIERT sein
    - Darf nicht bereits storniert sein
    """
    inv = invoices.get(inv_id)
    if not inv:
        return _json({"error": "Rechnung nicht gefunden"}, 404)

    if inv.status == "STORNIERT":
        return _json({"error": "Rechnung ist bereits storniert."}, 400)

    if inv.invoice_type_code == "381":
        return _json({"error": "Eine Gutschrift kann nicht storniert werden."}, 400)

    if (inv._direction or "AUSGANG") == "EINGANG":
        return _json({"error": "Eingangsrechnungen können nicht storniert werden. Wenden Sie sich an den Rechnungssteller."}, 400)

    if inv.status not in ("FREIGEGEBEN", "EXPORTIERT"):
        return _json({"error": f"Stornierung nur bei freigegebenen oder exportierten Rechnungen möglich (aktuell: {inv.status})."}, 400)

    data = request.get_json(silent=True) or {}
    user = data.get("user", "web-user")
    reason = data.get("reason", "Stornierung")

    import copy, uuid as _uuid

    # Stornonummer erzeugen
    storno_nr = f"ST-{inv.invoice_number}"
    if data.get("storno_number"):
        storno_nr = data["storno_number"]

    # Gutschrift als Kopie mit negierten Beträgen
    storno = Invoice(
        invoice_number=storno_nr,
        invoice_date=date.today(),
        invoice_type_code="381",  # Gutschrift
        currency_code=inv.currency_code,
        buyer_reference=inv.buyer_reference or inv.buyer.buyer_reference,
        preceding_invoice=inv.invoice_number,  # Bezug auf Original
        note=f"Storno zu {inv.invoice_number}: {reason}",
        seller=copy.deepcopy(inv.seller),
        buyer=copy.deepcopy(inv.buyer),
        payment=copy.deepcopy(inv.payment),
    )

    # Positionen mit negierten Beträgen
    for line in inv.lines:
        storno.lines.append(InvoiceLine(
            line_id=line.line_id,
            quantity=line.quantity * -1,
            unit_code=line.unit_code,
            item_name=line.item_name,
            item_description=f"Storno: {line.item_description or line.item_name}",
            unit_price=line.unit_price,
            line_net_amount=line.line_net_amount * -1,
            tax_category=line.tax_category,
            tax_rate=line.tax_rate,
        ))

    # Nachlässe/Zuschläge negieren
    for ac in inv.allowances_charges:
        storno.allowances_charges.append(AllowanceCharge(
            is_charge=not ac.is_charge,  # Nachlass ↔ Zuschlag tauschen
            amount=ac.amount,
            base_amount=ac.base_amount,
            percentage=ac.percentage,
            reason=f"Storno: {ac.reason}",
            tax_category=ac.tax_category,
            tax_rate=ac.tax_rate,
        ))

    # Validieren
    report = validate_invoice(storno)

    # XML erzeugen
    xml_bytes = generate_and_serialize(storno)

    # In System aufnehmen
    storno._direction = "AUSGANG"
    invoices[storno._id] = storno
    storno.status = "FREIGEGEBEN"
    storno.add_audit("STORNO_ERSTELLT", user,
                     f"Stornorechnung zu {inv.invoice_number}: {reason}")
    inbox.duplicates.register(storno, storno._id)

    # Original als storniert markieren
    inv.status = "STORNIERT"
    inv.add_audit("STORNIERT", user,
                  f"Storniert durch {storno_nr}. Grund: {reason}")

    # Beide archivieren
    archive.archive_invoice(storno, xml_bytes, report, direction="AUSGANG")

    # Benachrichtigung
    notifier.notify_new_invoice(storno, report.is_valid, report.error_count)

    # Dashboard
    dash.add_invoice(storno)

    return _json({
        "success": True,
        "storno_id": storno._id,
        "storno_number": storno.invoice_number,
        "original_number": inv.invoice_number,
        "original_status": inv.status,
        "type_code": "381",
        "gross": float(storno.tax_inclusive_amount()),
        "valid": report.is_valid,
        "errors": report.error_count,
        "xml_b64": base64.b64encode(xml_bytes).decode(),
    })


# ── API: Archiv ────────────────────────────────────────────────────────

@app.route("/api/archive")
def api_archive():
    records = archive.list_all()
    # Live-Status und Stornierbarkeit ergänzen
    for rec in records:
        inv_id = rec.get("invoice_id", "")
        inv = invoices.get(inv_id)
        if inv:
            rec["live_status"] = inv.status
            meta = rec.get("invoice_metadata", {})
            type_code = meta.get("type_code", "380")
            # Stornierbar: Ausgangsrechnung (nicht Gutschrift), freigegeben oder exportiert, noch nicht storniert
            rec["stornierbar"] = (
                rec.get("direction") == "AUSGANG"
                and type_code != "381"
                and inv.status in ("FREIGEGEBEN", "EXPORTIERT")
            )
        else:
            rec["live_status"] = rec.get("invoice_metadata", {}).get("status", "")
            rec["stornierbar"] = False
    return _json({"records": records, "count": len(records)})


# ── API: E-Mail Empfang (FR-100) ──────────────────────────────────────

@app.route("/api/email/check", methods=["POST"])
def api_email_check():
    """Prüft das E-Mail-Postfach auf neue Rechnungen."""
    try:
        email_receiver.connect()
        receipts = email_receiver.fetch_new_invoices()

        # Verarbeitete Rechnungen in globale Liste aufnehmen + archivieren
        for r in receipts:
            for item in inbox.items:
                if item.invoice and item.invoice.invoice_number in r.invoice_numbers:
                    if item.invoice._id not in invoices:
                        item.invoice._direction = "EINGANG"
                        invoices[item.invoice._id] = item.invoice
                        dash.add_invoice(item.invoice)
                        # Archivieren
                        report = item.validation or validate_invoice(item.invoice)
                        xml_bytes = generate_and_serialize(item.invoice)
                        archive.archive_invoice(item.invoice, xml_bytes, report, direction="EINGANG")

        return _json({
            "checked": True,
            "new_messages": len(receipts),
            "invoices_found": sum(len(r.invoice_numbers) for r in receipts),
            "receipts": [{
                "sender": r.sender,
                "subject": r.subject,
                "attachments": r.attachment_count,
                "invoices": r.invoice_numbers,
                "error": r.error,
            } for r in receipts],
        })
    except Exception as e:
        return _json({"error": str(e)}, 500)


@app.route("/api/email/receive-log")
def api_email_receive_log():
    """Zeigt das Empfangsprotokoll."""
    return _json({"logs": email_receiver.get_logs(), "count": len(email_receiver.logs)})


# ── API: E-Mail Versand (FR-710) ──────────────────────────────────────

@app.route("/api/invoices/<inv_id>/send", methods=["POST"])
def api_send_invoice(inv_id):
    """Versendet eine Rechnung per E-Mail (XML + druckbare Belegansicht)."""
    inv = invoices.get(inv_id)
    if not inv:
        return _json({"error": "Rechnung nicht gefunden"}, 404)

    data = request.get_json(silent=True) or {}
    recipient = data.get("recipient", "")
    if not recipient:
        return _json({"error": "Empfaenger-E-Mail (recipient) erforderlich"}, 400)

    subject = data.get("subject", "")
    body = data.get("body", "")

    # XML erzeugen
    xml_bytes = generate_and_serialize(inv)
    report = validate_invoice(inv)
    if not report.is_valid:
        return _json({
            "error": f"Rechnung hat {report.error_count} Validierungsfehler. Versand blockiert.",
            "errors": [i.message for i in report.issues if i.severity.value == "ERROR"],
        }, 400)

    # Druckbare Belegansicht (HTML) als PDF-Ersatz erzeugen
    pdf_attachments = []
    try:
        html_response = view_invoice_html(inv_id)
        if hasattr(html_response, 'get_data'):
            html_bytes = html_response.get_data()
        elif isinstance(html_response, str):
            html_bytes = html_response.encode("utf-8")
        else:
            html_bytes = str(html_response).encode("utf-8")
        pdf_filename = f"{inv.invoice_number.replace('/', '_')}_Beleg.html"
        pdf_attachments.append((pdf_filename, html_bytes))
    except Exception:
        pass  # Versand auch ohne Belegansicht moeglich

    send_log = email_sender.send_invoice(
        invoice=inv, recipient=recipient,
        subject=subject, body_text=body, xml_bytes=xml_bytes,
        additional_attachments=pdf_attachments,
    )

    # Versand im Audit-Trail protokollieren (kein erneutes Archivieren!)
    if send_log.success:
        attachments_info = f"XML + Belegansicht" if pdf_attachments else "XML"
        inv.add_audit("EMAIL_VERSENDET",
                      comment=f"An {recipient} ({attachments_info}), Message-ID: {send_log.message_id}")

    return _json({
        "success": send_log.success,
        "message_id": send_log.message_id,
        "recipient": send_log.recipient,
        "subject": send_log.subject,
        "attachment": send_log.attachment_filename,
        "attachments": [send_log.attachment_filename] + [a[0] for a in pdf_attachments],
        "size": send_log.attachment_size,
        "error": send_log.error,
    }, 200 if send_log.success else 500)


@app.route("/api/email/send-log")
def api_email_send_log():
    """Zeigt das Versandprotokoll."""
    return _json({"logs": email_sender.get_logs(), "count": len(email_sender.logs)})


@app.route("/api/email/config")
def api_email_config():
    """Zeigt die E-Mail-Konfiguration (ohne Passwörter)."""
    return _json(email_config.to_json())


@app.route("/api/email/config", methods=["POST"])
def api_email_config_update():
    """Aktualisiert die E-Mail-Konfiguration."""
    global email_config, email_sender, email_receiver
    data = request.get_json(silent=True) or {}

    # Alle konfigurierbaren Felder
    field_map = {
        "imap_host": str, "imap_port": int, "imap_user": str, "imap_folder": str,
        "imap_use_ssl": bool, "imap_processed_folder": str, "imap_error_folder": str,
        "imap_move_after_processing": bool, "poll_interval_seconds": int,
        "smtp_host": str, "smtp_port": int, "smtp_user": str,
        "smtp_use_tls": bool, "smtp_from_address": str, "smtp_from_name": str,
        "max_attachment_size_mb": int, "mandant_name": str,
    }
    for key, typ in field_map.items():
        if key in data:
            setattr(email_config, key, typ(data[key]))

    # Passwörter nur setzen wenn explizit übergeben
    if data.get("imap_password"):
        email_config.imap_password = data["imap_password"]
    if data.get("smtp_password"):
        email_config.smtp_password = data["smtp_password"]

    # Erlaubte Extensions
    if "allowed_extensions" in data:
        if isinstance(data["allowed_extensions"], str):
            email_config.allowed_extensions = [e.strip() for e in data["allowed_extensions"].split(",") if e.strip()]
        else:
            email_config.allowed_extensions = data["allowed_extensions"]

    # Sender/Receiver neu erstellen mit aktueller Config
    # WICHTIG: Echte Objekte verwenden wenn Server konfiguriert ist!
    if email_config.smtp_host and email_config.smtp_password:
        email_sender = EmailSender(email_config)
    else:
        email_sender = MockEmailSender(email_config, str(_DATA / "data" / "sent_mails"))

    if email_config.imap_host and email_config.imap_password:
        email_receiver = EmailReceiver(email_config, inbox)
    else:
        email_receiver = MockEmailReceiver(email_config, inbox, str(_DATA / "data" / "test_mails"))
    poller.receiver = email_receiver  # Poller aktualisieren

    # Config persistieren (inkl. Passwoerter fuer Standalone-Betrieb)
    config_path = _DATA / "data" / "email_config.json"
    config_data = email_config.to_json()
    # Passwoerter separat hinzufuegen (to_json() entfernt sie)
    if email_config.imap_password:
        config_data["imap_password"] = email_config.imap_password
    if email_config.smtp_password:
        config_data["smtp_password"] = email_config.smtp_password
    config_path.write_text(json.dumps(config_data, indent=2, ensure_ascii=False), encoding="utf-8")

    return _json({"saved": True, "config": email_config.to_json()})


@app.route("/api/email/test-imap", methods=["POST"])
def api_test_imap():
    """Testet die IMAP-Verbindung."""
    if not email_config.imap_host:
        return _json({"success": False, "error": "IMAP-Server nicht konfiguriert"})
    if not email_config.imap_password:
        return _json({"success": False, "error": "IMAP-Passwort nicht gesetzt"})

    try:
        import imaplib
        if email_config.imap_use_ssl:
            conn = imaplib.IMAP4_SSL(email_config.imap_host, email_config.imap_port)
        else:
            conn = imaplib.IMAP4(email_config.imap_host, email_config.imap_port)
        conn.login(email_config.imap_user, email_config.imap_password)
        status, data = conn.select(email_config.imap_folder)
        count = int(data[0]) if status == "OK" else 0
        conn.logout()
        return _json({"success": True, "message_count": count,
                       "message": f"Verbunden, {count} Nachrichten in {email_config.imap_folder}"})
    except Exception as e:
        return _json({"success": False, "error": str(e)})


@app.route("/api/email/test-smtp", methods=["POST"])
def api_test_smtp():
    """Testet die SMTP-Verbindung."""
    if not email_config.smtp_host:
        return _json({"success": False, "error": "SMTP-Server nicht konfiguriert"})
    if not email_config.smtp_password:
        return _json({"success": False, "error": "SMTP-Passwort nicht gesetzt"})

    try:
        import smtplib
        if email_config.smtp_use_tls:
            server = smtplib.SMTP(email_config.smtp_host, email_config.smtp_port, timeout=10)
            server.ehlo()
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(email_config.smtp_host, email_config.smtp_port, timeout=10)
        server.login(email_config.smtp_user, email_config.smtp_password)
        server.quit()
        return _json({"success": True, "message": "SMTP-Verbindung erfolgreich"})
    except Exception as e:
        return _json({"success": False, "error": str(e)})


# ── API: Benachrichtigungen (FR-640) ───────────────────────────────────

@app.route("/api/notifications")
def api_notifications():
    recipient = request.args.get("recipient", "")
    limit = int(request.args.get("limit", "50"))
    notifs = notifier.get_all(limit, recipient)
    return _json({
        "notifications": [n.to_dict() for n in notifs],
        "unread": notifier.unread_count(recipient),
    })

@app.route("/api/notifications/read", methods=["POST"])
def api_mark_read():
    data = request.get_json(silent=True) or {}
    nid = data.get("notification_id")
    if nid:
        notifier.mark_read(nid)
    elif data.get("all"):
        notifier.mark_all_read(data.get("recipient", ""))
    return _json({"ok": True, "unread": notifier.unread_count()})


# ── API: Kontierungsvorschläge (FR-260) ────────────────────────────────

@app.route("/api/invoices/<inv_id>/suggestions")
def api_suggestions(inv_id):
    inv = invoices.get(inv_id)
    if not inv:
        return _json({"error": "Nicht gefunden"}, 404)
    sugs = accounting.suggest(inv)
    return _json({"suggestions": [
        {"account": s.account, "cost_center": s.cost_center,
         "project": s.project, "confidence": s.confidence,
         "source": s.source, "based_on": s.based_on}
        for s in sugs
    ]})

@app.route("/api/invoices/<inv_id>/accounting", methods=["POST"])
def api_set_accounting(inv_id):
    inv = invoices.get(inv_id)
    if not inv:
        return _json({"error": "Nicht gefunden"}, 404)
    data = request.get_json(silent=True) or {}
    account = data.get("account", "")
    cost_center = data.get("cost_center", "")
    project = data.get("project", "")
    accounting.learn_from_invoice(inv, account, cost_center, project)
    inv.add_audit("KONTIERUNG", data.get("user", "web"), f"Konto: {account}, KSt: {cost_center}")
    return _json({"saved": True, "account": account, "cost_center": cost_center})

@app.route("/api/accounting/stats")
def api_accounting_stats():
    return _json(accounting.get_stats())


# ── API: Stellvertretung (FR-360) ──────────────────────────────────────

@app.route("/api/deputies")
def api_deputies():
    return _json({"rules": deputies.get_all_rules()})

@app.route("/api/deputies", methods=["POST"])
def api_add_deputy():
    data = request.get_json(silent=True) or {}
    try:
        rule = deputies.add_rule(
            absent=data["absent_user"], deputy=data["deputy_user"],
            start=date.fromisoformat(data.get("start_date", date.today().isoformat())),
            end=date.fromisoformat(data.get("end_date", (date.today() + timedelta(days=7)).isoformat())),
            reason=data.get("reason", ""),
        )
        return _json({"created": True, "rule_id": rule.rule_id})
    except (KeyError, ValueError) as e:
        return _json({"error": f"Fehlende/ungültige Daten: {e}"}, 400)

@app.route("/api/deputies/<rule_id>", methods=["DELETE"])
def api_delete_deputy(rule_id):
    ok = deputies.remove_rule(rule_id)
    return _json({"deleted": ok})


# ── API: Massenbearbeitung ─────────────────────────────────────────────

@app.route("/api/bulk/assign", methods=["POST"])
def api_bulk_assign():
    data = request.get_json(silent=True) or {}
    ids = data.get("invoice_ids", [])
    assigned_to = data.get("assigned_to", "")
    if not ids or not assigned_to:
        return _json({"error": "invoice_ids und assigned_to erforderlich"}, 400)
    inv_list = [invoices[i] for i in ids if i in invoices]
    result = bulk.bulk_assign(inv_list, assigned_to, data.get("user", "web"))
    return _json({"action": result.action, "total": result.total,
                   "success": result.success, "failed": result.failed, "details": result.details})

@app.route("/api/bulk/workflow", methods=["POST"])
def api_bulk_workflow():
    data = request.get_json(silent=True) or {}
    ids = data.get("invoice_ids", [])
    inv_list = [invoices[i] for i in ids if i in invoices]
    result = bulk.bulk_start_workflow(inv_list, data.get("user", "web"))
    return _json({"action": result.action, "total": result.total,
                   "success": result.success, "skipped": result.skipped, "details": result.details})

@app.route("/api/bulk/export", methods=["POST"])
def api_bulk_export():
    data = request.get_json(silent=True) or {}
    ids = data.get("invoice_ids", [])
    fmt = data.get("format", "DATEV")
    inv_list = [invoices[i] for i in ids if i in invoices]
    result = bulk.bulk_export(inv_list, fmt)
    return _json({"action": result.action, "total": result.total,
                   "success": result.success, "skipped": result.skipped,
                   "failed": result.failed, "details": result.details})


# ── API: Aufbewahrungsfristen (FR-530) ─────────────────────────────────

@app.route("/api/retention")
def api_retention():
    all_inv = list(invoices.values())
    checks = retention.check_all(all_inv)
    return _json({"policies": [{"name": p.name, "years": p.retention_years}
                                for p in retention.policies],
                   "invoices": checks})

@app.route("/api/retention/lock", methods=["POST"])
def api_retention_lock():
    data = request.get_json(silent=True) or {}
    inv_id = data.get("invoice_id", "")
    if not inv_id:
        return _json({"error": "invoice_id erforderlich"}, 400)
    retention.lock(inv_id)
    return _json({"locked": True, "invoice_id": inv_id})

@app.route("/api/retention/unlock", methods=["POST"])
def api_retention_unlock():
    data = request.get_json(silent=True) or {}
    inv_id = data.get("invoice_id", "")
    retention.unlock(inv_id)
    return _json({"unlocked": True, "invoice_id": inv_id})


# ── API: IMAP Background Polling ──────────────────────────────────────

@app.route("/api/polling/status")
def api_polling_status():
    return _json(poller.status())

@app.route("/api/polling/start", methods=["POST"])
def api_polling_start():
    data = request.get_json(silent=True) or {}
    interval = data.get("interval", email_config.poll_interval_seconds)
    poller.start(interval)
    return _json({"started": True, "interval": interval})

@app.route("/api/polling/stop", methods=["POST"])
def api_polling_stop():
    poller.stop()
    return _json({"stopped": True})


# ── API: GiroCode QR ───────────────────────────────────────────────────

@app.route("/api/invoices/<inv_id>/qrcode")
def api_qrcode(inv_id):
    """Gibt den EPC/GiroCode QR-Code für eine Rechnung zurück."""
    inv = invoices.get(inv_id)
    if not inv:
        return _json({"error": "Nicht gefunden"}, 404)
    return _json(get_qr_info(inv))


@app.route("/api/invoices/<inv_id>/qrcode.svg")
def api_qrcode_svg(inv_id):
    """Gibt den QR-Code als SVG-Bild zurück."""
    inv = invoices.get(inv_id)
    if not inv:
        abort(404)
    svg = generate_invoice_qr_svg(inv)
    if not svg:
        abort(404)
    return app.response_class(svg, mimetype="image/svg+xml")


# ── API: Logo-Verwaltung ───────────────────────────────────────────────

LOGO_DIR = _DATA / "data" / "logo"
LOGO_MAX_SIZE = 2 * 1024 * 1024  # 2 MB
LOGO_ALLOWED = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"}


def get_logo_path() -> Path | None:
    """Gibt den Pfad zum aktuellen Logo zurück, oder None."""
    if not LOGO_DIR.exists():
        return None
    for ext in (".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"):
        p = LOGO_DIR / f"logo{ext}"
        if p.exists():
            return p
    return None


def get_logo_data_uri() -> str:
    """Gibt das Logo als data:URI zurück (für Inline-Einbettung in HTML)."""
    p = get_logo_path()
    if not p:
        return ""
    mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".svg": "image/svg+xml", ".webp": "image/webp", ".gif": "image/gif"}
    mime = mime_map.get(p.suffix.lower(), "image/png")
    data = base64.b64encode(p.read_bytes()).decode()
    return f"data:{mime};base64,{data}"


@app.route("/api/logo", methods=["GET"])
def api_logo_info():
    """Gibt Info über das aktuelle Logo zurück."""
    p = get_logo_path()
    if not p:
        return _json({"has_logo": False})
    return _json({
        "has_logo": True,
        "filename": p.name,
        "size": p.stat().st_size,
        "url": f"/api/logo/file/{p.name}",
        "data_uri": get_logo_data_uri(),
    })


@app.route("/api/logo/file/<filename>")
def api_logo_file(filename):
    """Liefert die Logo-Datei direkt aus."""
    p = LOGO_DIR / filename
    if not p.exists() or p.suffix.lower() not in LOGO_ALLOWED:
        abort(404)
    return send_from_directory(str(LOGO_DIR), filename)


@app.route("/api/logo", methods=["POST"])
def api_logo_upload():
    """Lädt ein neues Logo hoch. Akzeptiert multipart/form-data oder raw body."""
    LOGO_DIR.mkdir(parents=True, exist_ok=True)

    if "file" in request.files:
        f = request.files["file"]
        data = f.read()
        filename = f.filename or "logo.png"
    elif request.content_type and "json" in request.content_type:
        body = request.get_json(silent=True) or {}
        if "data_uri" in body:
            # data:image/png;base64,iVBOR...
            header, b64 = body["data_uri"].split(",", 1) if "," in body["data_uri"] else ("", body["data_uri"])
            data = base64.b64decode(b64)
            ext = ".png"
            if "jpeg" in header or "jpg" in header:
                ext = ".jpg"
            elif "svg" in header:
                ext = ".svg"
            elif "webp" in header:
                ext = ".webp"
            filename = f"logo{ext}"
        else:
            return _json({"error": "Kein Logo-Daten gefunden"}, 400)
    else:
        data = request.get_data()
        filename = request.headers.get("X-Filename", "logo.png")

    if not data:
        return _json({"error": "Keine Datei übermittelt"}, 400)

    if len(data) > LOGO_MAX_SIZE:
        return _json({"error": f"Logo zu groß ({len(data)//1024} KB). Max: {LOGO_MAX_SIZE//1024} KB"}, 400)

    ext = Path(filename).suffix.lower()
    if ext not in LOGO_ALLOWED:
        return _json({"error": f"Dateityp {ext} nicht erlaubt. Erlaubt: {', '.join(LOGO_ALLOWED)}"}, 400)

    # Erst nach erfolgreicher Validierung altes Logo löschen
    for old in LOGO_DIR.glob("logo.*"):
        old.unlink()

    save_path = LOGO_DIR / f"logo{ext}"
    save_path.write_bytes(data)

    return _json({
        "uploaded": True,
        "filename": save_path.name,
        "size": len(data),
        "url": f"/api/logo/file/{save_path.name}",
    })


@app.route("/api/logo", methods=["DELETE"])
def api_logo_delete():
    """Löscht das Logo."""
    deleted = False
    if LOGO_DIR.exists():
        for old in LOGO_DIR.glob("logo.*"):
            old.unlink()
            deleted = True
    return _json({"deleted": deleted})


# ── Start ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import atexit, signal

    for d in ("data/archiv", "data/export", "data/sent_mails", "data/test_mails", "data/logo"):
        (_DATA / d).mkdir(parents=True, exist_ok=True)

    load_data()

    # Beim Beenden automatisch speichern
    def _shutdown():
        print("\n  Speichere Daten...")
        auto_save()
        print(f"  {len(invoices)} Rechnungen gespeichert.")

    atexit.register(_shutdown)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    print(f"\n  E-Rechnungssystem Web-UI")
    print(f"  http://localhost:{port}")
    print(f"  {len(invoices)} Rechnungen geladen")
    print(f"  Daten: {_DATA / 'data'}")
    print(f"  Auto-Save: aktiv (nach jeder Aenderung)\n")
    app.run(host="0.0.0.0", port=port, debug=False)
