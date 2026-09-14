#!/usr/bin/env python3
"""
E-Rechnungssystem – Web-Server
Flask-basiertes Frontend + REST-API
Verbindet alle Backend-Module mit dem HTML-Frontend.
"""
from __future__ import annotations
import json, os, sys, hashlib, base64, re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, abort, session, redirect, url_for, render_template_string, Response
from werkzeug.security import check_password_hash, generate_password_hash

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
import zugang
from pdf_import import parse_energieberatung_pdf
from zugferd_writer import generate_zugferd_pdf
from kosit_validator import validate_with_kosit, is_available as kosit_available
from suppliers import SupplierManager
from transactions import (TransactionManager, STEP_KEYS, STEP_LABELS,
                           DOC_PREFIXES, make_position, calc_step_totals)
from doc_generator import DocumentGenerator, _anrede as _doc_anrede
from dunning import DunningManager
from products import ProductManager, create_demo_products

# ── App ────────────────────────────────────────────────────────────────

app = Flask(__name__,
            static_folder=str(_BASE / "static"),
            static_url_path="/static")

# ── Auth ────────────────────────────────────────────────────────────────
_AUTH_FILE = _DATA / "data" / "auth.json"
_SK_FILE   = _DATA / "data" / ".secret_key"

def _load_secret_key():
    if _SK_FILE.exists():
        return _SK_FILE.read_bytes()
    import secrets
    key = secrets.token_bytes(32)
    _SK_FILE.write_bytes(key)
    return key

app.secret_key = _load_secret_key()

def _load_users():
    if _AUTH_FILE.exists():
        return json.loads(_AUTH_FILE.read_text()).get("users", [])
    return []

def _save_users(users):
    _AUTH_FILE.write_text(json.dumps({"users": users}, indent=2))

def _check_login(username, password):
    for u in _load_users():
        if u["username"] == username and check_password_hash(u["password_hash"], password):
            return True
    return False

_LOGIN_SKIP = {"/login", "/api/auth/change-password", "/static", "/.well-known"}
# Externe API-Endpoints haben eigene X-API-Key-Authentifizierung,
# daher Session-Auth-Pflicht hier überspringen.
# Liste, damit lokale Erweiterungen eigene Praefixe anmelden koennen
# (siehe _load_extensions); sie pruefen den Key dann in der Route selbst.
_API_KEY_PATHS = ["/api/external/", "/api/assistant/"]

@app.before_request
def require_login():
    path = request.path
    if not _AUTH_FILE.exists():
        if not zugang.SERVERMODUS:
            # Einzelplatz: Server lauscht nur auf 127.0.0.1, keine Anmeldung noetig.
            session["logged_in"] = True
            session.setdefault("username", "lokal")
            return
        # Serverbetrieb ohne Benutzer: nur die Ersteinrichtung ist erreichbar.
        if path == "/einrichten" or path.startswith("/static/"):
            return
        if path.startswith("/api/"):
            return jsonify({"error": "Einrichtung erforderlich", "login_required": True,
                            "setup_url": "/einrichten"}), 401
        return redirect("/einrichten")
    if any(path == p or path.startswith(p + "/") for p in _LOGIN_SKIP):
        return
    if any(path.startswith(p) for p in _API_KEY_PATHS):
        return   # Auth wird in der Route selbst via X-API-Key geprüft
    if not session.get("logged_in"):
        if path.startswith("/api/"):
            return jsonify({"error": "Nicht authentifiziert", "login_required": True}), 401
        return redirect("/login")

@app.route("/login", methods=["GET", "POST"])
def login():
    if not _AUTH_FILE.exists():
        return redirect("/einrichten" if zugang.SERVERMODUS else "/")
    error = None
    username = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if _check_login(username, password):
            session["logged_in"] = True
            session["username"] = username
            return redirect("/")
        error = "Ungültiger Benutzername oder Passwort."
    tpl = (_BASE / "static" / "login.html").read_text(encoding="utf-8")
    return render_template_string(tpl, error=error, username=username)

@app.route("/einrichten", methods=["GET", "POST"])
def einrichten():
    """Erstes Benutzerkonto im Serverbetrieb anlegen (Einrichtungscode noetig)."""
    if _AUTH_FILE.exists() or not zugang.SERVERMODUS:
        return redirect("/login" if _AUTH_FILE.exists() else "/")
    error = None
    username = ""
    if request.method == "POST":
        adresse = request.remote_addr or "?"
        username = request.form.get("username", "").strip()
        if zugang.gesperrt(adresse):
            error = "Zu viele Fehlversuche. Bitte in 15 Minuten erneut versuchen."
        else:
            error = zugang.pruefe_und_lege_an(
                _DATA / "data", _AUTH_FILE, request.form.get("code", ""), username,
                request.form.get("password", ""), request.form.get("password2", ""),
                generate_password_hash)
            if error is None:
                session.clear()
                session["logged_in"] = True
                session["username"] = username
                return redirect("/")
            if "Einrichtungscode" in error:
                zugang.fehlversuch(adresse)
    tpl = (_BASE / "static" / "einrichten.html").read_text(encoding="utf-8")
    return render_template_string(tpl, error=error, username=username)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/api/auth/change-password", methods=["POST"])
def change_password():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    old_pw   = data.get("old_password", "")
    new_pw   = data.get("new_password", "")
    if not username or not old_pw or not new_pw:
        return jsonify({"ok": False, "error": "Fehlende Felder"}), 400
    if len(new_pw) < 6:
        return jsonify({"ok": False, "error": "Neues Passwort muss mindestens 6 Zeichen haben"}), 400
    users = _load_users()
    for u in users:
        if u["username"] == username and check_password_hash(u["password_hash"], old_pw):
            u["password_hash"] = generate_password_hash(new_pw)
            _save_users(users)
            return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Benutzername oder aktuelles Passwort falsch"}), 401


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
    smtp_from_name="Demo GmbH", mandant_name="Demo GmbH",
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
    # Dual-Write nach Postgres (V002) — graceful: JSON bleibt führend.
    try:
        import db_pg
        db_pg.sync_invoices()
    except Exception as e:
        print(f"[db_pg] auto_save dual-write übersprungen: {e}", flush=True)


def _archive_ausgang(inv, xml_bytes, report, pdf_bytes=None):
    """Ausgangsrechnung archivieren — IMMER mit PDF.

    Die XRechnung-XML ist der Originalbeleg, aber niemand kann sie ansehen. Damit
    die Buchhaltung unter „Buchungen" einen lesbaren Beleg zeigt, wandert das
    erzeugte PDF mit ins Archiv (31.07.2026). Schlägt die PDF-Erzeugung fehl,
    wird trotzdem archiviert — die XML darf nie verloren gehen.
    """
    if pdf_bytes is None:
        try:
            pdf_bytes = _build_invoice_pdf(inv)[1]
        except Exception as e:
            print(f"[archiv] PDF für {getattr(inv, 'invoice_number', '?')} nicht erzeugt: {e}",
                  flush=True)
    return archive.archive_invoice(inv, xml_bytes, report, direction="AUSGANG",
                                   pdf_bytes=pdf_bytes)


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
        # Keine gespeicherten Daten. ACHTUNG: In Produktivinstanzen (Flag data/.no_demo)
        # NIEMALS automatisch Demo-Daten erzeugen — ein transient leeres/fehlendes
        # invoices.json würde sonst echte Rechnungen unwiederbringlich überschreiben.
        if (_DATA / "data" / ".no_demo").exists():
            print("  WARNUNG: keine gespeicherten Rechnungen gefunden — "
                  "Demo-Erzeugung durch data/.no_demo unterdrückt (Produktivschutz)")
        else:
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
_MANDANT_MASTER_FILE = _DATA / "data" / "mandant.json"

# mandant.json (kanonisch) → b2b-Schema-Mapping (für Read-Merge)
_MANDANT_MAP_READ = {
    "company_name":         "name",
    "person_name":          "contact_name",
    "strasse":              "street",
    "hausnummer":           "house_number",
    "plz":                  "post_code",
    "ort":                  "city",
    "email":                "email",
    "telefon":              "contact_phone",
    "iban_display":         "iban",
    "bic":                  "bic",
    "vat_id":               "vat_id",
    "tax_registration_id":  "tax_registration_id",
    "currency":             "currency",
}

def _load_mandant_settings() -> dict:
    """Lädt mandant_settings.json (Counter/Templates) und überlagert mit mandant.json (kanonische Stammdaten)."""
    settings = {}
    if _MANDANT_FILE.exists():
        try:
            settings = json.loads(_MANDANT_FILE.read_text("utf-8"))
        except Exception:
            pass
    # Master aus mandant.json überschreibt — sodass Stammdaten-Änderung an mandant.json sofort greift
    if _MANDANT_MASTER_FILE.exists():
        try:
            master = json.loads(_MANDANT_MASTER_FILE.read_text("utf-8"))
            for src, dst in _MANDANT_MAP_READ.items():
                if master.get(src) not in (None, ""):
                    settings[dst] = master[src]
        except Exception:
            pass
    return settings

def _save_mandant_settings(data: dict):
    """Speichert in mandant_settings.json (Counter, Templates) UND propagiert die Stammdaten zurück nach mandant.json."""
    _MANDANT_FILE.parent.mkdir(parents=True, exist_ok=True)
    _MANDANT_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    # Reverse-Sync: Stammdaten aus settings → mandant.json (Master bleibt aktuell)
    if _MANDANT_MASTER_FILE.exists():
        try:
            master = json.loads(_MANDANT_MASTER_FILE.read_text("utf-8"))
            changed = False
            for src, dst in _MANDANT_MAP_READ.items():
                if dst in data and data[dst] != master.get(src):
                    master[src] = data[dst]
                    changed = True
            if changed:
                _MANDANT_MASTER_FILE.write_text(json.dumps(master, indent=2, ensure_ascii=False), "utf-8")
        except Exception:
            pass

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


# Vorgang-Rechnungen ziehen ihre Nummer aus DEMSELBEN 5-stelligen Kreis wie die
# Energieausweis-/Direktrechnungen (einheitlich, GoBD) — statt aus dem separaten
# 4-stelligen number_sequences["RE-…"]. Andere Belegarten (V/ANG/LIEF/AR…) bleiben.
txn_mgr.invoice_number_fn = lambda: _next_invoice_number(advance=True)


GUTSCHRIFT_TERMS = ("Der Gutschriftsbetrag wird mit offenen Forderungen verrechnet "
                    "bzw. auf das uns bekannte Konto erstattet.")


def _next_gutschrift_number(advance: bool = False) -> str:
    """Naechste Gutschriftsnummer GS-YYYY-XXXXX — eigener, lueckenloser Kreis.

    Stornos behalten ihr ST-<Rechnungsnr>-Schema (1:1-Bezug); Gutschriften
    (Teil-/Kulanzgutschrift, auch ohne Bezug) brauchen eine eigene Folge.
    """
    settings = _load_mandant_settings()
    year = date.today().year
    stored_year = settings.get("gutschrift_number_year", year)
    counter = settings.get("gutschrift_number_counter", 0)
    if stored_year != year:
        counter = 0
    next_counter = counter + 1
    number = f"GS-{year}-{next_counter:05d}"
    while any(inv.invoice_number == number for inv in invoices.values()):
        next_counter += 1
        number = f"GS-{year}-{next_counter:05d}"
    if advance:
        settings["gutschrift_number_year"] = year
        settings["gutschrift_number_counter"] = next_counter
        _save_mandant_settings(settings)
    return number


@app.route("/api/mandant/settings")
def api_mandant_settings_get():
    return _json(_load_mandant_settings())

@app.route("/api/mandant/next-number")
def api_mandant_next_number():
    """Gibt die naechste freie Rechnungsnummer als Vorschau zurueck (ohne hochzuzaehlen).
    ?type=381 liefert die naechste Gutschriftsnummer."""
    if request.args.get("type") == "381":
        return _json({"number": _next_gutschrift_number(advance=False)})
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


# ── Optionale lokale Erweiterungen ────────────────────────────────────
# Ein Verzeichnis 'erweiterungen/' neben dieser Datei darf zusaetzliche Module
# beisteuern (eigene Routen, Anbindung an Fremdsysteme). Es ist NICHT Teil der
# Distribution — fehlt es, laeuft die Anwendung unveraendert weiter.
#
# Jedes Modul stellt bereit:
#     def einrichten(app, hook): ...
# 'hook(name, funktion)' meldet eine Funktion zu einem Ereignis an. Bekannte
# Ereignisse: 'buyers_saved' (buyers: list[dict]).

_HOOKS: dict[str, list] = {}


def register_hook(name: str, fn) -> None:
    _HOOKS.setdefault(name, []).append(fn)


def run_hook(name: str, *args) -> None:
    """Erweiterungen aufrufen. Fehler werden geloggt, nie weitergereicht —
    eine Erweiterung darf den Kernablauf nicht anhalten."""
    for fn in _HOOKS.get(name, ()):
        try:
            fn(*args)
        except Exception as e:
            print(f"[erweiterung:{name}] {e}", flush=True)


def _load_extensions() -> None:
    """Wird am Ende dieses Moduls aufgerufen — also unabhaengig davon, ueber
    welchen Starter (run.py, webapp.py, WSGI) die Anwendung hochkommt."""
    verzeichnis = _BASE / "erweiterungen"
    if not verzeichnis.is_dir():
        return
    import importlib
    if str(_BASE) not in sys.path:
        sys.path.insert(0, str(_BASE))
    for datei in sorted(verzeichnis.glob("*.py")):
        if datei.name.startswith("_"):
            continue
        try:
            modul = importlib.import_module("erweiterungen." + datei.stem)
            if hasattr(modul, "einrichten"):
                modul.einrichten(app, register_hook)
            print(f"[erweiterung] {datei.stem} geladen", flush=True)
        except Exception as e:
            print(f"[erweiterung] {datei.stem} übersprungen: {e}", flush=True)


# ── API: Rechnungsempfaenger / Kunden ─────────────────────────────────

_BUYERS_FILE = _DATA / "data" / "buyers.json"

def _load_buyers() -> list[dict]:
    if _BUYERS_FILE.exists():
        try:
            buyers = json.loads(_BUYERS_FILE.read_text("utf-8"))
        except Exception:
            return []
        for b in buyers:
            if "contacts" not in b or not isinstance(b.get("contacts"), list):
                b["contacts"] = []
        return buyers
    return []


_CONTACT_FIELDS = ("salutation", "name", "role", "email", "phone", "mobile", "note")


def _new_contact_id(existing: list[dict]) -> str:
    used = {c.get("id", "") for c in existing}
    n = 1
    while f"c{n}" in used:
        n += 1
    return f"c{n}"


def _normalize_contact(raw: dict, existing: list[dict], keep_id: str = "") -> dict:
    out = {f: str(raw.get(f, "") or "").strip() for f in _CONTACT_FIELDS}
    out["id"] = keep_id or raw.get("id") or _new_contact_id(existing)
    return out

def _save_buyers(buyers: list[dict]):
    _BUYERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _BUYERS_FILE.write_text(json.dumps(buyers, indent=2, ensure_ascii=False), "utf-8")
    try:
        import db_pg
        db_pg.sync_buyers()
    except Exception as e:
        print(f"[db_pg] _save_buyers dual-write übersprungen: {e}", flush=True)
    run_hook("buyers_saved", buyers)


# ── Ausgangsmail-Protokoll (Menüpunkt "Mails") ──────────────────────────
# Jede tatsächlich versendete E-Mail wird hier abgelegt, damit sie später
# aufgerufen und gedruckt werden kann.
_SENT_MAILS_FILE = _DATA / "data" / "sent_mails.json"


def _load_sent_mails() -> list[dict]:
    if _SENT_MAILS_FILE.exists():
        try:
            return json.loads(_SENT_MAILS_FILE.read_text("utf-8"))
        except Exception:
            return []
    return []


def _save_sent_mails(mails: list[dict]):
    _SENT_MAILS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SENT_MAILS_FILE.write_text(json.dumps(mails, indent=2, ensure_ascii=False), "utf-8")


def _log_sent_mail(to_email: str, subject: str, body: str, *,
                   doc_type: str = "", reference: str = "",
                   attachments=None, status: str = "GESENDET",
                   from_addr: str = "", error: str = "",
                   message_id: str = "") -> dict:
    """Protokolliert eine Ausgangsmail. Wirft NIE — Logging darf den Versand
    nicht gefährden."""
    try:
        from datetime import datetime as _dt
        mails = _load_sent_mails()
        entry = {
            "id": _dt.now().strftime("%Y%m%d%H%M%S%f"),
            "sent_at": _dt.now().isoformat(timespec="seconds"),
            "to": to_email or "",
            "from": from_addr or "",
            "subject": subject or "",
            "body": body or "",
            "doc_type": doc_type or "",
            "reference": reference or "",
            "attachments": [a for a in (attachments or []) if a],
            "status": status or "GESENDET",
            "error": error or "",
            # Message-ID = Schlüssel für den späteren Bounce-Abgleich.
            "message_id": message_id or "",
            # Zustellstatus: zunächst nur vom Relay angenommen.
            "delivery": "ANGENOMMEN" if (status or "GESENDET") == "GESENDET" else "",
        }
        mails.append(entry)
        _save_sent_mails(mails)
        return entry
    except Exception as e:
        print(f"[mail-log] konnte Ausgangsmail nicht protokollieren: {e}", flush=True)
        return {}


def _generate_buyer_id(name: str, existing=None) -> str:
    """Erzeugt eine 5-stellige Kunden-ID aus dem Firmennamen — kollisionsfrei
    gegen bereits vergebene IDs (`existing`). Bei Kollision wird der Name
    gesalzen neu gehasht, im Notfall sequenziell hochgezählt."""
    import hashlib
    existing = set(existing or ())
    base = name.strip()
    for salt in range(10000):
        src = base if salt == 0 else f"{base}#{salt}"
        h = hashlib.md5(src.encode("utf-8")).hexdigest()
        digits = "".join(c for c in h if c.isdigit())
        cand = digits[:5] if len(digits) >= 5 else digits.ljust(5, "0")
        if cand not in existing:
            return cand
    n = 10000
    while str(n) in existing:
        n += 1
    return str(n)


def _save_buyer_if_new(name: str, street: str = "", house_number: str = "",
                       post_code: str = "", city: str = "", email: str = "",
                       phone: str = "", reference: str = "",
                       vat_id: str = "", salutation: str = "",
                       contact_name: str = "", is_private: bool = False,
                       contact_salutation: str = "") -> dict | None:
    """Speichert einen Kaeufer wenn er noch nicht existiert.
    name = Firma (kann leer sein bei Privatpersonen)
    contact_name = Ansprechpartner (Vor- und Nachname)
    is_private = Privatperson (vereinfachte Rechnung, keine BR-Pflicht)
    """
    display = name.strip() if name else contact_name.strip() if contact_name else ""
    if not display:
        return None

    buyers = _load_buyers()
    email_norm = (email or "").strip().lower()
    # Namen duerfen einen Zeilenumbruch fuer die Anschrift tragen — beim
    # Dublettenvergleich wird Leerraum eingeebnet, sonst gilt derselbe Kunde
    # mit und ohne Umbruch als zwei verschiedene.
    _flach = lambda s: " ".join((s or "").split()).lower()
    display_norm = _flach(display)

    # Duplikat-Erkennung: NUR wenn E-Mail UND Anzeigename übereinstimmen,
    # bzw. (ohne E-Mail) der Anzeigename übereinstimmt. So gelten z.B. eine
    # Privatperson und ihre Firma mit derselben E-Mail als VERSCHIEDENE Kunden
    # (z.B. „Oliver Frentrup" privat vs. „Finanzkontor" Firma).
    for b in buyers:
        b_email = (b.get("email") or "").strip().lower()
        b_display = _flach(b.get("name", "") or b.get("contact_name", ""))
        if email_norm and b_email and b_email == email_norm and b_display == display_norm:
            return b
    for b in buyers:
        if _flach(b.get("name", "") or b.get("contact_name", "")) == display_norm:
            return b
    buyer = {
        "id": _generate_buyer_id(display, {b.get("id") for b in buyers}),
        "name": name.strip(),           # Firma (leer bei Privatpersonen)
        "street": street.strip(),
        "house_number": house_number.strip(),
        "post_code": post_code.strip(),
        "city": city.strip(),
        "email": email.strip(),
        "phone": phone.strip(),
        "reference": reference.strip(),
        "vat_id": vat_id.strip(),
        "salutation": salutation.strip(),
        # Anrede des ANSPRECHPARTNERS — getrennt von `salutation`, die zur
        # Adresszeile gehört (Firma/Familie/Eheleute). Ohne die Trennung wurde aus
        # „Firma" + Ansprechpartner „Müller" die Anrede „Firma Müller,"
        # (07.09.2026).
        "contact_salutation": contact_salutation.strip(),
        "contact_name": contact_name.strip(),
        "is_private": bool(is_private),
        "contacts": [],
    }
    buyers.append(buyer)
    _save_buyers(buyers)
    return buyer


@app.route("/api/external/upsert-customer", methods=["POST"])
def api_external_upsert_customer():
    """Kunde (Rechnungsempfaenger) von extern anlegen — z.B. EPBD-App nach
    Partner-Zulassung oder Endkunden-Ausweis-Freigabe. Dedup über
    _save_buyer_if_new (E-Mail+Name bzw. Name); bestehende Kunden werden
    NICHT überschrieben."""
    expected = _load_external_api_key()
    provided = request.headers.get("X-API-Key", "")
    if not expected or provided != expected:
        return _json({"error": "Ungültiger oder fehlender API-Key"}, 401)

    c = request.get_json(silent=True) or {}
    vorname = (c.get("vorname") or "").strip()
    nachname = (c.get("nachname") or "").strip()
    contact = (vorname + " " + nachname).strip() or (c.get("ansprechpartner") or "").strip()

    before_ids = {b.get("id") for b in _load_buyers()}
    buyer = _save_buyer_if_new(
        name=(c.get("firma") or "").strip(),
        street=(c.get("strasse") or "").strip(),
        house_number=(c.get("hausnummer") or "").strip(),
        post_code=str(c.get("plz") or "").strip(),
        city=(c.get("ort") or "").strip(),
        email=(c.get("email") or "").strip(),
        phone=(c.get("tel") or c.get("telefon") or "").strip(),
        reference=(c.get("quelle") or "").strip(),
        salutation=(c.get("anrede") or "").strip(),
        contact_name=contact,
        is_private=bool(c.get("is_private")),
    )
    if not buyer:
        return _json({"error": "Weder Firma noch Name angegeben"}, 400)
    existed = buyer.get("id") in before_ids
    print(f"[external/upsert-customer] {'vorhanden' if existed else 'NEU'}: "
          f"{buyer.get('name') or buyer.get('contact_name')} ({buyer.get('id')}) "
          f"quelle={c.get('quelle') or '—'}", flush=True)
    return _json({"ok": True, "buyer_id": buyer.get("id"), "existed": existed})


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
        house_number=data.get("house_number", ""),
        post_code=data.get("post_code", ""),
        city=data.get("city", ""),
        email=data.get("email", ""),
        phone=data.get("phone", ""),
        reference=data.get("reference", ""),
        vat_id=data.get("vat_id", ""),
        salutation=data.get("salutation", ""),
        contact_name=contact_name,
        contact_salutation=data.get("contact_salutation", ""),
        is_private=bool(data.get("is_private", False)),
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
            for key in ["name", "street", "house_number", "post_code", "city", "email",
                        "phone", "reference", "vat_id", "salutation", "contact_name",
                        "contact_salutation"]:
                if key in data:
                    b[key] = data[key].strip() if isinstance(data[key], str) else data[key]
            if "is_private" in data:
                b["is_private"] = bool(data["is_private"])
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


@app.route("/api/buyers/<buyer_id>/contacts", methods=["POST"])
def api_buyer_contact_add(buyer_id):
    """Legt einen Ansprechpartner unter einem Kunden an."""
    data = request.get_json(silent=True) or {}
    if not str(data.get("name", "")).strip():
        return _json({"error": "Name des Ansprechpartners erforderlich"}, 400)
    buyers = _load_buyers()
    for b in buyers:
        if b.get("id") == buyer_id:
            contact = _normalize_contact(data, b["contacts"])
            b["contacts"].append(contact)
            _save_buyers(buyers)
            return _json({"saved": True, "contact": contact})
    return _json({"error": "Kunde nicht gefunden"}, 404)


@app.route("/api/buyers/<buyer_id>/contacts/<contact_id>", methods=["PUT"])
def api_buyer_contact_update(buyer_id, contact_id):
    """Aktualisiert einen Ansprechpartner."""
    data = request.get_json(silent=True) or {}
    if not str(data.get("name", "")).strip():
        return _json({"error": "Name des Ansprechpartners erforderlich"}, 400)
    buyers = _load_buyers()
    for b in buyers:
        if b.get("id") == buyer_id:
            for i, c in enumerate(b["contacts"]):
                if c.get("id") == contact_id:
                    b["contacts"][i] = _normalize_contact(data, b["contacts"], keep_id=contact_id)
                    _save_buyers(buyers)
                    return _json({"saved": True, "contact": b["contacts"][i]})
            return _json({"error": "Ansprechpartner nicht gefunden"}, 404)
    return _json({"error": "Kunde nicht gefunden"}, 404)


@app.route("/api/buyers/<buyer_id>/contacts/<contact_id>", methods=["DELETE"])
def api_buyer_contact_delete(buyer_id, contact_id):
    """Loescht einen Ansprechpartner."""
    buyers = _load_buyers()
    for b in buyers:
        if b.get("id") == buyer_id:
            before = len(b["contacts"])
            b["contacts"] = [c for c in b["contacts"] if c.get("id") != contact_id]
            if len(b["contacts"]) == before:
                return _json({"error": "Ansprechpartner nicht gefunden"}, 404)
            _save_buyers(buyers)
            return _json({"deleted": True})
    return _json({"error": "Kunde nicht gefunden"}, 404)


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
            known_headers = ["firma", "name", "strasse", "street", "hausnummer", "house_number", "hausnr",
                             "plz", "post_code", "ort", "city", "email", "e-mail",
                             "telefon", "phone", "tel", "referenz", "reference",
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
                    elif h in ("hausnummer", "house_number", "hausnr", "nr"):
                        header_map["house_number"] = i
                    elif h in ("plz", "post_code", "postleitzahl", "zip"):
                        header_map["post_code"] = i
                    elif h in ("ort", "city", "stadt"):
                        header_map["city"] = i
                    elif h in ("email", "e-mail", "mail"):
                        header_map["email"] = i
                    elif h in ("telefon", "phone", "tel", "mobil"):
                        header_map["phone"] = i
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
    w.writerow(["Firma", "Strasse", "Hausnummer", "PLZ", "Ort", "E-Mail", "Telefon",
                "Referenz", "USt-ID", "Anrede", "Ansprechpartner"])
    for b in buyers:
        w.writerow([b.get("name",""), b.get("street",""), b.get("house_number",""),
                    b.get("post_code",""), b.get("city",""), b.get("email",""), b.get("phone",""),
                    b.get("reference",""), b.get("vat_id",""), b.get("salutation",""),
                    b.get("contact_name","")])
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
    tags = _parse_tags(request.form.get("tags"))
    if not file or not file.filename:
        return _json({"error": "Keine Datei"}, 400)

    import hashlib
    from werkzeug.utils import secure_filename

    doc_dir = _DATA / "data" / "supplier_docs" / sid / doc_type
    doc_dir.mkdir(parents=True, exist_ok=True)

    safe_name = secure_filename(file.filename) or "dokument.pdf"

    idx_file = _DATA / "data" / "supplier_docs" / sid / "index.json"
    idx = []
    if idx_file.exists():
        try:
            idx = json.loads(idx_file.read_text("utf-8"))
        except Exception:
            pass

    next_v = _next_version_for(idx, file.filename, doc_type)
    filename = _versioned_filename(safe_name, next_v)
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

    entry = {
        "filename": filename,
        "original_name": file.filename,
        "type": doc_type,
        "tags": tags,
        "version": next_v,
        "is_current": True,
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


@app.route("/api/suppliers/<sid>/documents/<doc_type>/<filename>", methods=["PUT"])
def api_supplier_doc_update(sid, doc_type, filename):
    """Aktualisiert Metadaten (Typ, Tags) eines Lieferanten-Dokuments mit Datei-Verschiebung."""
    data = request.json or {}
    new_type = (data.get("type") or doc_type).strip() or doc_type
    new_tags = _parse_tags(",".join(data.get("tags"))) if isinstance(data.get("tags"), list) else _parse_tags(data.get("tags"))

    base = _DATA / "data" / "supplier_docs" / sid
    old_dir = base / doc_type
    old_path = old_dir / filename
    if not old_path.exists():
        return _json({"error": "Datei nicht gefunden"}, 404)

    new_dir = base / new_type
    new_dir.mkdir(parents=True, exist_ok=True)
    new_path = new_dir / filename
    if new_type != doc_type:
        counter = 1
        stem, ext = (filename.rsplit(".", 1) + [""])[:2]
        new_filename = filename
        while new_path.exists():
            new_filename = f"{stem}_{counter}.{ext}" if ext else f"{stem}_{counter}"
            new_path = new_dir / new_filename
            counter += 1
        old_path.rename(new_path)
        filename_after = new_filename
    else:
        filename_after = filename

    idx_file = base / "index.json"
    if idx_file.exists():
        try:
            idx = json.loads(idx_file.read_text("utf-8"))
            for entry in idx:
                if entry.get("filename") == filename and entry.get("type") == doc_type:
                    entry["type"] = new_type
                    entry["tags"] = new_tags
                    if new_type != doc_type:
                        entry["filename"] = filename_after
                    break
            idx_file.write_text(json.dumps(idx, indent=2, ensure_ascii=False), "utf-8")
        except Exception as e:
            return _json({"error": f"Index-Fehler: {e}"}, 500)

    return _json({"updated": True, "type": new_type, "filename": filename_after, "tags": new_tags})


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


# ═══════════════════════════════════════════════════════════════
# KUNDEN-DOKUMENTE (analog zu Lieferanten-Dokumenten)
# Speicher: data/buyer_docs/<buyer_id>/<doc_type>/<filename>
# ═══════════════════════════════════════════════════════════════
def _buyer_by_id(bid):
    for b in _load_buyers():
        if b.get("id") == bid:
            return b
    return None


def _parse_tags(raw):
    """Kommagetrennten Tag-String zu sortierter Liste normalisieren."""
    if not raw:
        return []
    parts = [t.strip().lower() for t in str(raw).split(",")]
    return sorted({p for p in parts if p})


def _next_version_for(idx, original_name, doc_type):
    """Findet die nächste Versionsnummer für (original_name, doc_type).
    Setzt vorhandene 'is_current' auf False (mutiert idx in-place)."""
    next_v = 1
    for entry in idx:
        if not isinstance(entry, dict):
            continue
        # Backwards-Kompatibilität: ältere Einträge haben kein is_current — gelten als current
        is_curr = entry.get("is_current", True)
        if (entry.get("original_name") == original_name
                and entry.get("type") == doc_type
                and is_curr):
            entry["is_current"] = False
            next_v = max(next_v, (entry.get("version") or 1) + 1)
    return next_v


def _versioned_filename(filename, version):
    """Bei Version > 1: <stem>.v<N>.<ext>, sonst unveraendert."""
    if version <= 1:
        return filename
    stem, ext = (filename.rsplit(".", 1) + [""])[:2]
    return f"{stem}.v{version}.{ext}" if ext else f"{stem}.v{version}"


@app.route("/api/buyers/<bid>/documents", methods=["POST"])
def api_buyer_doc_upload(bid):
    """Lädt ein Dokument hoch und verknüpft es mit einem Kunden."""
    buyer = _buyer_by_id(bid)
    if not buyer:
        return _json({"error": "Kunde nicht gefunden"}, 404)

    file = request.files.get("file")
    doc_type = request.form.get("type", "sonstiges")
    tags = _parse_tags(request.form.get("tags"))
    if not file or not file.filename:
        return _json({"error": "Keine Datei"}, 400)

    import hashlib
    from werkzeug.utils import secure_filename

    doc_dir = _DATA / "data" / "buyer_docs" / bid / doc_type
    doc_dir.mkdir(parents=True, exist_ok=True)

    safe_name = secure_filename(file.filename) or "dokument.pdf"

    # Index früh laden — für Versions-Erkennung
    idx_file = _DATA / "data" / "buyer_docs" / bid / "index.json"
    idx = []
    if idx_file.exists():
        try:
            idx = json.loads(idx_file.read_text("utf-8"))
        except Exception:
            pass

    # Versionierung: anhand original_name + type
    next_v = _next_version_for(idx, file.filename, doc_type)
    filename = _versioned_filename(safe_name, next_v)
    filepath = doc_dir / filename
    # Disk-Konflikt durchnummerieren falls trotzdem vorhanden
    counter = 1
    stem, ext = (filename.rsplit(".", 1) + [""])[:2]
    while filepath.exists():
        filename = f"{stem}_{counter}.{ext}" if ext else f"{stem}_{counter}"
        filepath = doc_dir / filename
        counter += 1

    file_bytes = file.read()
    filepath.write_bytes(file_bytes)
    sha = hashlib.sha256(file_bytes).hexdigest()[:16]

    entry = {
        "filename": filename,
        "original_name": file.filename,
        "type": doc_type,
        "tags": tags,
        "version": next_v,
        "is_current": True,
        "size": len(file_bytes),
        "hash": sha,
        "uploaded_at": date.today().isoformat(),
        "buyer_id": bid,
        "buyer_name": buyer.get("name", ""),
    }
    idx.append(entry)
    idx_file.write_text(json.dumps(idx, indent=2, ensure_ascii=False), "utf-8")

    return _json({"uploaded": True, "document": entry})


@app.route("/api/buyers/<bid>/documents")
def api_buyer_docs_list(bid):
    """Liste aller Dokumente eines Kunden."""
    idx_file = _DATA / "data" / "buyer_docs" / bid / "index.json"
    if not idx_file.exists():
        return _json([])
    try:
        return _json(json.loads(idx_file.read_text("utf-8")))
    except Exception:
        return _json([])


@app.route("/api/buyers/<bid>/documents/<doc_type>/<filename>")
def api_buyer_doc_download(bid, doc_type, filename):
    """Download eines Kunden-Dokuments."""
    from flask import send_from_directory
    doc_dir = _DATA / "data" / "buyer_docs" / bid / doc_type
    filepath = doc_dir / filename
    if not filepath.exists():
        return _json({"error": "Datei nicht gefunden"}, 404)
    return send_from_directory(str(doc_dir), filename)


@app.route("/api/buyers/<bid>/documents/<doc_type>/<filename>", methods=["PUT"])
def api_buyer_doc_update(bid, doc_type, filename):
    """Aktualisiert Metadaten (Typ, Tags) eines Kunden-Dokuments.
    Wenn sich der Typ ändert, wird die Datei in den neuen Type-Ordner verschoben."""
    data = request.json or {}
    new_type = (data.get("type") or doc_type).strip() or doc_type
    new_tags = _parse_tags(",".join(data.get("tags"))) if isinstance(data.get("tags"), list) else _parse_tags(data.get("tags"))

    base = _DATA / "data" / "buyer_docs" / bid
    old_dir = base / doc_type
    old_path = old_dir / filename
    if not old_path.exists():
        return _json({"error": "Datei nicht gefunden"}, 404)

    new_dir = base / new_type
    new_dir.mkdir(parents=True, exist_ok=True)
    new_path = new_dir / filename
    if new_type != doc_type:
        # Disk-Konflikt: durchnummerieren
        counter = 1
        stem, ext = (filename.rsplit(".", 1) + [""])[:2]
        new_filename = filename
        while new_path.exists():
            new_filename = f"{stem}_{counter}.{ext}" if ext else f"{stem}_{counter}"
            new_path = new_dir / new_filename
            counter += 1
        old_path.rename(new_path)
        filename_after = new_filename
    else:
        filename_after = filename

    # Index aktualisieren
    idx_file = base / "index.json"
    if idx_file.exists():
        try:
            idx = json.loads(idx_file.read_text("utf-8"))
            for entry in idx:
                if entry.get("filename") == filename and entry.get("type") == doc_type:
                    entry["type"] = new_type
                    entry["tags"] = new_tags
                    if new_type != doc_type:
                        entry["filename"] = filename_after
                    break
            idx_file.write_text(json.dumps(idx, indent=2, ensure_ascii=False), "utf-8")
        except Exception as e:
            return _json({"error": f"Index-Fehler: {e}"}, 500)

    return _json({"updated": True, "type": new_type, "filename": filename_after, "tags": new_tags})


@app.route("/api/buyers/<bid>/documents/<doc_type>/<filename>", methods=["DELETE"])
def api_buyer_doc_delete(bid, doc_type, filename):
    """Löscht ein Kunden-Dokument."""
    doc_dir = _DATA / "data" / "buyer_docs" / bid / doc_type
    filepath = doc_dir / filename
    if filepath.exists():
        filepath.unlink()
    idx_file = _DATA / "data" / "buyer_docs" / bid / "index.json"
    if idx_file.exists():
        try:
            idx = json.loads(idx_file.read_text("utf-8"))
            idx = [d for d in idx if not (d["filename"] == filename and d["type"] == doc_type)]
            idx_file.write_text(json.dumps(idx, indent=2, ensure_ascii=False), "utf-8")
        except Exception:
            pass
    return _json({"deleted": True})


# ═══════════════════════════════════════════════════════════════
# VORGANGS-ANHÄNGE (frei, vorgangsbezogen — z.B. Grundrisse, Fotos)
# Unabhängig von den stufenbezogenen step-attachments oben
# Speicher: data/transaction_attachments/<tid>/<filename>
# ═══════════════════════════════════════════════════════════════
@app.route("/api/transactions/<tid>/attachments", methods=["POST"])
def api_txn_attach_upload(tid):
    """Lädt einen Anhang zu einem Vorgang hoch (frei, nicht stufengebunden)."""
    txn = txn_mgr.get(tid)
    if not txn:
        return _json({"error": "Vorgang nicht gefunden"}, 404)

    file = request.files.get("file")
    if not file or not file.filename:
        return _json({"error": "Keine Datei"}, 400)

    import hashlib
    from werkzeug.utils import secure_filename

    note = (request.form.get("note") or "").strip()[:200]
    doc_type = (request.form.get("type") or "sonstiges").strip()[:50]
    tags = _parse_tags(request.form.get("tags"))

    base_dir = _DATA / "data" / "transaction_attachments" / tid
    base_dir.mkdir(parents=True, exist_ok=True)

    safe_name = secure_filename(file.filename) or "anhang.pdf"

    idx_file = base_dir / "index.json"
    idx = []
    if idx_file.exists():
        try:
            idx = json.loads(idx_file.read_text("utf-8"))
        except Exception:
            pass

    next_v = _next_version_for(idx, file.filename, doc_type)
    filename = _versioned_filename(safe_name, next_v)
    filepath = base_dir / filename
    counter = 1
    stem, ext = (filename.rsplit(".", 1) + [""])[:2]
    while filepath.exists():
        filename = f"{stem}_{counter}.{ext}" if ext else f"{stem}_{counter}"
        filepath = base_dir / filename
        counter += 1

    file_bytes = file.read()
    filepath.write_bytes(file_bytes)
    sha = hashlib.sha256(file_bytes).hexdigest()[:16]

    entry = {
        "filename": filename,
        "original_name": file.filename,
        "type": doc_type,
        "tags": tags,
        "version": next_v,
        "is_current": True,
        "note": note,
        "size": len(file_bytes),
        "hash": sha,
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        "transaction_id": tid,
    }
    idx.append(entry)
    idx_file.write_text(json.dumps(idx, indent=2, ensure_ascii=False), "utf-8")

    return _json({"uploaded": True, "attachment": entry})


@app.route("/api/transactions/<tid>/attachments")
def api_txn_attach_list(tid):
    """Liste aller Anhänge eines Vorgangs."""
    idx_file = _DATA / "data" / "transaction_attachments" / tid / "index.json"
    if not idx_file.exists():
        return _json([])
    try:
        return _json(json.loads(idx_file.read_text("utf-8")))
    except Exception:
        return _json([])


@app.route("/api/transactions/<tid>/attachments/<filename>")
def api_txn_attach_download(tid, filename):
    """Download eines Vorgangs-Anhangs."""
    from flask import send_from_directory
    base_dir = _DATA / "data" / "transaction_attachments" / tid
    filepath = base_dir / filename
    if not filepath.exists():
        return _json({"error": "Datei nicht gefunden"}, 404)
    return send_from_directory(str(base_dir), filename)


@app.route("/api/transactions/<tid>/attachments/<filename>", methods=["PUT"])
def api_txn_attach_update(tid, filename):
    """Aktualisiert Metadaten eines Vorgangs-Anhangs (type, tags, note).
    Datei selbst wird nicht verschoben (type ist nur Index-Feld bei Vorgangs-Anhängen)."""
    data = request.json or {}
    base_dir = _DATA / "data" / "transaction_attachments" / tid
    idx_file = base_dir / "index.json"
    if not idx_file.exists():
        return _json({"error": "Datei nicht gefunden"}, 404)

    try:
        idx = json.loads(idx_file.read_text("utf-8"))
        found = False
        for entry in idx:
            if entry.get("filename") == filename:
                if "type" in data:
                    entry["type"] = (data.get("type") or "sonstiges").strip()[:50]
                if "tags" in data:
                    raw_tags = ",".join(data["tags"]) if isinstance(data["tags"], list) else data["tags"]
                    entry["tags"] = _parse_tags(raw_tags)
                if "note" in data:
                    entry["note"] = (data.get("note") or "").strip()[:200]
                found = True
                break
        if not found:
            return _json({"error": "Eintrag nicht gefunden"}, 404)
        idx_file.write_text(json.dumps(idx, indent=2, ensure_ascii=False), "utf-8")
        return _json({"updated": True, "entry": entry})
    except Exception as e:
        return _json({"error": str(e)}, 500)


@app.route("/api/transactions/<tid>/attachments/<filename>", methods=["DELETE"])
def api_txn_attach_delete(tid, filename):
    """Löscht einen Vorgangs-Anhang."""
    base_dir = _DATA / "data" / "transaction_attachments" / tid
    filepath = base_dir / filename
    if filepath.exists():
        filepath.unlink()
    idx_file = base_dir / "index.json"
    if idx_file.exists():
        try:
            idx = json.loads(idx_file.read_text("utf-8"))
            idx = [d for d in idx if d.get("filename") != filename]
            idx_file.write_text(json.dumps(idx, indent=2, ensure_ascii=False), "utf-8")
        except Exception:
            pass
    return _json({"deleted": True})


# ═══════════════════════════════════════════════════════════════
# VORGANGS-CHECKLISTE (Dokumenten-Verfolgung)
# Pro Vorgang eine geordnete Liste erforderlicher Dokumente
# Items: {id, position, name, doc_type, required, status, note, linked}
#   linked: null | {kind: 'attachment'|'buyer_doc', filename, doc_type?, buyer_id?}
#   status: 'open' | 'done' | 'skipped'
# ═══════════════════════════════════════════════════════════════
_DEFAULT_CHECKLIST_TEMPLATE = [
    {"position": 10, "name": "Vollmacht Kunde",       "doc_type": "vollmacht",     "required": True},
    {"position": 20, "name": "Datenschutz-Einwilligung", "doc_type": "datenschutz", "required": True},
    {"position": 30, "name": "Bestandsaufnahme / Fotos",  "doc_type": "foto",       "required": False},
    {"position": 40, "name": "Grundriss / Pläne",     "doc_type": "grundriss",     "required": False},
    {"position": 50, "name": "Auftragsbestätigung",   "doc_type": "vertrag",       "required": True},
]


def _gen_checklist_item_id():
    import uuid
    return uuid.uuid4().hex[:10]


def _txn_checklist(txn):
    cl = txn.get("checklist")
    if not isinstance(cl, list):
        return []
    return cl


@app.route("/api/transactions/<tid>/checklist")
def api_txn_checklist_list(tid):
    txn = txn_mgr.get(tid)
    if not txn:
        return _json({"error": "Vorgang nicht gefunden"}, 404)
    cl = sorted(_txn_checklist(txn), key=lambda x: (x.get("position") or 0, x.get("id") or ""))
    return _json({"items": cl})


@app.route("/api/transactions/<tid>/checklist", methods=["POST"])
def api_txn_checklist_add(tid):
    """Fügt ein neues Element zur Checkliste hinzu.
    Body kann ein einzelnes Item ODER {use_default: true} sein um die Default-Vorlage zu laden."""
    txn = txn_mgr.get(tid)
    if not txn:
        return _json({"error": "Vorgang nicht gefunden"}, 404)
    body = request.json or {}

    cl = list(_txn_checklist(txn))
    if body.get("use_default"):
        # Nur einfügen, wenn die Liste leer ist
        if cl:
            return _json({"error": "Checkliste enthält bereits Einträge"}, 409)
        for tpl in _DEFAULT_CHECKLIST_TEMPLATE:
            cl.append({
                "id": _gen_checklist_item_id(),
                "position": tpl["position"],
                "name": tpl["name"],
                "doc_type": tpl.get("doc_type", "sonstiges"),
                "required": bool(tpl.get("required", False)),
                "status": "open",
                "note": "",
                "linked": None,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            })
    else:
        name = (body.get("name") or "").strip()
        if not name:
            return _json({"error": "name ist Pflicht"}, 400)
        max_pos = max([x.get("position") or 0 for x in cl], default=0)
        cl.append({
            "id": _gen_checklist_item_id(),
            "position": int(body.get("position") or (max_pos + 10)),
            "name": name[:120],
            "doc_type": (body.get("doc_type") or "sonstiges")[:50],
            "required": bool(body.get("required", False)),
            "status": (body.get("status") or "open"),
            "note": (body.get("note") or "")[:300],
            "linked": None,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })

    txn_mgr.update(tid, {"checklist": cl})
    return _json({"ok": True, "items": sorted(cl, key=lambda x: (x.get("position") or 0))})


@app.route("/api/transactions/<tid>/checklist/<item_id>", methods=["PUT"])
def api_txn_checklist_update(tid, item_id):
    """Aktualisiert Felder eines Checklist-Items: name, position, doc_type, required, status, note, linked."""
    txn = txn_mgr.get(tid)
    if not txn:
        return _json({"error": "Vorgang nicht gefunden"}, 404)
    body = request.json or {}
    cl = list(_txn_checklist(txn))
    found = None
    for it in cl:
        if it.get("id") == item_id:
            found = it
            break
    if not found:
        return _json({"error": "Item nicht gefunden"}, 404)

    if "name" in body:
        found["name"] = (body.get("name") or "").strip()[:120]
    if "position" in body:
        try:
            found["position"] = int(body["position"])
        except Exception:
            pass
    if "doc_type" in body:
        found["doc_type"] = (body.get("doc_type") or "sonstiges")[:50]
    if "required" in body:
        found["required"] = bool(body["required"])
    if "status" in body:
        s = (body.get("status") or "open")
        if s not in ("open", "done", "skipped"):
            return _json({"error": "Ungültiger Status"}, 400)
        found["status"] = s
        found["status_changed_at"] = datetime.now().isoformat(timespec="seconds")
    if "note" in body:
        found["note"] = (body.get("note") or "")[:300]
    if "linked" in body:
        # null oder {kind, filename, ...}
        link = body.get("linked")
        if link is None:
            found["linked"] = None
            # Wenn aktuell auf 'done' und Verknüpfung aufgehoben → zurück auf open
            if found.get("status") == "done":
                found["status"] = "open"
        elif isinstance(link, dict) and link.get("kind") in ("attachment", "buyer_doc"):
            found["linked"] = {
                "kind": link.get("kind"),
                "filename": link.get("filename"),
                "doc_type": link.get("doc_type"),
                "buyer_id": link.get("buyer_id"),
                "label": link.get("label"),
                "linked_at": datetime.now().isoformat(timespec="seconds"),
            }
            # Auto-Status auf 'done' wenn vorher 'open'
            if found.get("status") == "open":
                found["status"] = "done"
                found["status_changed_at"] = datetime.now().isoformat(timespec="seconds")
        else:
            return _json({"error": "Ungültige linked-Struktur"}, 400)

    txn_mgr.update(tid, {"checklist": cl})
    return _json({"ok": True, "item": found})


@app.route("/api/transactions/<tid>/checklist/<item_id>", methods=["DELETE"])
def api_txn_checklist_delete(tid, item_id):
    txn = txn_mgr.get(tid)
    if not txn:
        return _json({"error": "Vorgang nicht gefunden"}, 404)
    cl = list(_txn_checklist(txn))
    cl_after = [it for it in cl if it.get("id") != item_id]
    if len(cl_after) == len(cl):
        return _json({"error": "Item nicht gefunden"}, 404)
    txn_mgr.update(tid, {"checklist": cl_after})
    return _json({"ok": True})


# ═══════════════════════════════════════════════════════════════
# GLOBALER DOKUMENTEN-POOL (DMS Stufe B)
# Aggregiert Kunden-Docs, Lieferanten-Docs und Vorgangs-Anhänge
# ═══════════════════════════════════════════════════════════════
@app.route("/api/documents/all")
def api_documents_all():
    """Liefert alle Dokumente aus buyer_docs/, supplier_docs/, transaction_attachments/
    als einheitliche Liste. Filter via Query-Params:
      - q       Volltext über Dateiname, Originalname, Notiz, Kontext-Name
      - type    Filter auf doc_type
      - context Filter auf buyer | supplier | transaction
    """
    q = (request.args.get("q") or "").strip().lower()
    f_type = (request.args.get("type") or "").strip().lower()
    f_ctx = (request.args.get("context") or "").strip().lower()
    f_tag = (request.args.get("tag") or "").strip().lower()
    include_history = (request.args.get("include_history") or "").strip() in ("1", "true", "yes")

    base = _DATA / "data"
    out = []

    def _read_idx(path):
        if not path.exists():
            return []
        try:
            d = json.loads(path.read_text("utf-8"))
            return d if isinstance(d, list) else []
        except Exception:
            return []

    # Buyer-Docs
    if f_ctx in ("", "buyer"):
        buyers_dir = base / "buyer_docs"
        if buyers_dir.exists():
            buyer_lookup = {b.get("id"): b.get("name", "") for b in _load_buyers()}
            for sub in buyers_dir.iterdir():
                if not sub.is_dir():
                    continue
                bid = sub.name
                bname = buyer_lookup.get(bid, "")
                for entry in _read_idx(sub / "index.json"):
                    if not include_history and entry.get("is_current", True) is False:
                        continue
                    out.append({
                        "context_type": "buyer",
                        "context_id": bid,
                        "context_name": bname or entry.get("buyer_name", "") or bid,
                        "type": entry.get("type") or "sonstiges",
                        "tags": entry.get("tags") or [],
                        "version": entry.get("version") or 1,
                        "is_current": entry.get("is_current", True),
                        "filename": entry.get("filename"),
                        "original_name": entry.get("original_name") or entry.get("filename"),
                        "size": entry.get("size") or 0,
                        "hash": entry.get("hash") or "",
                        "uploaded_at": entry.get("uploaded_at") or "",
                        "note": "",
                        "download_url": f"/api/buyers/{bid}/documents/{entry.get('type')}/{entry.get('filename')}",
                    })

    # Supplier-Docs
    if f_ctx in ("", "supplier"):
        sup_dir = base / "supplier_docs"
        if sup_dir.exists():
            try:
                sup_list = supplier_mgr.list_all()
            except Exception:
                sup_list = []
            sup_lookup = {s.get("id"): s.get("name", "") for s in sup_list}
            for sub in sup_dir.iterdir():
                if not sub.is_dir():
                    continue
                sid = sub.name
                sname = sup_lookup.get(sid, "")
                for entry in _read_idx(sub / "index.json"):
                    if not include_history and entry.get("is_current", True) is False:
                        continue
                    out.append({
                        "context_type": "supplier",
                        "context_id": sid,
                        "context_name": sname or entry.get("supplier_name", "") or sid,
                        "type": entry.get("type") or "sonstiges",
                        "tags": entry.get("tags") or [],
                        "version": entry.get("version") or 1,
                        "is_current": entry.get("is_current", True),
                        "filename": entry.get("filename"),
                        "original_name": entry.get("original_name") or entry.get("filename"),
                        "size": entry.get("size") or 0,
                        "hash": entry.get("hash") or "",
                        "uploaded_at": entry.get("uploaded_at") or "",
                        "note": "",
                        "download_url": f"/api/suppliers/{sid}/documents/{entry.get('type')}/{entry.get('filename')}",
                    })

    # Transaction-Attachments
    if f_ctx in ("", "transaction"):
        try:
            tx_list = txn_mgr.list_all()
            if isinstance(tx_list, dict):
                tx_list = list(tx_list.values())
        except Exception:
            tx_list = []
        tx_lookup = {t.get("id"): (t.get("subject") or t.get("buyer_name") or "") for t in (tx_list or [])}

        # (a) Vorgangs-Anhänge aus data/transaction_attachments/<tid>/index.json
        tx_dir = base / "transaction_attachments"
        if tx_dir.exists():
            for sub in tx_dir.iterdir():
                if not sub.is_dir():
                    continue
                tid = sub.name
                tname = tx_lookup.get(tid, "")
                for entry in _read_idx(sub / "index.json"):
                    if not include_history and entry.get("is_current", True) is False:
                        continue
                    out.append({
                        "context_type": "transaction",
                        "context_id": tid,
                        "context_name": tname or tid,
                        "type": entry.get("type") or "sonstiges",
                        "tags": entry.get("tags") or [],
                        "version": entry.get("version") or 1,
                        "is_current": entry.get("is_current", True),
                        "filename": entry.get("filename"),
                        "original_name": entry.get("original_name") or entry.get("filename"),
                        "size": entry.get("size") or 0,
                        "hash": entry.get("hash") or "",
                        "uploaded_at": entry.get("uploaded_at") or "",
                        "note": entry.get("note") or "",
                        "download_url": f"/api/transactions/{tid}/attachments/{entry.get('filename')}",
                    })

        # (b) Step-Anhänge (im Vorgangs-Schritt hochgeladen; Metadaten im Step,
        #     Dateien unter data/attachments/<tid>/<step_key>/). Wurden bislang NICHT
        #     in der Dokumentenliste geführt → Eingangsrechnungen o.ä. waren nicht auffindbar.
        _STEP_DOC_TYPE = {
            "supplier_quote": "angebot-lieferant",
            "purchase_order": "bestellung",
            "supplier_invoice": "eingangsrechnung",
            "customer_quote": "angebot",
            "order_intake": "auftragseingang",
            "order_confirmation": "auftragsbestaetigung",
            "delivery_note": "lieferschein",
            "invoice": "rechnung",
            "dunning": "mahnung",
        }
        for t in (tx_list or []):
            tid = t.get("id")
            if not tid:
                continue
            tname = tx_lookup.get(tid, "") or tid
            for step_key, step in (t.get("steps") or {}).items():
                for entry in (step.get("attachments") or []):
                    fn = entry.get("filename")
                    if not fn:
                        continue
                    out.append({
                        "context_type": "transaction",
                        "context_id": tid,
                        "context_name": tname,
                        "type": entry.get("type") or _STEP_DOC_TYPE.get(step_key, "vorgangsdokument"),
                        "tags": entry.get("tags") or [],
                        "version": entry.get("version") or 1,
                        "is_current": True,
                        "filename": fn,
                        "original_name": entry.get("original_name") or fn,
                        "size": entry.get("size") or 0,
                        "hash": entry.get("hash") or "",
                        "uploaded_at": entry.get("uploaded_at") or "",
                        "note": entry.get("note") or STEP_LABELS.get(step_key, ""),
                        "download_url": f"/api/transactions/{tid}/steps/{step_key}/attachments/{fn}",
                    })

    # Filter Typ
    if f_type:
        out = [d for d in out if (d.get("type") or "").lower() == f_type]

    # Filter Tag
    if f_tag:
        out = [d for d in out if f_tag in [t.lower() for t in (d.get("tags") or [])]]

    # Volltextsuche (umfasst auch Tags)
    if q:
        def hay(d):
            return " ".join([
                str(d.get("original_name") or ""),
                str(d.get("filename") or ""),
                str(d.get("note") or ""),
                str(d.get("context_name") or ""),
                str(d.get("type") or ""),
                " ".join(d.get("tags") or []),
            ]).lower()
        out = [d for d in out if q in hay(d)]

    # Sortierung: neueste zuerst
    out.sort(key=lambda d: (d.get("uploaded_at") or ""), reverse=True)

    return _json({"count": len(out), "documents": out})


@app.route("/api/documents/tags")
def api_documents_tags():
    """Liefert alle existierenden Tags über alle Quellen — für Filter-Dropdown."""
    base = _DATA / "data"
    tags = set()
    for sub_root in ("buyer_docs", "supplier_docs", "transaction_attachments"):
        root = base / sub_root
        if not root.exists():
            continue
        for sub in root.iterdir():
            idx = sub / "index.json"
            if not idx.exists():
                continue
            try:
                d = json.loads(idx.read_text("utf-8"))
                for entry in (d if isinstance(d, list) else []):
                    for t in (entry.get("tags") or []):
                        if t:
                            tags.add(t)
            except Exception:
                pass
    return _json({"tags": sorted(tags)})


@app.route("/api/documents/types")
def api_documents_types():
    """Liefert eine Liste aller existierenden doc_types über alle Quellen — für Filter-Dropdown."""
    base = _DATA / "data"
    types = set()
    for sub_root in ("buyer_docs", "supplier_docs", "transaction_attachments"):
        root = base / sub_root
        if not root.exists():
            continue
        for sub in root.iterdir():
            idx = sub / "index.json"
            if not idx.exists():
                continue
            try:
                d = json.loads(idx.read_text("utf-8"))
                for entry in (d if isinstance(d, list) else []):
                    t = entry.get("type")
                    if t:
                        types.add(t)
            except Exception:
                pass
    # Typen der Vorgangs-Step-Anhänge (data/attachments/<tid>/<step_key>/)
    try:
        _tx = txn_mgr.list_all()
        if isinstance(_tx, dict):
            _tx = list(_tx.values())
        _step_type = {
            "supplier_quote": "angebot-lieferant", "purchase_order": "bestellung",
            "supplier_invoice": "eingangsrechnung", "customer_quote": "angebot",
            "order_intake": "auftragseingang", "order_confirmation": "auftragsbestaetigung",
            "delivery_note": "lieferschein", "invoice": "rechnung", "dunning": "mahnung",
        }
        for t in (_tx or []):
            for step_key, step in (t.get("steps") or {}).items():
                if step.get("attachments"):
                    types.add(_step_type.get(step_key, "vorgangsdokument"))
    except Exception:
        pass
    return _json({"types": sorted(types)})


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
        # Steht die Rechnung wirklich im Rechnungsregister? Die Anzeige hing
        # vorher an document_id — die setzt aber schon die reine PDF-Erzeugung,
        # sodass "Im System" auch bei unregistrierter Rechnung gruen war.
        _inv_step = txn.get("steps", {}).get("invoice", {})
        _inv_obj = _txn_invoice_registriert(_inv_step.get("reference", ""))
        _inv_step["_registriert"] = _inv_obj is not None
        _inv_step["_register_status"] = getattr(_inv_obj, "status", "") if _inv_obj else ""
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


# ── API: Abschlagsrechnungen (Anzahlungen / Teilrechnungen pro Vorgang) ──

@app.route("/api/transactions/<tid>/payment-invoices", methods=["GET"])
def api_txn_payment_invoices_list(tid):
    return _json({"items": txn_mgr.list_payment_invoices(tid)})


@app.route("/api/transactions/<tid>/payment-invoices", methods=["POST"])
def api_txn_payment_invoices_create(tid):
    data = request.get_json(force=True) or {}
    try:
        p = txn_mgr.add_payment_invoice(tid, data, user=data.get("_user", "web-user"))
        return _json({"saved": True, "payment_invoice": p})
    except ValueError as e:
        return _json({"error": str(e)}, 400)


@app.route("/api/transactions/<tid>/payment-invoices/<pid>", methods=["PUT"])
def api_txn_payment_invoices_update(tid, pid):
    data = request.get_json(force=True) or {}
    try:
        p = txn_mgr.update_payment_invoice(tid, pid, data, user=data.get("_user", "web-user"))
        return _json({"saved": True, "payment_invoice": p})
    except ValueError as e:
        return _json({"error": str(e)}, 400)


@app.route("/api/transactions/<tid>/payment-invoices/<pid>", methods=["DELETE"])
def api_txn_payment_invoices_delete(tid, pid):
    try:
        ok = txn_mgr.delete_payment_invoice(tid, pid)
        return _json({"deleted": ok})
    except ValueError as e:
        return _json({"error": str(e)}, 400)


@app.route("/api/transactions/<tid>/payment-invoices/<pid>/mark-sent", methods=["POST"])
def api_txn_payment_invoices_mark_sent(tid, pid):
    """Abschlag als AUSSERHALB versendet vermerken (Post, fremdes Mailprogramm).

    Der Weg ueber /send verschickt wirklich und protokolliert selbst. Dieser
    hier ist der Nachtrag von Hand — und verlangt deshalb, dass dabeisteht, wie
    und wohin. Ohne Angabe war "versendet" eine Behauptung ohne Beleg
    (07.09.2026).
    """
    data = request.get_json(silent=True) or {}
    empfaenger = (data.get("to") or "").strip()
    hinweis = (data.get("note") or "").strip()
    if not empfaenger and not hinweis:
        return _json({"error": "Bitte angeben, wie der Abschlag hinausging "
                               "(Empfaenger unter 'to' oder ein Hinweis unter 'note'). "
                               "Zum wirklichen Versand den Weg /send benutzen."}, 400)
    try:
        p = txn_mgr.mark_payment_invoice_sent(tid, pid,
                                               user=data.get("_user", "web-user"),
                                               document_id=data.get("document_id", ""),
                                               to=empfaenger, channel="manuell",
                                               note=hinweis)
        return _json({"saved": True, "payment_invoice": p})
    except ValueError as e:
        return _json({"error": str(e)}, 400)


@app.route("/api/transactions/<tid>/payment-invoices/<pid>/generate-pdf", methods=["POST"])
def api_txn_payment_invoice_generate_pdf(tid, pid):
    """Erzeugt das PDF einer Abschlagsrechnung und speichert document_id."""
    txn = txn_mgr.get(tid)
    if not txn:
        return _json({"error": "Vorgang nicht gefunden"}, 404)
    p = next((x for x in txn.get("payment_invoices", []) if x.get("id") == pid), None)
    if not p:
        return _json({"error": "Abschlag nicht gefunden"}, 404)

    # Empfänger (Kunde)
    buyers = _load_buyers()
    buyer = next((b for b in buyers if b.get("id") == txn.get("buyer_id")), None)
    recipient = buyer if buyer else {"name": txn.get("buyer_name", "")}

    # Pauschal-Position auf Basis von amount_net + tax_rate
    pos = [{
        "pos_nr": 1,
        "description": p.get("description") or p.get("title") or "Abschlagsrechnung",
        "quantity": 1, "unit": "pauschal",
        "unit_price": float(p.get("amount_net", 0)),
        "net_amount": float(p.get("amount_net", 0)),
        "tax_rate": float(p.get("tax_rate", 19)),
        "discount_percent": 0,
    }]
    fake_step = {"positions": pos, "intro_text": "", "closing_text": "",
                  "doc_discount_amount": 0, "doc_surcharge_amount": 0}
    templates = _load_mandant_settings().get("doc_templates", {})
    intro = templates.get("payment_invoice_intro", "")
    closing = templates.get("payment_invoice_closing", "")

    try:
        doc_gen._prepaid_invoices = None
        doc_entry = doc_gen.generate(
            doc_type="payment_invoice",
            recipient=recipient,
            positions=pos,
            step=fake_step,
            reference=p.get("reference", ""),
            subject=p.get("title", "Abschlagsrechnung"),
            doc_date=p.get("date", ""),
            due_date=p.get("date", ""),
            transaction_id=tid,
            step_key="payment_invoice",
            intro_text=intro,
            closing_text=closing,
        )
        # document_id ans Payment-Invoice hängen
        txn_mgr.update_payment_invoice(tid, pid, {"document_id": doc_entry["id"]})
        return _json({"generated": True, "document": doc_entry,
                       "download_url": f"/api/documents/{doc_entry['filename']}"})
    except Exception as e:
        import traceback; traceback.print_exc()
        return _json({"error": f"PDF-Erzeugung fehlgeschlagen: {e}"}, 500)


def _abschlag_registrieren(tid, txn, p):
    """Traegt eine Abschlagsrechnung als echte Rechnung ins Register ein.

    Bis zum 07.09.2026 lebte ein Abschlag NUR in transactions.json: keine
    XRechnung, kein Validierungsbericht, kein GoBD-Archiv, kein Eintrag in den
    offenen Posten oder der USt-Auswertung — er liess sich also gar nicht
    pruefen . Dabei ist die Anzahlungsrechnung nach § 14 UStG eine
    Rechnung mit allen Pflichtangaben, und die Steuer entsteht mit der
    Vereinnahmung der Anzahlung (§ 13 Abs. 1 Nr. 1 a Satz 4 UStG).

    Typschluessel 326 = Teilrechnung (BT-3), der fuer Abschlaege vorgesehene
    Code aus der XRechnung-Auswahl.

    Rueckgabe: (inv, xml_bytes, report). Ist der Abschlag schon registriert,
    kommt die vorhandene Rechnung zurueck.
    """
    nummer = (p.get("reference") or "").strip()
    if not nummer:
        raise ValueError("Abschlag ohne Nummer — kann nicht registriert werden.")

    vorhanden = next((i for i in invoices.values()
                      if (i.invoice_number or "").strip() == nummer), None)
    if vorhanden is not None:
        return vorhanden, generate_and_serialize(vorhanden), validate_invoice(vorhanden)

    netto = Decimal(str(p.get("amount_net") or 0))
    if netto <= 0:
        raise ValueError("Abschlag ohne Betrag — bitte zuerst den Nettobetrag erfassen.")

    ms = _load_mandant_settings()
    buyers = _load_buyers()
    buyer_data = next((b for b in buyers if b.get("id") == txn.get("buyer_id")), None) or {
        "name": txn.get("buyer_name", ""), "street": "", "post_code": "", "city": "",
        "email": "", "reference": "",
    }

    inv_date = date.fromisoformat(str(p.get("date") or date.today().isoformat())[:10])
    zahlungsziel = "Zahlbar innerhalb von 14 Tagen"
    faellig = inv_date + timedelta(days=14)

    inv = Invoice(
        invoice_number=nummer,
        invoice_date=inv_date,
        invoice_type_code="326",          # Teilrechnung / Abschlag
        currency_code="EUR",
        buyer_reference=buyer_data.get("reference", "") or txn.get("buyer_name", ""),
        note=(f"Abschlagsrechnung zum Vorgang {txn.get('number') or txn.get('id')}: "
              f"{txn.get('subject', '')}").strip(),
        seller=Seller(
            name=ms.get("name", ""),
            address=Address(street=ms.get("street", ""), house_number=ms.get("house_number", ""),
                            city=ms.get("city", ""), post_code=ms.get("post_code", "")),
            electronic_address=ms.get("email", ""), electronic_address_scheme="EM",
            contact=Contact(ms.get("contact_name", ""), ms.get("contact_phone", ""), ms.get("email", "")),
            vat_id=ms.get("vat_id", ""), tax_registration_id=ms.get("tax_registration_id", ""),
        ),
        buyer=Buyer(
            name=buyer_data.get("name", ""),
            address=Address(street=buyer_data.get("street", ""),
                            house_number=buyer_data.get("house_number", ""),
                            city=buyer_data.get("city", ""),
                            post_code=buyer_data.get("post_code", "")),
            electronic_address=buyer_data.get("email", "") or "buyer@example.com",
            electronic_address_scheme="EM",
            buyer_reference=buyer_data.get("reference", ""),
            vat_id=buyer_data.get("vat_id", ""),
        ),
        payment=PaymentInfo(means_code="58", iban=ms.get("iban", ""),
                            payment_terms=zahlungsziel, due_date=faellig),
    )
    inv.lines.append(InvoiceLine(
        line_id="1", quantity=Decimal("1"),
        item_name=(p.get("description") or p.get("title") or "Abschlagsrechnung")[:200],
        unit_price=netto, line_net_amount=netto,
        tax_rate=Decimal(str(p.get("tax_rate") or 19)),
    ))

    report = validate_invoice(inv)
    xml_bytes = generate_and_serialize(inv)
    inv._direction = "AUSGANG"
    try:
        inv._transaction_id = tid
    except Exception:
        pass
    invoices[inv._id] = inv
    inbox.duplicates.register(inv, inv._id)

    # Das schon erzeugte Abschlags-PDF ins Archiv legen, statt ein zweites zu bauen
    _pdf = None
    try:
        _datei = _DATA / "data" / "documents" / f"{nummer.replace('/', '_')}.pdf"
        if _datei.exists():
            _pdf = _datei.read_bytes()
    except Exception:
        _pdf = None
    _archive_ausgang(inv, xml_bytes, report, pdf_bytes=_pdf)
    auto_save()

    txn_mgr.update_payment_invoice(tid, p["id"],
                                   {"invoice_id": inv._id, "invoice_number": nummer})
    return inv, xml_bytes, report


@app.route("/api/transactions/<tid>/payment-invoices/<pid>/register", methods=["POST"])
def api_txn_payment_invoice_register(tid, pid):
    """Abschlag als pruefbare Rechnung anlegen — ohne zu versenden."""
    txn = txn_mgr.get(tid)
    if not txn:
        return _json({"error": "Vorgang nicht gefunden"}, 404)
    p = next((x for x in txn.get("payment_invoices", []) if x.get("id") == pid), None)
    if not p:
        return _json({"error": "Abschlag nicht gefunden"}, 404)
    try:
        inv, _xml, report = _abschlag_registrieren(tid, txn, p)
    except ValueError as e:
        return _json({"error": str(e)}, 400)
    except Exception as e:
        import traceback; traceback.print_exc()
        return _json({"error": f"Registrierung fehlgeschlagen: {e}"}, 500)
    return _json({"registered": True, "invoice_id": inv._id,
                  "invoice_number": inv.invoice_number,
                  "valid": report.is_valid, "errors": report.error_count,
                  "issues": [i.message for i in report.issues
                             if i.severity.value == "ERROR"]})


@app.route("/api/transactions/<tid>/payment-invoices/<pid>/send", methods=["POST"])
def api_txn_payment_invoice_send(tid, pid):
    """Verschickt die Abschlagsrechnung wirklich — PDF + XRechnung, protokolliert.

    Vorher gab es nur "als versendet": ein Flag ohne Empfaenger, ohne Nachweis,
    ohne Eintrag im Versandprotokoll. Dieser Weg geht denselben Gang wie eine
    normale Rechnung: registrieren, validieren (Fehler blockieren den Versand),
    PDF + XML anhaengen, Mail protokollieren mit Message-ID.
    """
    txn = txn_mgr.get(tid)
    if not txn:
        return _json({"error": "Vorgang nicht gefunden"}, 404)
    p = next((x for x in txn.get("payment_invoices", []) if x.get("id") == pid), None)
    if not p:
        return _json({"error": "Abschlag nicht gefunden"}, 404)
    if p.get("status") == "PAID":
        return _json({"error": "Abschlag ist bezahlt — kein erneuter Versand."}, 400)

    data = request.get_json(silent=True) or {}
    buyers = _load_buyers()
    buyer = next((b for b in buyers if b.get("id") == txn.get("buyer_id")), None) or {}
    recipient = (data.get("recipient") or buyer.get("email") or "").strip()
    if not recipient:
        return _json({"error": "Empfaenger-E-Mail (recipient) erforderlich — "
                               "beim Kunden ist keine hinterlegt."}, 400)

    try:
        inv, xml_bytes, report = _abschlag_registrieren(tid, txn, p)
    except ValueError as e:
        return _json({"error": str(e)}, 400)
    except Exception as e:
        import traceback; traceback.print_exc()
        return _json({"error": f"Registrierung fehlgeschlagen: {e}"}, 500)

    if not report.is_valid:
        return _json({"error": f"Abschlagsrechnung hat {report.error_count} "
                               f"Validierungsfehler. Versand blockiert.",
                      "errors": [i.message for i in report.issues
                                 if i.severity.value == "ERROR"]}, 400)

    # PDF: das im Vorgang erzeugte Abschlags-PDF hat Vorrang
    pdf_anhang = []
    try:
        _datei = _DATA / "data" / "documents" / f"{inv.invoice_number.replace('/', '_')}.pdf"
        if _datei.exists():
            pdf_anhang = [(f"{inv.invoice_number}.pdf", _datei.read_bytes())]
        else:
            _name, _bytes = _build_invoice_pdf(inv)
            pdf_anhang = [(_name, _bytes)]
    except Exception as e:
        import traceback; traceback.print_exc()
        return _json({"error": "Versand abgebrochen: Das PDF konnte nicht erzeugt "
                               "werden. Der Empfaenger soll IMMER ein PDF erhalten.",
                      "details": str(e)}, 500)

    send_log = email_sender.send_invoice(
        invoice=inv, recipient=recipient,
        subject=data.get("subject", ""), body_text=data.get("body", ""),
        xml_bytes=xml_bytes, additional_attachments=pdf_anhang,
    )
    if not send_log.success:
        return _json({"error": f"Mail-Versand fehlgeschlagen: {send_log.error}"}, 502)

    inv.add_audit("EMAIL_VERSENDET",
                  comment=f"Abschlagsrechnung an {recipient} (PDF + XML), "
                          f"Message-ID: {send_log.message_id}")
    if inv.status in ("NEU", "IN_PRUEFUNG", "IN_FREIGABE"):
        _alt = inv.status
        inv.status = "FREIGEGEBEN"
        inv.add_audit("FREIGEGEBEN", user="system",
                      comment="Automatisch freigegeben durch Versand", old_value=_alt)
    _log_sent_mail(recipient, send_log.subject or "", data.get("body", ""),
                   doc_type="payment_invoice", reference=inv.invoice_number,
                   attachments=[send_log.attachment_filename] + [a[0] for a in pdf_anhang],
                   from_addr=getattr(getattr(email_sender, "config", None),
                                     "smtp_from_address", ""),
                   message_id=send_log.message_id)
    auto_save()

    pi = txn_mgr.mark_payment_invoice_sent(
        tid, pid, user=data.get("_user", "web-user"),
        document_id=p.get("document_id", ""), to=recipient, channel="email",
        message_id=send_log.message_id, invoice_id=inv._id,
        invoice_number=inv.invoice_number)
    return _json({"sent": True, "to": recipient, "message_id": send_log.message_id,
                  "invoice_id": inv._id, "invoice_number": inv.invoice_number,
                  "payment_invoice": pi})


@app.route("/api/transactions/<tid>/payment-invoices/<pid>/mark-paid", methods=["POST"])
def api_txn_payment_invoices_mark_paid(tid, pid):
    """Abschlag als bezahlt fuehren — mit Bankbuchung als Nachweis.

    Frueher setzte das nur ein Datum: AR-2026-0002 stand auf "bezahlt am
    03.09." ohne jede Zahlung dahinter (07.09.2026). Jetzt wird, wenn
    keine `booking_id` mitkommt, im Bankbestand nach einer noch nicht
    zugeordneten Buchung mit GENAU diesem Betrag gesucht (Datum +/- 45 Tage).
    Nur ein EINDEUTIGER Treffer wird verknuepft — bei mehreren bleibt die
    Zuordnung offen und die Antwort sagt das ausdruecklich. Lieber kein
    Nachweis als der falsche.
    """
    data = request.get_json(silent=True) or {}
    txn = txn_mgr.get(tid)
    p_alt = next((x for x in (txn or {}).get("payment_invoices", []) if x.get("id") == pid), None)
    if not p_alt:
        return _json({"error": "Abschlag nicht gefunden"}, 404)

    booking_id = (data.get("booking_id") or "").strip()
    betrag = data.get("paid_amount")
    if betrag is None:
        betrag = p_alt.get("amount_gross")
    kandidaten = []
    if not booking_id:
        booking_id, kandidaten = _abschlag_buchung_suchen(p_alt, data.get("paid_at", ""))

    try:
        p = txn_mgr.mark_payment_invoice_paid(tid, pid,
                                                user=data.get("_user", "web-user"),
                                                paid_at=data.get("paid_at", ""),
                                                paid_amount=betrag,
                                                booking_id=booking_id)
    except ValueError as e:
        return _json({"error": str(e)}, 400)

    # Die registrierte Rechnung mitfuehren — sonst steht der Abschlag ewig
    # als offener Posten, obwohl er bezahlt ist.
    rechnung = None
    inv = invoices.get(p.get("invoice_id") or "")
    if inv is not None:
        setattr(inv, "_paid_at", p.get("paid_at"))
        setattr(inv, "_paid_amount", float(betrag or 0))
        if booking_id:
            setattr(inv, "_paid_via_booking", booking_id)
        try:
            inv.status = "BEZAHLT"
        except Exception:
            pass
        inv.add_audit("BEZAHLT", user=data.get("_user", "web-user"),
                      comment=(f"Abschlag bezahlt am {p.get('paid_at')}"
                               + (f", Bankbuchung {booking_id}" if booking_id
                                  else " — ohne Bankbuchung als Nachweis")))
        auto_save()
        rechnung = inv.invoice_number

    # Gegenrichtung: die Bankbuchung auf die Rechnung zeigen lassen
    if booking_id and inv is not None:
        _bank_buchung_verknuepfen(booking_id, inv._id)

    return _json({"saved": True, "payment_invoice": p, "invoice_number": rechnung,
                  "booking_id": booking_id,
                  "ohne_nachweis": not booking_id,
                  "kandidaten": kandidaten})


def _abschlag_buchung_suchen(p, paid_at=""):
    """Sucht die Bankbuchung zu einem Abschlag: gleicher Betrag, zeitnah, frei.

    Rueckgabe (buchungs_id, kandidaten). Die Id kommt nur bei GENAU einem
    Treffer; sonst ist sie leer und die Kandidaten gehen zur Anzeige zurueck.
    """
    try:
        brutto = round(float(p.get("amount_gross") or 0), 2)
    except Exception:
        return "", []
    if brutto <= 0:
        return "", []
    try:
        roh = json.loads((_DATA / "data" / "bank_transactions.json").read_text(encoding="utf-8"))
    except Exception:
        return "", []
    liste = roh if isinstance(roh, list) else list(roh.values())
    bezug = (paid_at or p.get("paid_at") or p.get("date") or "")[:10]
    treffer = []
    for b in liste:
        if not isinstance(b, dict) or b.get("matched_invoice_id"):
            continue
        try:
            if round(float(b.get("amount") or 0), 2) != brutto:
                continue
        except Exception:
            continue
        datum = (b.get("booking_date") or b.get("value_date") or "")[:10]
        if bezug and datum:
            try:
                tage = abs((date.fromisoformat(datum) - date.fromisoformat(bezug)).days)
                if tage > 45:
                    continue
            except Exception:
                pass
        treffer.append({"id": b.get("id"), "datum": datum,
                        "betrag": float(b.get("amount") or 0),
                        "zweck": (b.get("purpose") or "")[:120]})
    return (treffer[0]["id"] if len(treffer) == 1 else ""), treffer


def _bank_buchung_verknuepfen(booking_id, invoice_id):
    """Traegt die Rechnung an der Bankbuchung ein (Gegenrichtung der Zuordnung)."""
    pfad = _DATA / "data" / "bank_transactions.json"
    try:
        roh = json.loads(pfad.read_text(encoding="utf-8"))
    except Exception:
        return False
    liste = roh if isinstance(roh, list) else list(roh.values())
    geaendert = False
    for b in liste:
        if isinstance(b, dict) and b.get("id") == booking_id and not b.get("matched_invoice_id"):
            b["matched_invoice_id"] = invoice_id
            b["match_status"] = "matched"
            b["matched_at"] = datetime.now().isoformat(timespec="seconds")
            b["match_confidence"] = "manuell-abschlag"
            geaendert = True
    if geaendert:
        try:
            pfad.write_text(json.dumps(roh, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            return False
    return geaendert


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

    # Dienstvertrag: Auswahl beim Versenden (19.08.2026).
    # Vorher entschied das allein die Artikelnummer — ohne "Vertrag separat"-Produkt
    # wurde er an JEDES Kundenangebot gemerged, ohne Weg das abzuwaehlen.
    # Jetzt gewinnt die ausdrueckliche Auswahl aus dem Versand-Dialog; sie wird am
    # Schritt festgehalten, damit jedes spaeter erzeugte PDF gleich aussieht.
    if "vertrag_anhaengen" in data:
        _kv = not bool(data.get("vertrag_anhaengen"))
        if step.get("kein_vertrag") != _kv:
            txn_mgr.update_step(tid, step_key, {"kein_vertrag": _kv})
        step["kein_vertrag"] = _kv

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
        if step_key == "invoice":
            # Rechnungen ziehen ihre Nummer IMMER aus dem zentralen Rechnungskreis
            # (RE-YYYY-XXXXX wie Direkt-/Energieausweisrechnungen, GoBD-einheitlich).
            # Vorher zog dieser PDF-Pfad aus number_sequences (RE-2026-0008 ff.) —
            # die spätere XRechnung-Registrierung übernahm dann diese 4-stellige
            # Nummer oder vergab eine andere → Nummern-Chaos (20.07.2026).
            ref = _next_invoice_number(advance=True)
        else:
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

    # Bei Schlussrechnung: Abschlagsrechnungen mitliefern → werden im PDF aufgelistet & abgezogen
    if step_key == "invoice":
        doc_gen._prepaid_invoices = [p for p in txn.get("payment_invoices", [])
                                       if p.get("status") in ("SENT", "PAID")]
    else:
        doc_gen._prepaid_invoices = None

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
            delivery_date=step.get("date") or data.get("date", ""),
            transaction_id=tid,
            step_key=step_key,
            dunning_level=dunning_level,
            original_invoice_ref=inv_ref,
            original_invoice_amount=inv_amount,
            dunning_fee=dunning_fee,
            intro_text=intro,
            closing_text=closing,
        )
        doc_gen._prepaid_invoices = None  # nach Render aufräumen

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


@app.route("/api/transactions/<tid>/vertrag")
def api_txn_vertrag(tid):
    """Liefert den auto-gefüllten Dienstvertrag als druckfertige HTML-Seite
    (Beratungsempfänger + Vergütung aus dem Vorgang). Wird im Browser geöffnet,
    gedruckt und unterschrieben."""
    txn = txn_mgr.get(tid)
    if not txn:
        return "Vorgang nicht gefunden", 404
    tpl_path = _DATA / "data" / "vertrag" / "dienstvertrag_template.html"
    if not tpl_path.exists():
        return "Vertragsvorlage fehlt", 500
    page = tpl_path.read_text("utf-8")

    def esc(s):
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))

    # Beratungsempfänger aus dem Käufer-Stammdatensatz
    buyer = next((b for b in _load_buyers() if b.get("id") == txn.get("buyer_id")), None) or {}
    name = buyer.get("name") or buyer.get("contact_name") or txn.get("buyer_name", "") or ""
    strasse = " ".join(x for x in [buyer.get("street", ""), buyer.get("house_number", "")] if x).strip()

    # Vergütung = Netto-Summe des Kundenangebots
    cq = (txn.get("steps") or {}).get("customer_quote", {})
    net = sum(float(p.get("net_amount") or 0) for p in (cq.get("positions") or []))
    verguetung = f"{net:,.2f}".replace(",", "#").replace(".", ",").replace("#", ".") if net else ""

    repl = {
        "{{empf_name}}":    esc(name),
        "{{empf_vorname}}": "",
        "{{empf_strasse}}": esc(strasse),
        "{{empf_plz}}":     esc(buyer.get("post_code", "")),
        "{{empf_ort}}":     esc(buyer.get("city", "")),
        "{{geb_strasse}}":  "",
        "{{geb_plz_ort}}":  "",
        "{{verguetung}}":   esc(verguetung),
    }
    for k, v in repl.items():
        page = page.replace(k, v)
    return app.response_class(page, mimetype="text/html; charset=utf-8")


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

def _txn_invoice_registriert(ref: str):
    """Registriertes Rechnungsobjekt zu einer Vorgangs-Rechnungsnummer.

    `invoices` ist nach interner ID indiziert, nicht nach Rechnungsnummer —
    ein `invoices.get(ref)` geht deshalb immer ins Leere.
    """
    if not ref:
        return None
    return next((i for i in invoices.values() if i.invoice_number == ref), None)


def _letzte_versandmail(ref: str):
    """Letzte protokollierte Ausgangsmail zu einer Rechnungsnummer."""
    if not ref:
        return None
    try:
        treffer = [m for m in _load_sent_mails()
                    if m.get("reference") == ref and m.get("doc_type") == "invoice"
                    and m.get("status", "GESENDET") == "GESENDET"]
        return treffer[-1] if treffer else None
    except Exception:
        return None


def _txn_anzahlungen(txn) -> Decimal:
    """Summe der Abschlaege eines Vorgangs, die in die Schlussrechnung eingehen.

    Gezaehlt wird, was gestellt oder bezahlt ist (SENT/PAID) — genau die Auswahl,
    die das PDF unter "Bereits geleistete Anzahlungen" auflistet
    (doc_gen._prepaid_invoices). Entwuerfe zaehlen nicht: sie sind dem Kunden nie
    gestellt worden.
    """
    summe = Decimal("0.00")
    nachweis = []
    for p in (txn.get("payment_invoices") or []):
        if p.get("status") not in ("SENT", "PAID"):
            continue
        try:
            brutto = Decimal(str(p.get("amount_gross") or 0))
        except Exception:
            continue
        if brutto <= 0:
            continue
        summe += brutto
        nachweis.append({
            "reference": p.get("reference", ""),
            "title": p.get("title", ""),
            "date": p.get("paid_at") or p.get("date") or "",
            "amount_gross": float(brutto),
            "status": p.get("status", ""),
        })
    return summe.quantize(Decimal("0.01")), nachweis


def _txn_invoice_registrieren(tid, txn, data=None):
    """Registriert die Rechnung eines Vorgangs im Rechnungsregister + Archiv.

    Rückgabe: (inv, xml_bytes, report) — bei einer bereits registrierten Nummer
    (inv, None, None). Wirft ValueError mit Klartext-Meldung.
    """
    data = data or {}
    inv_step = txn["steps"].get("invoice", {})

    # Schon registriert? Dann nichts doppelt anlegen.
    vorhanden = _txn_invoice_registriert(inv_step.get("reference", ""))
    if vorhanden is not None:
        return vorhanden, None, None

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
        raise ValueError("Keine Positionen in Stufe 7 (E-Rechnung) erfasst.")

    # Rechnungsdatum / Fälligkeit
    inv_date_str = inv_step.get("date") or data.get("date", date.today().isoformat())
    inv_date = date.fromisoformat(inv_date_str[:10])
    payment_terms = data.get("payment_terms", "Zahlbar innerhalb von 30 Tagen")
    import re as _re
    _m = _re.search(r'(\d+)\s*Tag', payment_terms)
    due_date = inv_date + timedelta(days=int(_m.group(1))) if _m else inv_date + timedelta(days=30)

    inv = Invoice(
        invoice_number=number,
        invoice_date=inv_date,
        invoice_type_code=data.get("type", "380"),
        currency_code="EUR",
        buyer_reference=buyer_data.get("reference", "") or txn.get("buyer_name", ""),
        note=data.get("note", f"Vorgang {txn['id']}: {txn.get('subject', '')}"),
        seller=Seller(
            name=ms.get("name", ""),
            address=Address(
                street=ms.get("street", ""),
                house_number=ms.get("house_number", ""),
                city=ms.get("city", ""),
                post_code=ms.get("post_code", ""),
            ),
            electronic_address=ms.get("email", ""),
            electronic_address_scheme="EM",
            contact=Contact(ms.get("contact_name", ""), ms.get("contact_phone", ""), ms.get("email", "")),
            vat_id=ms.get("vat_id", ""),
            tax_registration_id=ms.get("tax_registration_id", ""),
        ),
        buyer=Buyer(
            name=buyer_data.get("name", ""),
            address=Address(
                street=buyer_data.get("street", ""),
                house_number=buyer_data.get("house_number", ""),
                city=buyer_data.get("city", ""),
                post_code=buyer_data.get("post_code", ""),
            ),
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

    # Anzahlungen (BT-113): dieselben Abschlaege, die das PDF unter "Bereits
    # geleistete Anzahlungen" auflistet, gehoeren auch in die XRechnung. Bis zum
    # 07.09.2026 stand dort fest 0,00 — das PDF forderte den Rest, die
    # maschinenlesbare Rechnung den vollen Betrag.
    inv.prepaid_amount, inv.prepaid_details = _txn_anzahlungen(txn)

    # Duplikat-Check
    if any(v.invoice_number == inv.invoice_number for v in invoices.values()):
        raise ValueError(f"Rechnungsnummer {inv.invoice_number} existiert bereits.")

    # Validieren + XML erzeugen
    report = validate_invoice(inv)
    xml_bytes = generate_and_serialize(inv)
    inv._direction = "AUSGANG"

    # Im Rechnungssystem registrieren
    invoices[inv._id] = inv
    inbox.duplicates.register(inv, inv._id)

    # Archiv-PDF: das im Vorgang erzeugte (und ggf. schon versendete) PDF hat
    # Vorrang. Sonst baut _archive_ausgang ueber invoice_pdf.build ein zweites
    # PDF aus den Mandanten-Vorlagen — und ueberschreibt dabei sogar die
    # versendete Datei unter data/documents/<Nr>.pdf.
    _pdf_bytes = None
    try:
        _pdf_datei = _DATA / "data" / "documents" / f"{number.replace('/', '_')}.pdf"
        if _pdf_datei.exists():
            _pdf_bytes = _pdf_datei.read_bytes()
    except Exception:
        _pdf_bytes = None
    _archive_ausgang(inv, xml_bytes, report, pdf_bytes=_pdf_bytes)

    # Nachtraegliche Registrierung: wurde diese Rechnung schon per Mail
    # verschickt (Vorgangs-Versand ohne vorherige Registrierung), fehlt sonst
    # der Versand-Nachweis — sie zaehlt dann weder als versendet noch als
    # offene Forderung. Der Nachweis kommt aus dem Mail-Protokoll.
    versand = _letzte_versandmail(number)
    if versand:
        inv.add_audit("EMAIL_VERSENDET", user="system",
                      comment=f"An {versand.get('to', '')} am "
                              f"{versand.get('sent_at', '')} — nachtraeglich aus dem "
                              f"Mail-Protokoll uebernommen")
        if inv.status in ("NEU", "IN_PRUEFUNG", "IN_FREIGABE"):
            _alt_status = inv.status
            inv.status = "FREIGEGEBEN"
            inv.add_audit("FREIGEGEBEN", user="system",
                          comment="Automatisch freigegeben durch bereits erfolgten Versand",
                          old_value=_alt_status)
    auto_save()

    # Step im Vorgang aktualisieren
    txn_mgr.update_step(tid, "invoice", {
        "reference": number,
        "date": inv_date_str,
        "due_date": due_date.isoformat(),
        "amount": float(inv.tax_inclusive_amount()),
        "invoice_id": inv._id,
    })
    return inv, xml_bytes, report


@app.route("/api/transactions/<tid>/generate-invoice", methods=["POST"])
def api_txn_generate_invoice(tid):
    """Erzeugt eine XRechnung aus den Daten eines Vorgangs (Stufe 7)."""
    txn = txn_mgr.get(tid)
    if not txn:
        return _json({"error": "Vorgang nicht gefunden"}, 404)
    try:
        inv, xml_bytes, report = _txn_invoice_registrieren(tid, txn, request.json or {})
    except ValueError as e:
        return _json({"error": str(e)}, 400)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return _json({"error": str(e)}, 400)

    if xml_bytes is None:      # war bereits registriert
        xml_bytes = generate_and_serialize(inv)

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
        "valid": report.is_valid if report else True,
        "errors": report.error_count if report else 0,
        "gross": float(inv.tax_inclusive_amount()),
        "xml_size": len(xml_bytes),
        "xml_b64": __import__("base64").b64encode(xml_bytes).decode(),
    }
    if zugferd_bytes:
        result["zugferd_size"] = len(zugferd_bytes)
    return _json(result)


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


# ── API: Angebots-Vorlagen (globale AGB + Hilfsendpoints) ────────────

_OFFER_SETTINGS_FILE = _DATA / "data" / "offer_settings.json"


def _load_offer_settings() -> dict:
    defaults = {
        "agb_title": "Allgemeine Geschäftsbedingungen",
        "agb_text": "",
        "default_haftung": "",
        "agb_mode": "markdown",        # "markdown" oder "pdf"
        "agb_pdf_filename": "",
    }
    try:
        if _OFFER_SETTINGS_FILE.exists():
            d = json.loads(_OFFER_SETTINGS_FILE.read_text("utf-8"))
            for k, v in defaults.items():
                d.setdefault(k, v)
            return d
    except Exception:
        pass
    return defaults


def _save_offer_settings(data: dict):
    _OFFER_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _OFFER_SETTINGS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")


@app.route("/api/offer-settings", methods=["GET"])
def api_offer_settings_get():
    return _json(_load_offer_settings())


@app.route("/api/offer-settings", methods=["PUT"])
def api_offer_settings_put():
    data = request.get_json(force=True) or {}
    settings = _load_offer_settings()
    if "agb_title" in data:
        settings["agb_title"] = str(data.get("agb_title") or "").strip() or "Allgemeine Geschäftsbedingungen"
    if "agb_text" in data:
        settings["agb_text"] = str(data.get("agb_text") or "")
    if "default_haftung" in data:
        settings["default_haftung"] = str(data.get("default_haftung") or "")
    if "agb_mode" in data:
        mode = str(data.get("agb_mode") or "").strip().lower()
        settings["agb_mode"] = mode if mode in ("pdf", "markdown") else "markdown"
    if "agb_pdf_filename" in data:
        settings["agb_pdf_filename"] = str(data.get("agb_pdf_filename") or "").strip()
    _save_offer_settings(settings)
    return _json({"saved": True, "settings": settings})


# AGB-PDF Upload + Status
_AGB_DIR = _DATA / "data" / "agb"


@app.route("/api/offer-settings/vertrag-pdf", methods=["GET"])
def api_offer_vertrag_pdf():
    """Liefert die Dienstvertrags-PDF, die beim Versand wirklich mitgeht.

    Warum eigens (07.09.2026, „wenn angewählt, dann auch beim PDF prüfen mit
    anzeigen"): Es gab bisher KEINEN Weg, dieses Dokument anzusehen. Die vorhandene
    Route /api/transactions/<id>/vertrag zeigt etwas ANDERES — den auto-gefüllten
    Vertrag als HTML zum Ausdrucken und Unterschreiben. Angehängt wird dagegen die
    hinterlegte PDF aus den Angebotseinstellungen. Wer im Versanddialog prüft, muss
    das sehen, was beim Kunden ankommt, nicht ein zweites Dokument.
    """
    from flask import send_file
    settings = _load_offer_settings()
    fname = settings.get("vertrag_pdf_filename") or ""
    if not fname:
        return _json({"error": "In den Angebotseinstellungen ist keine Vertrags-PDF hinterlegt."}, 404)
    pfad = _DATA / "data" / "vertrag" / fname
    if not pfad.exists():
        return _json({"error": f"Vertrags-PDF '{fname}' fehlt im Ablageordner."}, 404)
    return send_file(str(pfad), mimetype="application/pdf",
                     as_attachment=False, download_name=pfad.name)


@app.route("/api/offer-settings/agb-pdf", methods=["GET"])
def api_offer_agb_pdf_info():
    """Liefert Info zum aktuell hinterlegten AGB-PDF."""
    settings = _load_offer_settings()
    fname = settings.get("agb_pdf_filename") or ""
    info = {"filename": fname, "exists": False, "size_kb": 0, "pages": 0}
    if fname:
        path = _AGB_DIR / fname
        if path.exists():
            info["exists"] = True
            info["size_kb"] = round(path.stat().st_size / 1024, 1)
            try:
                from pypdf import PdfReader
                info["pages"] = len(PdfReader(str(path)).pages)
            except Exception:
                info["pages"] = 0
    return _json(info)


@app.route("/api/offer-settings/agb-pdf", methods=["POST"])
def api_offer_agb_pdf_upload():
    """AGB als PDF hochladen (multipart/form-data, Feld 'file')."""
    if "file" not in request.files:
        return _json({"error": "Keine Datei übermittelt (Feld 'file' fehlt)"}, 400)
    f = request.files["file"]
    if not f.filename or not f.filename.lower().endswith(".pdf"):
        return _json({"error": "Nur PDF-Dateien zulässig"}, 400)
    _AGB_DIR.mkdir(parents=True, exist_ok=True)
    target = _AGB_DIR / "AGB.pdf"
    f.save(str(target))
    # PDF prüfen
    try:
        from pypdf import PdfReader
        pages = len(PdfReader(str(target)).pages)
    except Exception as e:
        target.unlink(missing_ok=True)
        return _json({"error": f"PDF nicht lesbar: {e}"}, 400)
    # Settings: agb_mode auf 'pdf' + Dateiname merken
    s = _load_offer_settings()
    s["agb_mode"] = "pdf"
    s["agb_pdf_filename"] = "AGB.pdf"
    _save_offer_settings(s)
    return _json({"saved": True, "filename": "AGB.pdf",
                   "size_kb": round(target.stat().st_size / 1024, 1),
                   "pages": pages})


@app.route("/api/offer-settings/agb-pdf", methods=["DELETE"])
def api_offer_agb_pdf_delete():
    """AGB-PDF entfernen und auf Markdown-Modus zurückstellen."""
    target = _AGB_DIR / "AGB.pdf"
    if target.exists():
        target.unlink()
    s = _load_offer_settings()
    s["agb_mode"] = "markdown"
    s["agb_pdf_filename"] = ""
    _save_offer_settings(s)
    return _json({"saved": True})


# ── API: Freigabe + Mail senden ──────────────────────────────────────

@app.route("/api/transactions/<tid>/steps/<step_key>/approve-and-send", methods=["POST"])
def api_txn_approve_and_send(tid, step_key):
    """Freigeben + PDF erzeugen + per E-Mail an Empfänger senden."""
    import smtplib, ssl
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.mime.application import MIMEApplication
    from email.utils import formataddr

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

    # Dienstvertrag: Auswahl beim Versenden (19.08.2026).
    # Vorher entschied das allein die Artikelnummer — ohne "Vertrag separat"-Produkt
    # wurde er an JEDES Kundenangebot gemerged, ohne Weg das abzuwaehlen.
    # Jetzt gewinnt die ausdrueckliche Auswahl aus dem Versand-Dialog; sie wird am
    # Schritt festgehalten, damit jedes spaeter erzeugte PDF gleich aussieht.
    if "vertrag_anhaengen" in data:
        _kv = not bool(data.get("vertrag_anhaengen"))
        if step.get("kein_vertrag") != _kv:
            txn_mgr.update_step(tid, step_key, {"kein_vertrag": _kv})
        step["kein_vertrag"] = _kv

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
            delivery_date=step.get("date", ""),
            transaction_id=tid, step_key=step_key,
            intro_text=intro, closing_text=closing,
        )
    except Exception as e:
        return _json({"approved": True, "sent": False, "error": f"PDF-Fehler: {e}"})

    # 2b. Rechnungen VOR dem Versand im Rechnungsregister anlegen.
    # Ohne diesen Schritt entstehen nur Nummer und PDF — die Rechnung fehlt in
    # Rechnungsliste, Archiv und Buchhaltung und die Mail geht ohne XRechnung-XML
    # raus (Befund 31.08.2026, RE-2026-00078). Der Aufruf ist idempotent.
    if step_key == "invoice":
        try:
            _reg_data = {}
            if step.get("payment_terms"):
                _reg_data["payment_terms"] = step["payment_terms"]
            _inv_reg, _, _rep_reg = _txn_invoice_registrieren(tid, txn, _reg_data)
        except Exception as e:
            return _json({"approved": True, "sent": False,
                           "error": f"Rechnung konnte nicht im Rechnungsregister "
                                    f"angelegt werden: {e} — es wurde nichts versendet.",
                           "document": doc_entry})

        # Registrierte Rechnung und Vorgang muessen denselben Betrag zeigen.
        # Sonst wurden die Positionen nach der Registrierung geaendert: das PDF
        # zeigt dann den neuen, die XRechnung im Register den alten Betrag.
        _brutto_reg = float(_inv_reg.tax_inclusive_amount())
        _brutto_step = float(step.get("amount") or 0)
        if _brutto_step and abs(_brutto_reg - _brutto_step) > 0.01:
            return _json({"approved": True, "sent": False,
                           "error": f"Betragsabweichung: registriert sind "
                                    f"{_brutto_reg:.2f} €, der Vorgang zeigt "
                                    f"{_brutto_step:.2f} €. Eine registrierte Rechnung "
                                    f"wird nicht stillschweigend geaendert — bitte "
                                    f"stornieren und neu erstellen. Es wurde nichts "
                                    f"versendet.",
                           "document": doc_entry})

        # Wie im Direktversand: eine fehlerhafte XRechnung geht nicht raus.
        if _rep_reg is not None and not _rep_reg.is_valid:
            return _json({"approved": True, "sent": False,
                           "error": f"Die XRechnung hat {_rep_reg.error_count} "
                                    f"Validierungsfehler — Versand blockiert.",
                           "document": doc_entry})

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
        "supplier_quote": "Anfrage",
        "purchase_order": "Bestellung",
        "customer_quote": "Angebot",
        "order_intake": "Auftragseingang",
        "order_confirmation": "Auftragsbestätigung",
        "delivery_note": "Lieferschein",
        "invoice": "Rechnung",
        "dunning": "Zahlungserinnerung",
    }
    doc_title = doc_titles.get(step_key, "Dokument")
    r_name = recipient.get("name", "")
    # Anrede aus dem Kundenstammsatz — dieselbe Logik wie im PDF
    anrede = _doc_anrede(recipient)

    # Bei Auftragsbestätigung: Betreff/Body auf die Angebots-Referenz beziehen
    quote_ref = ""
    if step_key == "order_confirmation":
        try:
            quote_ref = (txn.get("steps", {}).get("customer_quote", {}) or {}).get("reference", "")
        except Exception:
            quote_ref = ""

    if step_key == "order_confirmation" and quote_ref:
        subject = f"Auftragsbestätigung zum Angebot {quote_ref} laut Ihrer Auftragserteilung"
        body_intro = (f"{anrede}\n\n"
                      f"vielen Dank für Ihre Auftragserteilung. Anbei erhalten Sie "
                      f"unsere Auftragsbestätigung {ref} zum Angebot {quote_ref}.\n\n")
    elif step_key == "order_confirmation":
        subject = f"Auftragsbestätigung {ref} laut Ihrer Auftragserteilung"
        body_intro = (f"{anrede}\n\n"
                      f"vielen Dank für Ihre Auftragserteilung. Anbei erhalten Sie "
                      f"unsere Auftragsbestätigung {ref}.\n\n")
    else:
        subject = f"{doc_title} {ref} – {ms.get('name', '')}"
        body_intro = (f"{anrede}\n\n"
                      f"anbei erhalten Sie unsere {doc_title} {ref}.\n\n")

    # Standard-Mailtext – im Vorschau-Dialog bearbeitete Werte haben Vorrang,
    # sodass ein persönlicher Zusatztext ergänzt werden kann.
    default_body = (body_intro
                    + "Bei Fragen stehen wir Ihnen gerne zur Verfügung.\n\n"
                    + f"Mit freundlichen Grüßen\n{ms.get('name', '')}")
    if str(data.get("subject", "")).strip():
        subject = str(data["subject"]).strip()
    body = str(data["body"]) if str(data.get("body", "")).strip() else default_body

    msg = MIMEMultipart()
    msg["From"] = formataddr((from_name, from_addr))
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # PDF anhängen
    pdf_path = doc_gen.get_filepath(doc_entry["filename"])
    if pdf_path:
        with open(pdf_path, "rb") as f:
            att = MIMEApplication(f.read(), _subtype="pdf")
            att.add_header("Content-Disposition", "attachment",
                           filename=doc_entry["filename"])
            msg.attach(att)

    # Dienstvertrag als SEPARATEN Anhang beifügen (BAFA/KFW-Angebote),
    # wenn im Versanddialog angehakt. Der Vertrag ist dann NICHT ins Angebot
    # gemerged (siehe doc_generator: contract_separate) — die AGB bleiben drin.
    # `_vertrag_spur` hält fest, WIE der Vertrag mitging — Protokoll, Antwort und
    # Vorgangs-Verlauf lesen es. Ohne diese Spur war nach dem Versand nicht mehr
    # erkennbar, ob überhaupt einer dabei war (07.09.2026: „ich habe den
    # Dienstvertrag angekreuzt, kann aber nicht erkennen ob und wie er mitgesendet
    # wurde").
    _vertrag_spur = None
    if data.get("attach_contract") and step_key == "customer_quote":
        try:
            _v_settings = _load_offer_settings()
            _vfname = _v_settings.get("vertrag_pdf_filename") or "Dienstvertrag.pdf"
            _vpath = _DATA / "data" / "vertrag" / _vfname
            if _vpath.exists():
                with open(_vpath, "rb") as vf:
                    v_att = MIMEApplication(vf.read(), _subtype="pdf")
                    _v_out = f"Dienstvertrag_{ref}.pdf" if ref else _vfname
                    v_att.add_header("Content-Disposition", "attachment",
                                     filename=_v_out)
                    msg.attach(v_att)
                _vertrag_spur = {"weg": "eigene_datei", "datei": _v_out, "quelle": _vfname}
            else:
                # Angehakt, aber die Datei fehlt — das darf NICHT stillschweigend
                # durchgehen, sonst glaubt der Absender, der Vertrag sei dabei.
                _vertrag_spur = {"weg": "fehlt", "datei": "", "quelle": _vfname}
                print(f"  Dienstvertrag-Anhang: Datei {_vpath} fehlt — NICHT mitgesendet")
        except Exception as e:
            _vertrag_spur = {"weg": "fehler", "datei": "", "quelle": str(e)}
            print(f"  Dienstvertrag-Anhang: {e}")
    elif step_key == "customer_quote" and not step.get("kein_vertrag"):
        # Eingebundener Weg: der Vertrag steckt im Angebots-PDF. Die Bedingung MUSS
        # dieselbe sein wie in doc_generator (Z. ~1115), sonst behauptet die Meldung
        # etwas, das gar nicht passiert ist: trägt eine Position „Vertrag separat"
        # (_sep_contract), überspringt der Generator das Einbinden.
        try:
            _os2 = _load_offer_settings() or {}
            _prods = {p.get("art_nr"): p for p in _load_products()}
            _sep = any(_prods.get(p.get("art_nr"), {}).get("contract_separate")
                       for p in (step.get("positions") or []))
            if _os2.get("vertrag_mode") == "pdf" and _os2.get("vertrag_pdf_filename") and not _sep:
                _vertrag_spur = {"weg": "im_pdf", "datei": doc_entry.get("filename", ""),
                                 "quelle": _os2.get("vertrag_pdf_filename")}
        except Exception:
            pass

    # Bei Rechnungen: auch XRechnung-XML anhängen
    if step_key == "invoice":
        try:
            # XRechnung aus Vorgang erzeugen (falls noch nicht geschehen)
            xml_path = _DATA / "data" / "documents" / f"{ref}.xml"
            if not xml_path.exists():
                # Versuche XML aus dem Rechnungssystem zu holen
                inv_obj = _txn_invoice_registriert(ref)
                if inv_obj:
                    xml_bytes = generate_and_serialize(inv_obj)
                    xml_path.parent.mkdir(parents=True, exist_ok=True)
                    xml_path.write_bytes(xml_bytes)
            if xml_path.exists():
                xml_att = MIMEApplication(xml_path.read_bytes(), _subtype="xml")
                xml_att.add_header("Content-Disposition", "attachment",
                                   filename=f"{ref}.xml")
                msg.attach(xml_att)
        except Exception as e:
            print(f"  XRechnung-XML für Mail: {e}")

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

        # Anhaenge ehrlich protokollieren: die XML wird nur mitgeschickt, wenn
        # sie auch erzeugt wurde — vorher stand sie immer im Protokoll.
        _anhaenge = [doc_entry.get("filename", "")]
        if step_key == "invoice" and (_DATA / "data" / "documents" / f"{ref}.xml").exists():
            _anhaenge.append(f"{ref}.xml")
        # Der Dienstvertrag gehört ins Protokoll: als eigene Datei mit seinem
        # Dateinamen, eingebunden als Vermerk am Angebots-PDF.
        if _vertrag_spur and _vertrag_spur["weg"] == "eigene_datei":
            _anhaenge.append(_vertrag_spur["datei"])
        elif _vertrag_spur and _vertrag_spur["weg"] == "im_pdf":
            _anhaenge.append(f"(Dienstvertrag im Angebots-PDF: {_vertrag_spur['quelle']})")
        _log_sent_mail(to_email, subject, body,
                       doc_type=step_key, reference=ref,
                       attachments=_anhaenge,
                       from_addr=from_addr)
        # Am Vorgang festhalten, damit der Verlauf es zeigt und nicht nur das Mailprotokoll
        try:
            if _vertrag_spur:
                txn_mgr.update_step(tid, step_key, {
                    "vertrag_versand": dict(_vertrag_spur, am=datetime.now().isoformat(timespec="seconds"),
                                            an=to_email)})
        except Exception as e:
            print(f"  Vertrags-Vermerk am Vorgang: {e}")

        # Versand auch am registrierten Rechnungsobjekt protokollieren — ohne
        # EMAIL_VERSENDET-Audit zählt die Rechnung in Dashboard/Archiv nicht als
        # versendet und fehlt damit in den offenen Forderungen (offen_sum).
        if step_key == "invoice":
            inv_obj = invoices.get(step.get("invoice_id") or "") \
                or _txn_invoice_registriert(ref)
            if inv_obj is not None:
                inv_obj.add_audit("EMAIL_VERSENDET",
                                  comment=f"An {to_email} (Vorgang {tid}, PDF + XML)")
                if inv_obj.status in ("NEU", "IN_PRUEFUNG", "IN_FREIGABE"):
                    _old_status = inv_obj.status
                    inv_obj.status = "FREIGEGEBEN"
                    inv_obj.add_audit("FREIGEGEBEN", user="system",
                                      comment="Automatisch freigegeben durch Versand",
                                      old_value=_old_status)
                auto_save()

        return _json({"approved": True, "sent": True, "sent_to": to_email,
                       "document": doc_entry,
                       "anhaenge": _anhaenge,
                       "vertrag": _vertrag_spur})
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
    """Sammelt ALLE Belege für einen Zeitraum — Eingang, Ausgang, Lieferanten-Docs, Workflow-Anhänge."""
    von = request.args.get("von", "")
    bis = request.args.get("bis", "")

    def in_range(d):
        if not d:
            return False
        d = d[:10]
        if von and d < von:
            return False
        if bis and d > bis:
            return False
        return True

    belege = []
    seen_files = set()  # Duplikate vermeiden

    # ── 1. Eingangsrechnungen aus E-Rechnung-Archiv ──
    archiv_dir = _DATA / "data" / "archiv"
    if archiv_dir.exists():
        idx_file = archiv_dir / "index.json"
        if idx_file.exists():
            try:
                idx = json.loads(idx_file.read_text("utf-8"))
                for entry in idx:
                    inv_date = entry.get("invoice_date", entry.get("archived_at", ""))[:10]
                    if not in_range(inv_date):
                        continue
                    inv_id = entry.get("id", "")
                    inv_dir = archiv_dir / inv_id
                    pdf_file = xml_file = None
                    if inv_dir.exists():
                        for f in inv_dir.iterdir():
                            if f.suffix.lower() == ".pdf":
                                pdf_file = str(f)
                            elif f.suffix.lower() == ".xml":
                                xml_file = str(f)
                    belege.append({
                        "typ": "Eingang",
                        "kategorie": "E-Rechnung",
                        "nummer": entry.get("invoice_number", ""),
                        "datum": inv_date,
                        "lieferant": entry.get("seller_name", ""),
                        "kunde": entry.get("buyer_name", ""),
                        "betrag": entry.get("total_gross", 0),
                        "pdf": pdf_file,
                        "xml": xml_file,
                        "id": inv_id,
                    })
                    if pdf_file:
                        seen_files.add(pdf_file)
            except Exception:
                pass

    # ── 2. Lieferanten-Dokumente (Rechnungen + Lieferscheine) ──
    sup_docs_dir = _DATA / "data" / "supplier_docs"
    if sup_docs_dir.exists():
        for sid_dir in sup_docs_dir.iterdir():
            if not sid_dir.is_dir():
                continue
            idx_file = sid_dir / "index.json"
            if not idx_file.exists():
                continue
            try:
                docs = json.loads(idx_file.read_text("utf-8"))
                for doc in docs:
                    doc_date = doc.get("uploaded_at", "")[:10]
                    if not in_range(doc_date):
                        continue
                    doc_type = doc.get("type", "sonstiges")
                    filepath = str(sid_dir / doc_type / doc.get("filename", ""))
                    if filepath in seen_files:
                        continue
                    seen_files.add(filepath)
                    typ_label = {"rechnung": "Eingang", "lieferschein": "Eingang",
                                 "angebot": "Eingang", "sonstiges": "Sonstiges"}.get(doc_type, "Sonstiges")
                    kat_label = {"rechnung": "Lieferanten-Rechnung", "lieferschein": "Lieferschein",
                                 "angebot": "Lieferanten-Angebot", "sonstiges": "Sonstiges"}.get(doc_type, doc_type)
                    belege.append({
                        "typ": typ_label,
                        "kategorie": kat_label,
                        "nummer": doc.get("original_name", doc.get("filename", "")),
                        "datum": doc_date,
                        "lieferant": doc.get("supplier_name", ""),
                        "kunde": "",
                        "betrag": 0,
                        "pdf": filepath if Path(filepath).exists() else "",
                        "xml": None,
                        "id": doc.get("hash", ""),
                    })
            except Exception:
                pass

    # ── 3. Workflow-Anhänge (Eingangsrechnungen im Step supplier_invoice) ──
    att_dir = _DATA / "data" / "attachments"
    if att_dir.exists():
        for txn_dir in att_dir.iterdir():
            if not txn_dir.is_dir():
                continue
            for step_dir in txn_dir.iterdir():
                if not step_dir.is_dir():
                    continue
                step_name = step_dir.name
                for f in step_dir.iterdir():
                    if not f.is_file():
                        continue
                    fp = str(f)
                    if fp in seen_files:
                        continue
                    seen_files.add(fp)
                    # Datum aus Datei-Änderungszeit
                    fdate = date.fromtimestamp(f.stat().st_mtime).isoformat()
                    if not in_range(fdate):
                        continue
                    step_labels = {
                        "supplier_quote": "Lieferanten-Angebot",
                        "purchase_order": "Bestellung",
                        "supplier_invoice": "Lieferanten-Rechnung",
                        "customer_quote": "Kundenangebot",
                        "order_intake": "Auftragseingang",
                        "order_confirmation": "Auftragsbestätigung",
                        "delivery_note": "Lieferschein",
                        "invoice": "Ausgangsrechnung",
                        "dunning": "Mahnung",
                    }
                    kat = step_labels.get(step_name, step_name)
                    is_eingang = step_name in ("supplier_quote", "purchase_order", "supplier_invoice")
                    belege.append({
                        "typ": "Eingang" if is_eingang else "Ausgang",
                        "kategorie": kat,
                        "nummer": f.name,
                        "datum": fdate,
                        "lieferant": "",
                        "kunde": "",
                        "betrag": 0,
                        "pdf": fp,
                        "xml": None,
                        "id": txn_dir.name + "/" + step_name,
                    })

    # ── 4. Erzeugte Ausgangsrechnungen (PDFs aus doc_generator) ──
    docs_dir = _DATA / "data" / "documents"
    doc_idx_file = docs_dir / "index.json"
    if doc_idx_file.exists():
        try:
            doc_idx = json.loads(doc_idx_file.read_text("utf-8"))
            for doc in doc_idx:
                doc_date = doc.get("created_at", "")[:10]
                if not in_range(doc_date):
                    continue
                filepath = doc.get("filepath", "")
                if filepath in seen_files:
                    continue
                seen_files.add(filepath)
                doc_type = doc.get("type", "")
                type_labels = {
                    "invoice": "Ausgangsrechnung",
                    "customer_quote": "Kundenangebot",
                    "order_intake": "Auftragseingang",
                    "order_confirmation": "Auftragsbestätigung",
                    "delivery_note": "Lieferschein",
                    "purchase_order": "Bestellung",
                    "supplier_quote": "Anfrage",
                    "dunning": "Mahnung",
                }
                kat = type_labels.get(doc_type, doc_type)
                is_ausgang = doc_type in ("invoice", "customer_quote", "delivery_note", "dunning")
                belege.append({
                    "typ": "Ausgang" if is_ausgang else "Eingang",
                    "kategorie": kat,
                    "nummer": doc.get("reference", ""),
                    "datum": doc_date,
                    "lieferant": "",
                    "kunde": "",
                    "betrag": 0,
                    "pdf": filepath if filepath and Path(filepath).exists() else "",
                    "xml": None,
                    "id": doc.get("id", ""),
                })
        except Exception:
            pass

    # ── 5. Workflow-Rechnungen (als Fallback) ──
    try:
        txns = txn_mgr.list_all()
        for txn in txns:
            inv_step = txn.get("steps", {}).get("invoice", {})
            if not inv_step.get("approved"):
                continue
            inv_date = inv_step.get("date", "")[:10]
            if not in_range(inv_date):
                continue
            ref = inv_step.get("reference", "")
            if any(b["nummer"] == ref and b["kategorie"] == "Ausgangsrechnung" for b in belege):
                continue
            amount = inv_step.get("amount", 0)
            pdf_path = str(docs_dir / f"{ref}.pdf") if ref else ""
            if pdf_path in seen_files:
                continue
            belege.append({
                "typ": "Ausgang",
                "kategorie": "Ausgangsrechnung",
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
        w.writerow(["Typ", "Kategorie", "Belegnummer", "Datum", "Lieferant/Kunde", "Betrag", "Datei"])
        for b in belege:
            partner = b.get("lieferant") or b.get("kunde") or ""
            filename = ""
            # Ordner nach Kategorie
            sub = b.get("kategorie", b.get("typ", "Sonstiges")).replace("/", "-")
            if b.get("pdf") and Path(b["pdf"]).exists():
                fname = Path(b["pdf"]).name
                arcname = f"{sub}/{fname}"
                # Duplikat im ZIP vermeiden
                existing = [n.filename for n in zf.filelist]
                if arcname in existing:
                    stem, ext = fname.rsplit(".", 1) if "." in fname else (fname, "")
                    arcname = f"{sub}/{stem}_{len(existing)}.{ext}"
                zf.write(b["pdf"], arcname)
                filename = arcname
            if b.get("xml") and Path(b["xml"]).exists():
                fname = Path(b["xml"]).name
                arcname = f"{sub}/{fname}"
                if arcname not in [n.filename for n in zf.filelist]:
                    zf.write(b["xml"], arcname)
            w.writerow([b["typ"], b.get("kategorie",""), b.get("nummer",""), b.get("datum",""),
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
    from email.utils import formataddr

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
    msg["From"] = formataddr((from_name, from_addr))
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
        _log_sent_mail(stb_email, msg["Subject"], body,
                       doc_type="steuerberater", reference=period,
                       attachments=[f"Belege_{period.replace(' ','_')}.zip"],
                       from_addr=from_addr)
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

def _is_invoice_sent(inv) -> bool:
    """Ausgangs-Rechnung versendet: EMAIL_VERSENDET-Audit ODER Status EXPORTIERT/BEZAHLT.
    Zurückgewiesene/stornierte Rechnungen gelten nicht als versendet."""
    if getattr(inv, "_direction", "") != "AUSGANG":
        return False
    if getattr(inv, "status", "") in ("ZURUECKGEWIESEN", "STORNIERT"):
        return False
    if getattr(inv, "invoice_type_code", "380") == "381":  # Gutschrift
        return False
    if inv.status in (InvoiceStatus.EXPORTIERT.value, "BEZAHLT"):
        return True
    return any(getattr(e, "event_type", "") == "EMAIL_VERSENDET"
                for e in (inv.audit_trail or []))


def _ist_storno(inv) -> bool:
    """Storno (Vollgutschrift ST-…) im Unterschied zur Teil-/Kulanzgutschrift GS-…"""
    import invoice_pdf
    return invoice_pdf.ist_storno(inv)


def _collect_storno_targets(all_inv) -> dict:
    """Gutgeschriebene Brutto-Summe je Originalrechnung: {RECHNUNGSNR: betrag}.

    Enthaelt Stornos (ST-…, voller Betrag) UND Teilgutschriften (GS-…, Typ 381
    mit preceding_invoice). Frueher ein Set — `nr in targets` funktioniert
    weiterhin, zusaetzlich steht der Betrag fuer den offenen Rest zur Verfuegung.
    """
    targets = {}
    for inv in all_inv:
        nr = (getattr(inv, "invoice_number", "") or "").upper()
        tc = getattr(inv, "invoice_type_code", "380")
        preceding = (getattr(inv, "preceding_invoice", "") or "").upper()
        if (nr.startswith("ST-") or tc == "381") and preceding:
            try:
                betrag = float(inv.tax_inclusive_amount())
            except Exception:
                betrag = 0.0
            targets[preceding] = round(targets.get(preceding, 0.0) + betrag, 2)
    return targets


def _gutgeschrieben(inv, storno_targets) -> float:
    """Brutto-Summe der Gutschriften/Stornos, die auf diese Rechnung verweisen."""
    if not storno_targets:
        return 0.0
    nr = (getattr(inv, "invoice_number", "") or "").upper()
    return float(storno_targets.get(nr, 0.0)) if nr else 0.0


def _is_invoice_paid(inv, storno_targets=None) -> bool:
    """Eine Rechnung gilt als bezahlt (oder erledigt), wenn:
    - Status BEZAHLT / ZURUECKGEWIESEN / STORNIERT
    - _paid_at gesetzt
    - Gutschriften/Storno den Bruttobetrag vollstaendig abdecken (preceding-Referenz);
      eine Teilgutschrift laesst die Rechnung offen, mindert aber den Rest
      (siehe _offener_betrag)
    """
    if getattr(inv, "status", "") in ("BEZAHLT", "ZURUECKGEWIESEN", "STORNIERT"):
        return True
    if getattr(inv, "_paid_at", None):
        return True
    gut = _gutgeschrieben(inv, storno_targets)
    if gut > 0:
        try:
            brutto = float(inv.tax_inclusive_amount())
        except Exception:
            brutto = 0.0
        if gut >= brutto - 0.01:
            return True
    return False


def _is_invoice_overpaid(inv) -> bool:
    """Echte Überzahlung: bezahlter Betrag liegt mehr als 1 € über dem Brutto.
    Kleine Aufrundungen (z.B. 85,00 € für eine 84,99 €-Rechnung) zählen NICHT."""
    pa = getattr(inv, "_paid_amount", None)
    if pa is None:
        return False
    try:
        return float(pa) > float(inv.tax_inclusive_amount()) + 1.0
    except Exception:
        return False


def _invoice_due_date(inv):
    """Liefert das Fälligkeitsdatum als date oder None.
    Fallback: invoice_date + 30 Tage wenn payment.due_date nicht gesetzt.
    Hinweis: Ein Zahlungsziel = Rechnungsdatum ist gewollt ('zahlbar sofort',
    z.B. bei Energieausweisen) und macht die Rechnung am Folgetag überfällig."""
    from datetime import timedelta
    due = None
    if hasattr(inv, "payment") and inv.payment:
        due = getattr(inv.payment, "due_date", None)
    if due is None:
        inv_date = getattr(inv, "invoice_date", None)
        if inv_date:
            due = inv_date + timedelta(days=30)
    return due


def _is_invoice_overdue(inv, today, storno_targets=None) -> bool:
    """Überfällig: versendet, nicht bezahlt/storniert, Fälligkeitsdatum überschritten."""
    if _is_invoice_paid(inv, storno_targets):
        return False
    if not _is_invoice_sent(inv):
        return False
    due = _invoice_due_date(inv)
    return bool(due and due < today)


@app.route("/api/dashboard")
def api_dashboard():
    from datetime import date as _date
    today = _date.today()

    all_inv = list(invoices.values())
    total = len(all_inv)
    offene = sum(1 for i in all_inv if i.status in ("IN_PRUEFUNG", "IN_FREIGABE", "NEU"))
    freigegeben = sum(1 for i in all_inv if i.status == "FREIGEGEBEN")
    exportiert = sum(1 for i in all_inv if i.status == "EXPORTIERT")

    # Rechnungsvolumen-Kennzahlen (nur Ausgangsrechnungen)
    # Storno-Originale werden via preceding-Referenz als 'erledigt' behandelt
    storno_targets = _collect_storno_targets(all_inv)
    sent_inv = [i for i in all_inv if _is_invoice_sent(i)]
    open_inv = [i for i in sent_inv if not _is_invoice_paid(i, storno_targets)]
    overdue_inv = [i for i in open_inv if _is_invoice_overdue(i, today, storno_targets)]

    versendet = len(sent_inv)
    volumen = sum(float(i.tax_inclusive_amount()) for i in sent_inv)
    offen_count = len(open_inv)
    offen_sum = sum(_offener_betrag(i, storno_targets) for i in open_inv)   # Teilzahlungen + Gutschriften abgezogen
    overdue_count = len(overdue_inv)
    overdue_sum = sum(_offener_betrag(i, storno_targets) for i in overdue_inv)

    # Offene Eingangsrechnungen (Verbindlichkeiten – zu zahlen an Lieferanten)
    eingang_open = [i for i in all_inv
                    if (getattr(i, "_direction", "") or "AUSGANG") == "EINGANG"
                    and getattr(i, "status", "") not in ("BEZAHLT", "STORNIERT", "ZURUECKGEWIESEN")]
    eingang_open_count = len(eingang_open)
    eingang_open_sum = sum(_offener_betrag(i) for i in eingang_open)

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
        "versendet": versendet,
        # Forderungs-Kennzahlen aus versendeten Ausgangsrechnungen
        "offen_count": offen_count, "offen_sum": round(offen_sum, 2),
        "ueberfaellig_count": overdue_count, "ueberfaellig_sum": round(overdue_sum, 2),
        # Offene Eingangsrechnungen (zu zahlen)
        "eingang_open_count": eingang_open_count, "eingang_open_sum": round(eingang_open_sum, 2),
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

# Rein formale Pflichtfeld-Regeln (fehlende Stammdaten/Steuer-ID) — kein "echter" Fehler.
_FORMAL_VALIDATION_RULES = {"BR-CO-26", "BR-08", "BR-09", "BR-10", "BR-11"}


def _hard_validation_error(report):
    """True nur bei ECHTEN Validierungsfehlern (Rechenfehler, negative Preise, …).
    Rein formale XRechnung-Pflichtfeld-Mängel (BR-DE-* wie Telefon/IBAN/PLZ sowie
    fehlende Steuer-ID) zählen NICHT: Eingangsrechnungen von Fremdsystemen können
    diese deutschen Formalregeln nie erfüllen — für die Prüf-Ansicht (Finanzamt)
    gilt dort 'geprüft' statt 'Fehler'. Akzeptiert ValidationReport ODER Index-Dict."""
    issues = getattr(report, "issues", None)
    if issues is None and isinstance(report, dict):
        issues = report.get("issues") or []
    for i in (issues or []):
        if isinstance(i, dict):
            sev, rid = i.get("severity"), i.get("rule_id") or ""
        else:
            sev, rid = getattr(i, "severity", None), getattr(i, "rule_id", "") or ""
        sev = getattr(sev, "value", sev)
        if sev != "ERROR":
            continue
        if not rid.startswith("BR-DE") and rid not in _FORMAL_VALIDATION_RULES:
            return True
    return False


@app.route("/api/invoices")
def api_invoices():
    status_filter = request.args.get("status", "")
    direction_filter = request.args.get("direction", "")
    search = request.args.get("q", "").lower()

    # Abgeleitete (zahlungsbasierte) Statusfilter zusätzlich zu den Workflow-Status
    from datetime import date as _date
    _today = _date.today()
    _storno = _collect_storno_targets(list(invoices.values()))

    result = []
    for inv in invoices.values():
        if status_filter:
            if status_filter == "OFFEN":
                if not (_is_invoice_sent(inv) and not _is_invoice_paid(inv, _storno)):
                    continue
            elif status_filter == "UEBERFAELLIG":
                if not _is_invoice_overdue(inv, _today, _storno):
                    continue
            elif status_filter == "UEBERZAHLT":
                if not _is_invoice_overpaid(inv):
                    continue
            elif inv.status != status_filter:
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
            "hard_error": _hard_validation_error(report),
            "error_count": report.error_count,
            "overpaid": _is_invoice_overpaid(inv),
            "overdue": _is_invoice_overdue(inv, _today, _storno),
            "credited": _gutgeschrieben(inv, _storno),
            "open_amount": _offener_betrag(inv, _storno),
            "is_storno": _ist_storno(inv),
            "preceding_invoice": inv.preceding_invoice or "",
            "format": inv._source_format or "XRechnung",
            "direction": inv._direction or "AUSGANG",
            "buyer_reference": inv.buyer_reference or inv.buyer.buyer_reference,
            "delivery_date": (inv.tax_point_date or inv.period_end).isoformat() if (inv.tax_point_date or inv.period_end) else None,
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

    # Gutschriften/Stornos, die auf DIESE Rechnung verweisen (fuer den Bezugs-Block
    # im Detail und die Deckelung einer weiteren Teilgutschrift)
    _nr_up = (inv.invoice_number or "").upper()
    credits = []
    for g in invoices.values():
        if g is inv or getattr(g, "invoice_type_code", "380") != "381":
            continue
        if (getattr(g, "preceding_invoice", "") or "").upper() != _nr_up:
            continue
        credits.append({
            "id": g._id, "number": g.invoice_number,
            "date": g.invoice_date.isoformat(),
            "gross": float(g.tax_inclusive_amount()),
            "is_storno": _ist_storno(g),
            "status": g.status,
        })
    credits.sort(key=lambda c: c["date"])
    _storno_map = _collect_storno_targets(invoices.values())

    return _json({
        "id": inv._id,
        "number": inv.invoice_number,
        "date": inv.invoice_date.isoformat(),
        "type_code": inv.invoice_type_code,
        "is_storno": _ist_storno(inv),
        "credits": credits,
        "credited_total": _gutgeschrieben(inv, _storno_map),
        "open_amount": _offener_betrag(inv, _storno_map),
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
            "street": inv.seller.address.street,
            "house_number": inv.seller.address.house_number,
            "city": inv.seller.address.city,
            "post_code": inv.seller.address.post_code,
            "country": inv.seller.address.country_code,
            "vat_id": inv.seller.vat_id, "tax_reg": inv.seller.tax_registration_id,
            "email": inv.seller.electronic_address,
            "contact_name": inv.seller.contact.name,
            "contact_phone": inv.seller.contact.telephone,
            "contact_email": inv.seller.contact.email,
        },
        "buyer": {
            "name": inv.buyer.name,
            "street": inv.buyer.address.street,
            "house_number": inv.buyer.address.house_number,
            "city": inv.buyer.address.city,
            "post_code": inv.buyer.address.post_code,
            "country": inv.buyer.address.country_code,
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
                        "hard_error": _hard_validation_error(report),
                        "warnings": report.warning_count,
                        "issues": [{"rule": i.rule_id, "severity": i.severity.value,
                                    "message": i.message, "field": i.field} for i in report.issues]},
        "audit_trail": audit,
        "format": inv._source_format or "XRechnung",
        "direction": inv._direction or "AUSGANG",
        "paid_at": getattr(inv, '_paid_at', None),
        "paid_amount": getattr(inv, '_paid_amount', None),
        "paid_via_booking": getattr(inv, '_paid_via_booking', None),
    })


# ── API: Rechnungs-Viewer (HTML) ───────────────────────────────────────

@app.route("/view/<inv_id>")
def view_invoice_html(inv_id):
    """Rendert eine Rechnung als eigenständige druckbare HTML-Seite."""
    inv = invoices.get(inv_id)
    if not inv:
        abort(404)
    return _render_invoice_html(inv)


def _render_invoice_html(inv):
    """Erzeugt den HTML-Code der Belegansicht für eine Rechnung."""
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
    _delivery = inv.tax_point_date or inv.period_end or inv.period_start
    if inv.period_start and inv.period_end and inv.period_start != inv.period_end:
        period_fmt = f'<div class="meta-row"><span class="k">Leistungszeitraum</span><span>{inv.period_start.strftime("%d.%m.%Y")} – {inv.period_end.strftime("%d.%m.%Y")}</span></div>'
    elif _delivery:
        period_fmt = f'<div class="meta-row"><span class="k">Leistungsdatum</span><span>{_delivery.strftime("%d.%m.%Y")}</span></div>'

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
        <div class="seller-sub">{s.address.street_full()}, {s.address.post_code} {s.address.city}</div>
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
    <div>{b.address.street_full()}</div>
    <div>{b.address.post_code} {b.address.city}</div>
    {'<div style="margin-top:4px;font-size:12px;color:#666">USt-ID: ' + b.vat_id + '</div>' if b.vat_id else ''}
  </div>
  <div class="meta-grid">
    <div class="meta-box">
      <div class="meta-row"><span class="k">{('Stornodatum' if inv.invoice_type_code == '381' else 'Rechnungsdatum')}</span><span>{date_fmt}</span></div>
      {period_fmt}
      {'<div class="meta-row"><span class="k">Fällig am</span><span style="font-weight:500">' + due_fmt + '</span></div>' if p.due_date and inv.invoice_type_code != '381' else ''}
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
    <div><div class="col-title">{s.name}</div>{s.address.street_full()}<br>{s.address.post_code} {s.address.city}</div>
    <div><div class="col-title">Kontakt</div>{s.contact.name}<br>{s.contact.telephone}<br>{s.contact.email}</div>
    <div><div class="col-title">Steuerdaten</div>USt-ID: {s.vat_id}<br>{('St.-Nr.: ' + s.tax_registration_id + '<br>') if s.tax_registration_id else ''}Format: XRechnung (EN 16931)</div>
  </div>
  {val_block}
</div></body></html>"""


# ── API: Upload ────────────────────────────────────────────────────────

# ═══════════════════════════════════════════════════════════════
# EINGANGSRECHNUNG — KI-Extraktion aus PDF (DMS B-Sub-1)
# pdfplumber zieht Text aus PDF, Claude haiku extrahiert strukturierte Felder
# ═══════════════════════════════════════════════════════════════
def _extract_pdf_text(pdf_bytes, max_pages=8):
    """Liest Text aus einem PDF via pdfplumber. Liefert (text, page_count)."""
    import io
    import pdfplumber
    text_parts = []
    page_count = 0
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page_count = len(pdf.pages)
        for i, p in enumerate(pdf.pages):
            if i >= max_pages:
                break
            t = p.extract_text() or ""
            if t:
                text_parts.append(t)
    return "\n\n".join(text_parts), page_count


_EXTRACT_SYSTEM_PROMPT = (
    "Du bist ein präziser Rechnungs-Extraktor. Gegeben ist der reine Text einer Lieferanten-Rechnung. "
    "Extrahiere die strukturierten Felder und gib NUR ein JSON-Objekt zurück — kein Markdown, "
    "keine Code-Fences, keine Erklärung. "
    "Schema (alle Felder Pflicht, Werte null wenn nicht erkennbar):\n"
    "{"
    "\"seller_name\": string|null, "
    "\"seller_street\": string|null, "
    "\"seller_postcode\": string|null, "
    "\"seller_city\": string|null, "
    "\"seller_email\": string|null, "
    "\"seller_phone\": string|null, "
    "\"seller_vat_id\": string|null, "
    "\"seller_tax_id\": string|null, "
    "\"seller_iban\": string|null, "
    "\"seller_bic\": string|null, "
    "\"invoice_number\": string|null, "
    "\"invoice_date\": \"YYYY-MM-DD\"|null, "
    "\"due_date\": \"YYYY-MM-DD\"|null, "
    "\"payment_terms\": string|null, "
    "\"currency\": \"EUR\", "
    "\"net_amount\": number|null, "
    "\"tax_amount\": number|null, "
    "\"gross_amount\": number|null, "
    "\"tax_rate\": number|null, "
    "\"lines\": [{\"description\": string, \"quantity\": number, \"unit_price\": number, "
    "\"net_amount\": number, \"tax_rate\": number}], "
    "\"note\": string|null"
    "}\n"
    "Hinweise: deutsche Datumsformate ('15.03.2026') in YYYY-MM-DD wandeln. "
    "Beträge als Dezimalzahlen mit Punkt (NICHT Komma). "
    "USt-ID-Format: DE12345678 (Großbuchstaben). "
    "IBAN ohne Leerzeichen. "
    "Wenn nur ein Gesamtbetrag erkennbar ist, setze ihn auf gross_amount, andere auf null. "
    "Bei lines: nur eindeutige Positionen mit Beschreibung extrahieren — Adressen, Anschriften und Footer-Text NICHT als Position aufnehmen."
)


# Fuer EIGENE (Ausgangs-)Rechnungen: hier ist nicht der Lieferant gesucht, sondern
# der KUNDE. Die Alt-Rechnungen fuehren netto, Steuer und brutto sauber getrennt.
# Der Absender kommt aus den Mandanten-Stammdaten (mandant.json), damit kein
# Betreibername im Quelltext steht (Nacht-Audit 04.09.2026).
def _absender_fuer_prompt() -> tuple[str, str]:
    try:
        ms = _load_mandant_settings()
    except Exception:
        ms = {}
    name = (ms.get("name") or "").strip() or "des Rechnungsstellers"
    teile = [name, (ms.get("street") or "").strip(), (ms.get("city") or "").strip()]
    return name, ", ".join(t for t in teile if t)


_ABSENDER_NAME, _ABSENDER_KURZ = _absender_fuer_prompt()

_EXTRACT_AUSGANG_PROMPT = (
    "Du bist ein präziser Rechnungs-Extraktor. Gegeben ist der reine Text einer "
    f"AUSGANGS-Rechnung des Unternehmens „{_ABSENDER_NAME}“ an einen Kunden. Extrahiere die "
    "Daten des RECHNUNGSEMPFÄNGERS (Kunde) — nicht die des Absenders — und gib NUR ein "
    "JSON-Objekt zurück, kein Markdown, keine Erklärung.\n"
    "Schema (alle Felder Pflicht, null wenn nicht erkennbar):\n"
    "{"
    "\"buyer_name\": string|null, "
    "\"buyer_street\": string|null, "
    "\"buyer_house_number\": string|null, "
    "\"buyer_postcode\": string|null, "
    "\"buyer_city\": string|null, "
    "\"buyer_email\": string|null, "
    "\"buyer_vat_id\": string|null, "
    "\"invoice_number\": string|null, "
    "\"invoice_date\": \"YYYY-MM-DD\"|null, "
    "\"due_date\": \"YYYY-MM-DD\"|null, "
    "\"currency\": \"EUR\", "
    "\"net_amount\": number|null, "
    "\"tax_amount\": number|null, "
    "\"gross_amount\": number|null, "
    "\"tax_rate\": number|null, "
    "\"lines\": [{\"description\": string, \"quantity\": number, \"unit_price\": number, "
    "\"net_amount\": number, \"tax_rate\": number}], "
    "\"note\": string|null"
    "}\n"
    "Hinweise: deutsche Datumsformate in YYYY-MM-DD wandeln. Beträge als Dezimalzahlen "
    "mit Punkt. Die Rechnungsnummer steht meist im Format R-Name-JJJJMMTT-NN. "
    f"Absenderdaten ({_ABSENDER_KURZ}) NICHT als Kunde "
    "ausgeben. Bei lines nur echte Leistungspositionen, keine Adress- oder Fußzeilen."
)


def _call_claude_extract(text, system_prompt=None):
    """Ruft Claude haiku auf und liefert das geparste JSON-Dict zurück. Wirft Exception bei Fehler."""
    import urllib.request
    import urllib.error
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY nicht im ENV")

    body = {
        "model": os.environ.get("OCR_MODEL", "claude-haiku-4-5"),
        "max_tokens": 2500,
        "temperature": 0.1,
        "system": system_prompt or _EXTRACT_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": "Rechnungstext:\n\n" + text[:14000]}]
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        method="POST",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        msg = (e.read() or b"").decode("utf-8", errors="ignore")[:300]
        raise RuntimeError(f"Anthropic-API {e.code}: {msg}")
    raw = "".join([c.get("text", "") for c in data.get("content", [])]).strip()
    # Defensive: ggf. Markdown-Codefences strippen
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
    if raw.endswith("```"):
        raw = raw[:raw.rfind("```")]
    raw = raw.strip()
    return json.loads(raw)


# ═══════════════════════════════════════════════════════════════
# BANK-IMPORT (CAMT.053) + Auto-Match gegen Rechnungen
# ═══════════════════════════════════════════════════════════════
import bank_import as _bank

_BANK_TXN_FILE = _DATA / "data" / "bank_transactions.json"


def _load_bank_txns():
    if not _BANK_TXN_FILE.exists():
        return []
    try:
        return json.loads(_BANK_TXN_FILE.read_text("utf-8"))
    except Exception:
        return []


def _save_bank_txns(txns):
    _BANK_TXN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _BANK_TXN_FILE.write_text(json.dumps(txns, indent=2, ensure_ascii=False, default=str), "utf-8")
    try:
        import db_pg
        db_pg.sync_bank()
        # V003: Zahlungseingänge automatisch verbuchen (idempotent, graceful)
        import buch_engine
        buch_engine.buche_alle()
    except Exception as e:
        print(f"[db_pg] _save_bank_txns dual-write/buchen übersprungen: {e}", flush=True)


_BANK_PRIVAT_FILE = _DATA / "data" / "bank_privat_filter.json"


def _load_privat_filter():
    """Gegenpartei-Namen (Teilstring, lowercase), deren Bank-Umsätze PRIVAT sind:
    kein Rechnungs-Matching, in der Bank-Ansicht ausgeblendet — sie werden nur an
    die Buchhaltung durchgereicht (buche_alle bucht match_status='privat' als Tag P)."""
    try:
        with open(_BANK_PRIVAT_FILE) as f:
            return [str(x).strip().lower() for x in (json.load(f).get("names") or [])
                    if str(x).strip()]
    except Exception:
        return []


def _is_privat_txn(t, names=None):
    nm = (t.get("counterparty_name") or "").lower()
    if not nm:
        return False
    return any(p in nm for p in (_load_privat_filter() if names is None else names))


def _bank_dedup_key(t):
    """Quellenübergreifender Schlüssel: Datum + Betrag + Richtung + Gegenseite + Zweck.
    Bewusst OHNE bank_reference/transaction_id, da CAMT-Upload und FinTS-Abruf für
    denselben Umsatz unterschiedliche Referenzen liefern — sonst Doppel-Einträge."""
    try:
        amt = abs(float(t.get("amount") or 0))
    except (TypeError, ValueError):
        amt = 0.0
    purpose = ' '.join((t.get("purpose") or '').split()).lower()[:60]
    return '|'.join([
        (t.get("booking_date") or '')[:10],
        f"{amt:.2f}",
        (t.get("direction") or ''),
        (t.get("counterparty_iban") or '').replace(' ', '').upper(),
        purpose,
    ])


def _bank_grobschluessel(t):
    return _bank.grobschluessel(t)


def _ist_dublette(neu_t, grob_index):
    return _bank.ist_dublette(neu_t, grob_index)


def _bank_dedupe_existing():
    """Einmalige Bereinigung: entfernt bereits gespeicherte Doppel-Einträge
    (z. B. derselbe Umsatz aus CAMT-Upload UND FinTS). Behält bei Duplikaten
    bevorzugt den gematchten/zugeordneten Eintrag."""
    txns = _load_bank_txns()
    seen = {}
    result = []
    removed = 0
    for t in txns:
        k = _bank_dedup_key(t)
        if k in seen:
            existing = seen[k]
            # Den informativeren Eintrag behalten (gematcht > ignoriert > offen)
            rank = lambda x: (1 if x.get("matched_invoice_id") else 0,
                              1 if x.get("match_status") in ("matched", "ignored") else 0)
            if rank(t) > rank(existing):
                result[result.index(existing)] = t
                seen[k] = t
            removed += 1
            continue
        seen[k] = t
        result.append(t)
    if removed:
        result.sort(key=lambda x: (x.get("booking_date") or ''), reverse=True)
        _save_bank_txns(result)
    return {"removed": removed, "remaining": len(result)}


def _apply_invoice_paid(invoice_id, txn):
    """Markiert Rechnung als bezahlt + speichert Bezugs-Buchung. Persistiert sofort."""
    inv = invoices.get(invoice_id)
    if not inv:
        return False
    setattr(inv, '_paid_at', txn.get("booking_date") or txn.get("value_date"))
    setattr(inv, '_paid_amount', txn.get("amount"))
    setattr(inv, '_paid_via_booking', txn.get("id"))
    try:
        inv.status = "BEZAHLT"
    except Exception:
        pass
    auto_save()  # damit Status den Restart überlebt
    return True


def _undo_invoice_paid(invoice_id):
    inv = invoices.get(invoice_id)
    if not inv:
        return False
    for attr in ('_paid_at', '_paid_amount', '_paid_via_booking'):
        if hasattr(inv, attr):
            try:
                delattr(inv, attr)
            except Exception:
                pass
    try:
        if getattr(inv, 'status', None) == 'BEZAHLT':
            inv.status = 'FREIGEGEBEN'
    except Exception:
        pass
    auto_save()
    return True


def _ingest_bank_records(records, source_label):
    """Gemeinsamer Ingestion-Pfad für CAMT/CSV-Upload UND FinTS-Abruf:
    dedupliziert, führt Auto-Match (high → sofort BEZAHLT, medium/low → Vorschlag)
    + Sammelzahlungs-Erkennung aus, speichert. Gibt Statistik zurück."""
    existing = _load_bank_txns()
    # Eigene Konto-IBAN aus der Gegenseiten-Spalte zurechtrücken (Outbank-Export)
    try:
        import buch_engine as _be
        verschoben = _bank.normalisiere_eigenkonto(records, _be.EIGENE_IBANS)
        if verschoben:
            print(f"[bank] {verschoben} Umsätze: eigene IBAN als Herkunftskonto übernommen", flush=True)
    except Exception as e:
        print(f"[bank] Kontonormalisierung übersprungen: {e}", flush=True)
    existing_keys = {_bank_dedup_key(t) for t in existing}
    grob_index = {}
    for t in existing:
        grob_index.setdefault(_bank_grobschluessel(t), []).append(t)
    privat_names = _load_privat_filter()

    new_items = []
    duplicates = 0
    privat_count = 0
    auto_matched_high = 0
    auto_matched_medium = 0

    ergaenzt_felder = 0
    ergaenzt_buchungen = 0
    for t in records:
        key = _bank_dedup_key(t)
        vorhanden = _bank.finde_dublette(t, grob_index)
        if key in existing_keys or vorhanden is not None:
            duplicates += 1
            # Dublette heisst nicht wertlos: traegt der neue Datensatz Felder, die
            # beim ersten Import fehlten (die CSV-Exporte kamen ohne Verwendungszweck),
            # werden sie hier nachgetragen statt verworfen.
            if vorhanden is None:
                vorhanden = next((x for x in existing
                                  if _bank_dedup_key(x) == key), None)
            if vorhanden is not None:
                felder = _bank.anreichern(vorhanden, t)
                if felder:
                    ergaenzt_felder += len(felder)
                    ergaenzt_buchungen += 1
                    vorhanden.setdefault("angereichert_aus", []).append(source_label)
            continue
        existing_keys.add(key)
        grob_index.setdefault(_bank_grobschluessel(t), []).append(t)
        t.setdefault("import_source", source_label)
        # Privat-Filter: gesperrte Gegenparteien gehen NUR an die Buchhaltung (Tag P),
        # kein Rechnungs-Matching, in der Bank-Ansicht ausgeblendet.
        if _is_privat_txn(t, privat_names):
            t["match_status"] = "privat"
            new_items.append(t)
            privat_count += 1
            continue
        # Auto-Match
        try:
            mr = _bank.auto_match(t, invoices.values())
            if mr.get("invoice_id") and mr.get("confidence") == "high":
                # Sicher genug → automatisch verlinken + Rechnung auf BEZAHLT
                t["match_suggestion"] = mr
                t["match_status"] = "matched"
                t["matched_invoice_id"] = mr["invoice_id"]
                t["matched_at"] = datetime.now().isoformat(timespec="seconds")
                t["match_confidence"] = "high"
                _apply_invoice_paid(mr["invoice_id"], t)
                auto_matched_high += 1
            elif mr.get("candidates"):
                # medium/low (auto_match liefert invoice_id=None) → als Vorschlag zum
                # Bestätigen speichern, NICHT auto-verbuchen. (Vorher fiel dieser Fall
                # durch den Guard `if mr.get("invoice_id")` heraus → Vorschlag ging verloren.)
                t["match_suggestion"] = mr
                t["match_status"] = "open"
                auto_matched_medium += 1
        except Exception as e:
            print(f"[bank-ingest] Auto-Match Fehler: {e}")
        # Sammelzahlung erkennen (eine Buchung deckt mehrere offene Rechnungen)
        if not t.get("matched_invoice_id"):
            try:
                sp = _bank.detect_split(t, invoices.values())
                if sp:
                    t["split_suggestion"] = sp
            except Exception as e:
                print(f"[bank-ingest] Split-Erkennung Fehler: {e}")
        new_items.append(t)

    existing.extend(new_items)
    existing.sort(key=lambda x: (x.get("booking_date") or ''), reverse=True)
    # Frisch importierte Buchungen, zu denen es gar keine Rechnung gibt (Dauerauftrag,
    # Lastschrift, eigene Umbuchung, Steuer), gleich den Buchhaltungsregeln zuordnen —
    # sonst waechst die offene Liste mit jedem Abruf weiter an.
    per_regel = 0
    try:
        per_regel, _ = _bank_regel_zuordnen(new_items)
    except Exception as e:
        print(f"[bank-ingest] Regel-Zuordnung übersprungen: {e}")
    _save_bank_txns(existing)

    return {
        "imported": len(new_items),
        "duplicates": duplicates,
        "privat": privat_count,
        "auto_matched_high": auto_matched_high,
        "auto_matched_suggested": auto_matched_medium,
        "per_regel": per_regel,
        "ergaenzt": ergaenzt_buchungen,
        "ergaenzte_felder": ergaenzt_felder,
    }


@app.route("/api/bank/import", methods=["POST"])
def api_bank_import():
    """Liest CAMT.053-XML ODER Bank-CSV ein, dedupliziert, führt Auto-Match aus.
    Returns: {imported, duplicate, transactions[], statement{}, format}."""
    f = request.files.get("file")
    if not f or not f.filename:
        return _json({"error": "Keine Datei"}, 400)
    raw = f.read()
    if len(raw) > 20 * 1024 * 1024:
        return _json({"error": "Datei zu groß (>20 MB)"}, 413)

    try:
        parsed, fmt = _bank.parse_auto(raw, f.filename)
    except Exception as e:
        # Abgewiesene Datei samt Fehler festhalten — sonst steht in der Oberfläche
        # eine Meldung, die im Nachhinein niemand mehr nachvollziehen kann.
        import traceback
        print(f"[bank-import] ABGEWIESEN: {f.filename} ({len(raw)} Bytes) — {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        try:
            ordner = _BASE / "data" / "bank_fehlgeschlagen"
            ordner.mkdir(parents=True, exist_ok=True)
            import re as _re
            sicher = _re.sub(r"[^A-Za-z0-9._-]", "_", f.filename or "datei")[:60]
            ziel = ordner / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{sicher}"
            ziel.write_bytes(raw)
            (ordner / (ziel.name + ".fehler.txt")).write_text(str(e), encoding="utf-8")
        except Exception as e2:
            print(f"[bank-import] Kopie der abgewiesenen Datei fehlgeschlagen: {e2}", flush=True)
        return _json({"error": f"Datei konnte nicht gelesen werden: {e}"}, 400)

    stats = _ingest_bank_records(parsed["transactions"], f.filename)
    statement = parsed["statement"]

    # PDF-Auszüge werden zusätzlich archiviert: das PDF IST der Beleg (GoBD,
    # Aufbewahrung 10 Jahre) — die daraus gelesenen Buchungen sind nur die
    # Auswertung. Doppelte Uploads erkennen wir am SHA-256 der Datei.
    if fmt == "pdf":
        try:
            statement["archiv"] = _archiviere_kontoauszug(raw, f.filename, statement, stats)
        except Exception as e:
            statement.setdefault("warnungen", []).append(f"Archivierung fehlgeschlagen: {e}")

    return _json({"ok": True, "format": fmt, "statement": statement, **stats})


def _kontoauszug_index_path():
    return _DATA / "data" / "kontoauszuege.json"


def _load_kontoauszuege():
    p = _kontoauszug_index_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")) or []
    except Exception:
        return []


def _archiviere_kontoauszug(raw, filename, statement, stats):
    """Legt das Original-PDF ab und schreibt einen Indexeintrag. Gibt den Eintrag zurück."""
    import hashlib, re as _re
    sha = hashlib.sha256(raw).hexdigest()
    index = _load_kontoauszuege()
    for e in index:
        if e.get("sha256") == sha:
            e["zuletzt_hochgeladen"] = datetime.now().isoformat(timespec="seconds")
            _kontoauszug_index_path().write_text(
                json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
            return {**e, "bereits_vorhanden": True}

    jahr = (statement.get("to_date") or statement.get("from_date") or "")[:4] or str(datetime.now().year)
    ordner = _DATA / "data" / "kontoauszuege" / jahr
    ordner.mkdir(parents=True, exist_ok=True)
    sicher = _re.sub(r"[^A-Za-z0-9._-]", "_", filename or "kontoauszug.pdf")[:80]
    if not sicher.lower().endswith(".pdf"):
        sicher += ".pdf"
    ziel = ordner / f"{sha[:10]}_{sicher}"
    ziel.write_bytes(raw)

    eintrag = {
        "id": sha[:16],
        "sha256": sha,
        "dateiname": filename,
        "pfad": str(ziel.relative_to(_DATA)),
        "groesse": len(raw),
        "iban": statement.get("account_iban"),
        "von": statement.get("from_date"),
        "bis": statement.get("to_date"),
        "buchungen": stats.get("imported", 0),
        "gelesen": stats.get("imported", 0) + stats.get("duplicates", 0),
        "warnungen": statement.get("warnungen") or [],
        "saldo_check": statement.get("saldo_check"),
        "hochgeladen": datetime.now().isoformat(timespec="seconds"),
    }
    index.append(eintrag)
    _kontoauszug_index_path().write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return eintrag


@app.route("/api/bank/abdeckung")
def api_bank_abdeckung():
    """Welcher Kontoauszug fehlt? Umsätze je Konto und Monat + erkannte Lücken."""
    try:
        import buch_engine
        return _json(buch_engine.abdeckung(request.args.get("ab") or None))
    except Exception as e:
        return _json({"error": str(e), "konten": []}, 500)


@app.route("/api/bank/statements")
def api_bank_statements():
    """Archivierte PDF-Kontoauszüge, neueste zuerst."""
    index = sorted(_load_kontoauszuege(), key=lambda e: e.get("hochgeladen") or "", reverse=True)
    return _json({"statements": index})


@app.route("/api/bank/statements/<sid>/pdf")
def api_bank_statement_pdf(sid):
    """Original-PDF eines archivierten Auszugs herunterladen."""
    from flask import send_file
    eintrag = next((e for e in _load_kontoauszuege() if e.get("id") == sid), None)
    if not eintrag:
        return _json({"error": "Kontoauszug nicht gefunden"}, 404)
    pfad = _DATA / eintrag["pfad"]
    if not pfad.exists():
        return _json({"error": "Datei fehlt im Archiv"}, 404)
    return send_file(str(pfad), mimetype="application/pdf",
                     as_attachment=False, download_name=eintrag.get("dateiname") or "kontoauszug.pdf")


@app.route("/api/bank/transactions")
def api_bank_transactions():
    """Liefert alle Buchungen mit optionalen Filtern: status, direction, q (Volltext)."""
    status_filter = request.args.get("status")  # 'open' | 'matched' | 'ignored'
    dir_filter = request.args.get("direction")
    q = (request.args.get("q") or '').lower().strip()

    txns = _load_bank_txns()

    if status_filter == "vorschlag":
        # Virtueller Filter: offene Buchungen, zu denen ein Rechnungs-Vorschlag
        # vorliegt — die Liste, die nach einem Re-Match abzuarbeiten ist.
        txns = [t for t in txns
                if t.get("match_status") == "open"
                and (t.get("match_suggestion") or {}).get("candidates")]
        txns.sort(key=lambda t: -((t.get("match_suggestion") or {}).get("score") or 0))
    elif status_filter:
        txns = [t for t in txns if t.get("match_status") == status_filter]
    else:
        # Privat-Umsätze (gesperrte Gegenparteien) gehören nicht in die E-Rechnungs-
        # Ansicht — nur mit ?status=privat explizit abrufbar.
        txns = [t for t in txns
                if t.get("match_status") not in ("privat", "intern")]
    if dir_filter:
        txns = [t for t in txns if t.get("direction") == dir_filter]
    if q:
        def hay(t):
            return ' '.join([
                str(t.get("counterparty_name") or ''),
                str(t.get("counterparty_iban") or ''),
                str(t.get("purpose") or ''),
                str(t.get("additional_info") or ''),
                str(t.get("end_to_end_id") or ''),
                str(t.get("bank_reference") or ''),
            ]).lower()
        txns = [t for t in txns if q in hay(t)]

    # Match-Suggestion-Info anreichern (Rechnungsnummer)
    for t in txns:
        sug = t.get("match_suggestion")
        if sug and sug.get("invoice_id"):
            inv = invoices.get(sug["invoice_id"])
            if inv:
                sug["invoice_number"] = getattr(inv, 'invoice_number', '')
        if t.get("matched_invoice_id"):
            inv = invoices.get(t["matched_invoice_id"])
            if inv:
                t["matched_invoice_number"] = getattr(inv, 'invoice_number', '')

    return _json({"count": len(txns), "transactions": txns})


@app.route("/api/bank/transactions/<txn_id>/match/<invoice_id>", methods=["POST"])
def api_bank_match(txn_id, invoice_id):
    """Verlinkt Buchung manuell mit einer Rechnung + setzt Status BEZAHLT."""
    txns = _load_bank_txns()
    t = next((x for x in txns if x.get("id") == txn_id), None)
    if not t:
        return _json({"error": "Buchung nicht gefunden"}, 404)
    inv = invoices.get(invoice_id)
    if not inv:
        return _json({"error": "Rechnung nicht gefunden"}, 404)
    # Falls schon ein anderes Match existiert: erst rückgängig machen
    if t.get("matched_invoice_id") and t["matched_invoice_id"] != invoice_id:
        _undo_invoice_paid(t["matched_invoice_id"])
    t["match_status"] = "matched"
    t["matched_invoice_id"] = invoice_id
    t["matched_at"] = datetime.now().isoformat(timespec="seconds")
    t["match_confidence"] = "manual"
    _apply_invoice_paid(invoice_id, t)
    _save_bank_txns(txns)
    return _json({"ok": True, "transaction": t})


def _invoice_gross(inv) -> float:
    """Bruttobetrag einer Rechnung (netto + USt je Steuersatz)."""
    try:
        return round(float(sum((s.taxable_amount + s.tax_amount)
                               for s in inv.compute_tax_subtotals())), 2)
    except Exception:
        return 0.0


@app.route("/api/bank/transactions/<txn_id>/match-split", methods=["POST"])
def api_bank_match_split(txn_id):
    """Sammelzahlung: verteilt EINE Buchung auf MEHRERE Rechnungen.

    Reihenfolge nach Rechnungsnummer. Alle Rechnungen bis auf die letzte
    werden voll beglichen; der verbleibende Rest geht auf die LETZTE Rechnung.
    Weicht der Gesamtbetrag ab (zu viel/zu wenig), wird die Differenz auf der
    letzten Rechnung gekennzeichnet (Überzahlung bzw. Restbetrag offen)."""
    txns = _load_bank_txns()
    t = next((x for x in txns if x.get("id") == txn_id), None)
    if not t:
        return _json({"error": "Buchung nicht gefunden"}, 404)
    data = request.get_json(silent=True) or {}
    inv_ids = data.get("invoice_ids") or []
    if not inv_ids:
        return _json({"error": "invoice_ids erforderlich"}, 400)
    invs = []
    for i in inv_ids:
        iv = invoices.get(i)
        if not iv:
            return _json({"error": f"Rechnung {i} nicht gefunden"}, 404)
        invs.append(iv)
    # Letzte = höchste Rechnungsnummer
    invs.sort(key=lambda x: getattr(x, "invoice_number", "") or "")

    total = round(abs(float(t.get("amount") or 0)), 2)
    last = invs[-1]

    # 1) Alle außer der letzten voll begleichen
    paid_before_last = 0.0
    allocations = []
    for inv in invs[:-1]:
        g = _invoice_gross(inv)
        tt = dict(t); tt["amount"] = g
        _apply_invoice_paid(getattr(inv, "_id", None), tt)
        paid_before_last = round(paid_before_last + g, 2)
        allocations.append({"invoice_number": inv.invoice_number,
                            "allocated": g, "status": "BEZAHLT"})

    # 2) Rest auf die letzte Rechnung
    rest = round(total - paid_before_last, 2)
    last_gross = _invoice_gross(last)
    diff = round(rest - last_gross, 2)   # + = Überzahlung, − = Restbetrag offen

    if diff >= -0.005:
        tt = dict(t); tt["amount"] = rest
        _apply_invoice_paid(getattr(last, "_id", None), tt)
        last_status = "BEZAHLT"
    else:
        # nur teilbezahlt → Rechnung bleibt offen, Restbetrag wird gekennzeichnet
        setattr(last, "_paid_at", t.get("booking_date") or t.get("value_date"))
        setattr(last, "_paid_amount", rest)
        setattr(last, "_paid_via_booking", t.get("id"))
        last_status = getattr(last, "status", "FREIGEGEBEN")

    # 3) Differenz auf der letzten Rechnung kennzeichnen
    if abs(diff) >= 0.01:
        setattr(last, "_payment_difference", diff)
        kind = "Überzahlung" if diff > 0 else "Restbetrag offen"
        marker = f"[Zahlungsdifferenz {diff:+.2f} € – {kind}]"
        note = getattr(last, "note", "") or ""
        if "[Zahlungsdifferenz" not in note:
            last.note = (note + "  " + marker).strip()
        try:
            last.add_audit("ZAHLUNGSDIFFERENZ", comment=marker)
        except Exception:
            pass
    allocations.append({"invoice_number": last.invoice_number, "allocated": rest,
                        "status": last_status, "difference": diff})

    # Buchung als Sammel-Match markieren
    t["match_status"] = "matched"
    t["matched_invoice_id"] = getattr(invs[0], "_id", None)
    t["matched_invoice_ids"] = [getattr(x, "_id", None) for x in invs]
    t["match_confidence"] = "manual-split"
    t["matched_at"] = datetime.now().isoformat(timespec="seconds")
    _save_bank_txns(txns)
    auto_save()
    return _json({"ok": True, "total": total, "allocations": allocations,
                  "difference": diff, "difference_on": last.invoice_number})


@app.route("/api/bank/transactions/<txn_id>/unmatch", methods=["POST"])
def api_bank_unmatch(txn_id):
    txns = _load_bank_txns()
    t = next((x for x in txns if x.get("id") == txn_id), None)
    if not t:
        return _json({"error": "Buchung nicht gefunden"}, 404)
    import re as _re
    # Einzel- UND Sammel-Match (Split) rückgängig machen
    undo_ids = list(t.get("matched_invoice_ids") or [])
    if t.get("matched_invoice_id") and t["matched_invoice_id"] not in undo_ids:
        undo_ids.append(t["matched_invoice_id"])
    for iid in undo_ids:
        _undo_invoice_paid(iid)
        inv = invoices.get(iid)
        if inv is not None and hasattr(inv, "_payment_difference"):
            try:
                delattr(inv, "_payment_difference")
                if getattr(inv, "note", None):
                    inv.note = _re.sub(r"\s*\[Zahlungsdifferenz[^\]]*\]", "", inv.note).strip()
            except Exception:
                pass
    t["match_status"] = "open"
    t["matched_invoice_id"] = None
    t["matched_invoice_ids"] = None
    t["matched_at"] = None
    t["match_confidence"] = None
    # Als Gegenbuchung ausgebucht? Dann auch diese Kennzeichnung loesen.
    t.pop("gegenbuchung", None)
    t.pop("intern_vorher", None)
    t.pop("intern_am", None)
    _save_bank_txns(txns)
    auto_save()
    return _json({"ok": True, "transaction": t})


@app.route("/api/bank/transactions/<txn_id>/ignore", methods=["POST"])
def api_bank_ignore(txn_id):
    """Buchung als 'ignoriert' markieren (z.B. private Bewegungen, Bankgebühren)."""
    txns = _load_bank_txns()
    t = next((x for x in txns if x.get("id") == txn_id), None)
    if not t:
        return _json({"error": "Buchung nicht gefunden"}, 404)
    if t.get("matched_invoice_id"):
        _undo_invoice_paid(t["matched_invoice_id"])
        t["matched_invoice_id"] = None
    t["match_status"] = "ignored"
    _save_bank_txns(txns)
    return _json({"ok": True})


@app.route("/api/bank/transactions/<txn_id>/privat", methods=["POST"])
def api_bank_privat(txn_id):
    """Buchung als privat kennzeichnen — statt sie an eine Rechnung zu haengen.

    „Ignorieren" hiess bisher beides: unwichtig ODER privat. Fuer die Buchhaltung
    ist das ein Unterschied — private Bewegungen gehoeren auf ein Privatkonto, nicht
    in den Papierkorb (01.09.2026). Mit `dauerhaft` wandert die Gegenpartei in
    den Privat-Filter, dann laufen kuenftige Buchungen automatisch mit.
    """
    data = request.get_json(silent=True) or {}
    txns = _load_bank_txns()
    t = next((x for x in txns if x.get("id") == txn_id), None)
    if not t:
        return _json({"error": "Buchung nicht gefunden"}, 404)
    if t.get("matched_invoice_id"):
        _undo_invoice_paid(t["matched_invoice_id"])
        t["matched_invoice_id"] = None
    t["match_status"] = "privat"
    t["privat_am"] = datetime.now().isoformat(timespec="seconds")
    t.pop("match_suggestion", None)
    t.pop("split_suggestion", None)

    aufgenommen = None
    weitere = 0
    if data.get("dauerhaft"):
        name = (t.get("counterparty_name") or "").strip()
        if name:
            pfad = _DATA / "data" / "bank_privat_filter.json"
            try:
                cfg = json.loads(pfad.read_text("utf-8")) if pfad.exists() else {}
            except Exception:
                cfg = {}
            namen = [str(n).lower() for n in (cfg.get("names") or [])]
            if name.lower() not in namen:
                namen.append(name.lower())
                cfg["names"] = namen
                pfad.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
                aufgenommen = name
            # bereits vorhandene Buchungen derselben Gegenpartei mitnehmen
            for x in txns:
                if x is t or x.get("match_status") not in ("open", "regel"):
                    continue
                if name.lower() in (x.get("counterparty_name") or "").lower():
                    x["match_status"] = "privat"
                    x["privat_am"] = t["privat_am"]
                    weitere += 1
    _save_bank_txns(txns)
    return _json({"ok": True, "aufgenommen": aufgenommen, "weitere": weitere})


@app.route("/api/bank/transactions/<txn_id>/privat", methods=["DELETE"])
def api_bank_privat_zurueck(txn_id):
    """Privat-Kennzeichnung einer einzelnen Buchung zuruecknehmen."""
    txns = _load_bank_txns()
    t = next((x for x in txns if x.get("id") == txn_id), None)
    if not t:
        return _json({"error": "Buchung nicht gefunden"}, 404)
    t["match_status"] = "open"
    t.pop("privat_am", None)
    _save_bank_txns(txns)
    return _json({"ok": True})


def _buch_regeln_laden():
    """Aktive Buchhaltungsregeln (buch.regel) nach Prioritaet."""
    con = None
    try:
        import db_pg
        con = db_pg._connect()
        if con is None:
            return []
        with con.cursor() as cur:
            cur.execute("select prio, suchbegriff, tag, coalesce(konto,'') "
                        "from buch.regel where aktiv order by prio")
            return [(int(p), (b or '').strip().lower(), t, k)
                    for p, b, t, k in cur.fetchall()]
    except Exception as e:
        print(f"[bank-regel] Regeln nicht lesbar: {e}", flush=True)
        return []
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass


_REGEL_TAG_TEXT = {"P": "privat", "F": "Firma", "S": "Steuer",
                   "G": "Gesellschafter", "I": "intern"}


# Rechnungsnummern, wie Kunden sie in den Verwendungszweck schreiben.
_RECHNUNGSNR_IM_ZWECK = __import__("re").compile(
    r"\bR[-. ]?[A-Za-zÄÖÜäöü]{2,}[-. _]?\s?\d{8}[-. ]?\d{2}|RE-20\d\d-\d{5}",
    __import__("re").IGNORECASE)


# Privatentnahmen und Familienzahlungen laufen auf die eigenen Namen. Deren Regeln
# duerfen NUR die Gegenpartei pruefen, nicht den Buchungstext: der eigene Name steht
# als Referenz in unzaehligen fremden Buchungen — Hausverwaltung, Steuerberater,
# Versicherung —, und die landeten dadurch faelschlich als Privatentnahme
# (40 Buchungen, 18.911 EUR; 01.09.2026).
_NUR_GEGENPARTEI_KONTEN = {"1800", "1810"}


def _bank_regel_treffer(t, regeln):
    """Erste passende Buchhaltungsregel zu einer Buchung (oder None)."""
    name = str(t.get("counterparty_name") or "").lower()
    blob = " ".join([name,
                     str(t.get("purpose") or ""),
                     str(t.get("additional_info") or "")]).lower()
    for _prio, such, tag, konto in regeln:
        if not such:
            continue
        feld = name if konto in _NUR_GEGENPARTEI_KONTEN else blob
        if such in feld:
            return {"suchbegriff": such, "tag": tag, "konto": konto,
                    "text": _REGEL_TAG_TEXT.get(tag, tag)}
    return None


def _bank_regel_zuordnen(txns, regeln=None):
    """Setzt offene Buchungen, die eine Buchhaltungsregel trifft, auf 'regel'.

    Dauerauftraege, Lastschriften, eigene Umbuchungen, Steuern und Beitraege sind
    buchhalterisch ueber buch.regel versorgt — es gibt zu ihnen gar keine Rechnung.
    In der Bankansicht standen sie trotzdem jahrelang als „offen", weil dort allein
    „Rechnung verknuepft?" zaehlte (31.08.2026). Der vorherige Status bleibt in
    `regel_vorher` stehen, damit sich das jederzeit zurueckdrehen laesst.
    """
    if regeln is None:
        regeln = _buch_regeln_laden()
    if not regeln:
        return 0, 0
    n = 0
    aufgefrischt = 0
    for t in txns:
        status = t.get("match_status")
        if status not in ("open", "regel"):
            continue
        treffer = _bank_regel_treffer(t, regeln)

        # Schon per Regel gebucht: Zuordnung nachfuehren, wenn sich die Regel
        # geaendert hat (z. B. easybell von Firma auf privat). Trifft gar keine
        # Regel mehr, geht die Buchung zurueck in die offene Liste.
        if status == "regel":
            if treffer is None:
                t["match_status"] = t.pop("regel_vorher", "open") or "open"
                t.pop("regel", None)
                t.pop("regel_am", None)
                aufgefrischt += 1
            elif treffer != t.get("regel"):
                t["regel"] = treffer
                t["regel_am"] = datetime.now().isoformat(timespec="seconds")
                aufgefrischt += 1
            continue

        if not treffer:
            continue
        # Ein Geldeingang, der eine Rechnungsnummer nennt, ist eine Kundenzahlung —
        # da hat keine Buchhaltungsregel etwas verloren, auch wenn der Kundenname
        # zufaellig in einer Regel steht (01.09.2026, Manz).
        if t.get("direction") == "credit" and _RECHNUNGSNR_IM_ZWECK.search(
                (t.get("purpose") or "") + " " + (t.get("additional_info") or "")):
            continue
        # Ein Rechnungs-Vorschlag haelt die Buchung nur dann offen, wenn er auf einer
        # NUMMER beruht — Rechnungs- oder Energieausweisnummer im Verwendungszweck.
        # Trifft er bloss ueber Betrag und Name, gewinnt die Buchhaltungsregel: ein
        # Abo (u-wert.net) oder ein Wocheneinkauf (Lidl) bezahlt keine Eingangsrechnung
        # (01.09.2026).
        sug = t.get("match_suggestion") or {}
        if sug.get("candidates"):
            gruende = " ".join(sug["candidates"][0].get("reasons") or [])
            harter_treffer = any(w in gruende for w in
                                 ("Rechnungsnummer", "Energieausweisnummer",
                                  "laufende Nummer"))
            if harter_treffer:
                continue
        t["regel_vorher"] = t.get("match_status")
        t["match_status"] = "regel"
        t["regel"] = treffer
        t["regel_am"] = datetime.now().isoformat(timespec="seconds")
        n += 1
    return n, aufgefrischt


@app.route("/api/bank/auszuege-nachtragen", methods=["POST"])
def api_bank_auszuege_nachtragen():
    """Liest die gespeicherten Kontoauszug-PDFs erneut ein — zum Anreichern.

    Die CSV-Exporte kamen teils ohne Konto, ohne Namen und ohne Buchungstext herein.
    Die Auszuege derselben Zeitraeume liegen unter data/kontoauszuege und tragen
    alles davon. Erneutes Einlesen legt nichts doppelt an, sondern fuellt die
    Luecken der vorhandenen Buchungen (01.09.2026).
    """
    daten = request.get_json(silent=True) or {}
    try:
        limit = max(1, min(20, int(daten.get("limit") or 5)))
    except Exception:
        limit = 5
    ordner = _DATA / "data" / "kontoauszuege"
    dateien = sorted(ordner.glob("**/*.pdf")) + sorted(ordner.glob("**/*.PDF"))
    erledigt = set(daten.get("erledigt") or [])
    offen = [p for p in dateien if p.name not in erledigt]
    ergebnisse = []
    for pfad in offen[:limit]:
        try:
            res = _bank.parse_pdf(pfad.read_bytes(), pfad.name)
            stats = _ingest_bank_records(res.get("transactions", []), f"Auszug {pfad.name}")
            ergebnisse.append({"datei": pfad.name, "zeilen": len(res.get("transactions", [])),
                                "neu": stats.get("imported", 0),
                                "ergaenzt": stats.get("ergaenzt", 0),
                                "felder": stats.get("ergaenzte_felder", 0)})
        except Exception as e:
            ergebnisse.append({"datei": pfad.name, "fehler": str(e)})
    return _json({"ok": True, "verarbeitet": len(ergebnisse),
                   "offen": max(0, len(offen) - len(ergebnisse)),
                   "gesamt": len(dateien), "ergebnisse": ergebnisse,
                   "erledigt": sorted(erledigt | {e["datei"] for e in ergebnisse})})


@app.route("/api/bank/regel-zuordnung", methods=["POST"])
def api_bank_regel_zuordnung():
    """Ordnet offene Buchungen den Buchhaltungsregeln zu (Belegpflicht entfaellt)."""
    txns = _load_bank_txns()
    regeln = _buch_regeln_laden()
    if not regeln:
        return _json({"error": "Keine Buchhaltungsregeln lesbar (Postgres)."}, 500)
    n, aufgefrischt = _bank_regel_zuordnen(txns, regeln)
    _save_bank_txns(txns)
    return _json({"ok": True, "regeln": len(regeln), "zugeordnet": n,
                  "aufgefrischt": aufgefrischt,
                  "offen": len([t for t in txns if t.get("match_status") == "open"])})


@app.route("/api/bank/regel-zuordnung", methods=["DELETE"])
def api_bank_regel_zuordnung_zuruecknehmen():
    """Macht die Regel-Zuordnung wieder rueckgaengig."""
    txns = _load_bank_txns()
    n = 0
    for t in txns:
        if t.get("match_status") == "regel":
            t["match_status"] = t.pop("regel_vorher", "open") or "open"
            t.pop("regel", None)
            t.pop("regel_am", None)
            n += 1
    _save_bank_txns(txns)
    return _json({"ok": True, "zurueckgesetzt": n})


@app.route("/api/bank/gegenbuchungen", methods=["GET", "POST", "DELETE"])
def api_bank_gegenbuchungen():
    """Bucht Bewegungen aus, zu denen es im eigenen Haus eine Gegenbuchung gibt.

    Uebertrag zwischen zwei eigenen Konten, Ausgleich der Kartenabrechnung, Storno
    derselben Gegenpartei: Geld wechselt nur die Tasche, es ist weder Aufwand noch
    Ertrag und braucht keinen Beleg (01.09.2026). GET zeigt nur an, POST bucht
    aus, DELETE dreht es zurueck.
    """
    import gegenbuchung
    txns = _load_bank_txns()
    if request.method == "DELETE":
        n = gegenbuchung.zuruecknehmen(txns)
        _save_bank_txns(txns)
        return _json({"ok": True, "zurueckgesetzt": n})

    treffer = gegenbuchung.finde(txns)
    nach_typ = {}
    for t in txns:
        info = treffer.get(t.get("id"))
        if not info:
            continue
        eintrag = nach_typ.setdefault(info["typ"], {"typ": info["typ"], "text": info["text"],
                                                   "anzahl": 0, "summe": 0.0, "offen": 0})
        eintrag["anzahl"] += 1
        eintrag["summe"] = round(eintrag["summe"] + float(t.get("amount") or 0), 2)
        if t.get("match_status") == "open":
            eintrag["offen"] += 1
    typen = sorted(nach_typ.values(), key=lambda x: -x["anzahl"])
    if request.method == "GET":
        return _json({"ok": True, "erkannt": len(treffer), "typen": typen,
                      "ausgebucht": len([t for t in txns
                                         if t.get("match_status") == "intern"])})

    n = gegenbuchung.ausbuchen(txns, treffer)
    _save_bank_txns(txns)                       # sync_bank + buche_alle laufen mit
    nachgezogen = None
    try:
        import buch_engine
        nachgezogen = buch_engine.stillege_interne()
    except Exception as e:
        print(f"[gegenbuchung] Altbestand-Bereinigung uebersprungen: {e}", flush=True)
    return _json({"ok": True, "ausgebucht": n, "erkannt": len(treffer), "typen": typen,
                  "buchhaltung_stillgelegt": nachgezogen,
                  "offen": len([t for t in txns if t.get("match_status") == "open"])})


@app.route("/api/bank/rematch", methods=["POST"])
def api_bank_rematch():
    """Lässt alle Buchungen erneut durch Auto-Match laufen.
    Bei bereits gematchten Buchungen wird der BEZAHLT-Status auf der Rechnung
    neu gesetzt (idempotent — repariert verlorene Status nach Restart o.ä.)."""
    txns = _load_bank_txns()
    rechecked_open = 0
    refreshed_matched = 0
    new_high = 0
    new_suggested = 0
    for t in txns:
        status = t.get("match_status")
        if status == "matched" and t.get("matched_invoice_id"):
            # Schon verlinkt → BEZAHLT-Status auf Rechnung sicherstellen
            if _apply_invoice_paid(t["matched_invoice_id"], t):
                refreshed_matched += 1
            continue
        if status != "open":
            continue
        rechecked_open += 1
        try:
            mr = _bank.auto_match(t, invoices.values())
            has_candidates = bool(mr.get("candidates"))
            if mr.get("invoice_id") and mr.get("confidence") == "high":
                # Sicher genug → automatisch verlinken
                t["match_suggestion"] = mr
                t["match_status"] = "matched"
                t["matched_invoice_id"] = mr["invoice_id"]
                t["matched_at"] = datetime.now().isoformat(timespec="seconds")
                t["match_confidence"] = "high"
                _apply_invoice_paid(mr["invoice_id"], t)
                new_high += 1
            elif has_candidates:
                # Unsicher / mehrere Möglichkeiten → User soll bestätigen
                t["match_suggestion"] = mr
                new_suggested += 1
            else:
                t.pop("match_suggestion", None)
            # Sammelzahlung erkennen (eine Buchung = mehrere offene Rechnungen)
            if not t.get("matched_invoice_id"):
                sp = _bank.detect_split(t, invoices.values())
                if sp:
                    t["split_suggestion"] = sp
                else:
                    t.pop("split_suggestion", None)
        except Exception as e:
            print(f"[bank-rematch] Fehler: {e}")
    _save_bank_txns(txns)
    # Fuer die Rueckmeldung an der Oberflaeche: wie viele offene Buchungen haben
    # jetzt einen Vorschlag, und wie viele davon sind wirklich belastbar.
    mit_vorschlag = 0
    stark = 0
    for t in txns:
        if t.get("match_status") != "open":
            continue
        sug = t.get("match_suggestion") or {}
        if sug.get("candidates"):
            mit_vorschlag += 1
            if (sug.get("score") or 0) >= 60:
                stark += 1
    return _json({
        "ok": True,
        "rechecked": rechecked_open,
        "refreshed_matched": refreshed_matched,
        "new_high": new_high,
        "new_suggested": new_suggested,
        "mit_vorschlag": mit_vorschlag,
        "stark": stark,
        "offen": len([t for t in txns if t.get("match_status") == "open"]),
    })


@app.route("/api/bank/stats")
def api_bank_stats():
    """Übersichts-Stats für Dashboard."""
    txns = _load_bank_txns()
    return _json({
        "total": len(txns),
        "open": len([t for t in txns if t.get("match_status") == "open"]),
        "matched": len([t for t in txns if t.get("match_status") == "matched"]),
        "ignored": len([t for t in txns if t.get("match_status") == "ignored"]),
        "regel": len([t for t in txns if t.get("match_status") == "regel"]),
        "with_suggestion": len([t for t in txns if t.get("match_suggestion") and t.get("match_status") == "open"]),
    })


# ── API: Bank-Direktabruf via FinTS/HBCI (read-only: Umsätze + Saldo) ─────
import bank_secure as _bsec

# In-Memory Pending-Store für laufende decoupled-TAN-Vorgänge (enthält NIE die PIN).
_BANK_SYNC_PENDING = {}


def _bank_slug(s):
    import re as _re
    sl = _re.sub(r'[^a-z0-9]+', '-', (s or '').strip().lower()).strip('-')
    return sl or "bank"


def _gen_product_id():
    """python-fints verlangt seit v4 eine product_id (FinTS-Limit: max. 25 Zeichen).
    Wer eine registrierte ID hat, trägt sie ein; sonst eine stabile app-eigene Kennung."""
    import secrets as _secrets
    return ("B2B" + _secrets.token_hex(10))[:25]  # 3 + 20 = 23 Zeichen


def _valid_pid(pid):
    return bool(pid) and len(pid) <= 25


def _bank_ensure_product_id(cid, conn):
    """Heilt gespeicherte Verbindungen ohne/mit zu langer product_id (persistiert stabil)."""
    if _valid_pid(conn.get("product_id")):
        return conn
    conns = _bsec.load_connections()
    c = conns.get(cid) or conn
    c["product_id"] = _gen_product_id()
    conns[cid] = c
    _bsec.save_connections(conns)
    return c


def _bank_pending_gc():
    # 20 Minuten (28.07.2026): Die Sparkassen-Freigabe passiert am Handy — Entsperren,
    # App öffnen, Auftrag prüfen, freigeben. 10 Minuten waren dafür knapp; lief die Sitzung
    # ab, kam „Sitzung abgelaufen" NACH der Freigabe und der Abruf musste neu starten
    # (inkl. neuer Freigabe). Der Store enthält keine PIN, nur den FinTS-Dialogstand.
    cutoff = datetime.now() - timedelta(minutes=20)
    for k in [k for k, v in list(_BANK_SYNC_PENDING.items()) if v["ts"] < cutoff]:
        _BANK_SYNC_PENDING.pop(k, None)


def _bank_conn_public(cid, conn):
    return {
        "id": cid,
        "connected": True,
        "blz": conn.get("blz"),
        "fints_url": conn.get("fints_url"),
        "user_id": conn.get("user_id"),
        "iban": conn.get("iban"),
        "bank_name": conn.get("bank_name") or cid,
        "account_verbunden": bool(conn.get("account")),
        "last_sync": conn.get("last_sync"),
        "last_balance": conn.get("last_balance"),
        # Saldo JE KONTO: ein Zugang fuehrt mehrere Konten (Sparkasse: drei).
        "last_balances": conn.get("last_balances") or [],
        # Sperre und TAN-Freigabe gehoerten in die Oberflaeche: eine gesperrte
        # Verbindung war dort nicht zu sehen und ohne Entsperr-Knopf auch nicht
        # zu loesen — der Abruf lief nur noch in eine 423-Meldung.
        "pin_gesperrt": bool(conn.get("pin_gesperrt")),
        "pin_gesperrt_am": conn.get("pin_gesperrt_am"),
        "pin_gesperrt_grund": conn.get("pin_gesperrt_grund"),
        "sca_gueltig_bis": conn.get("sca_gueltig_bis"),
    }


@app.route("/api/bank/connections", methods=["GET"])
def api_bank_connections_list():
    """Liste aller Bankverbindungen (ohne PIN)."""
    conns = _bsec.load_connections()
    return _json({"connections": [_bank_conn_public(cid, c) for cid, c in conns.items()]})


@app.route("/api/bank/connection", methods=["POST"])
def api_bank_connection_save():
    """Legt eine Bankverbindung an / aktualisiert sie. PIN AES-GCM-verschlüsselt,
    niemals im Klartext. Mehrere Banken möglich (id = Slug aus bank_name, falls
    nicht angegeben). Neue/geänderte Zugangsdaten setzen Konto-/State-Cache zurück."""
    data = request.get_json(silent=True) or {}
    user_id = (data.get("user_id") or "").strip()
    pin = data.get("pin") or ""
    bank_name = (data.get("bank_name") or "").strip()
    cid = (data.get("id") or "").strip() or _bank_slug(bank_name)
    conns = _bsec.load_connections()
    prev = conns.get(cid) or {}
    # PIN: neu verschlüsseln; beim Bearbeiten ohne Eingabe die bestehende behalten
    pin_enc = _bsec.encrypt(pin) if pin else prev.get("pin_enc")
    if not user_id or not pin_enc:
        return _json({"error": "Benutzerkennung und PIN erforderlich"}, 400)
    _entered = (data.get("product_id") or "").strip()
    product_id = _entered if _valid_pid(_entered) else (prev.get("product_id") if _valid_pid(prev.get("product_id")) else _gen_product_id())
    conns[cid] = {
        "blz": (data.get("blz") or prev.get("blz") or "").strip(),
        "fints_url": (data.get("fints_url") or prev.get("fints_url") or "").strip(),
        "user_id": user_id,
        "pin_enc": pin_enc,
        "product_id": product_id,
        "tan_mechanism": (data.get("tan_mechanism") or "").strip() or None,
        "tan_medium": (data.get("tan_medium") or "").strip() or None,
        "iban": (data.get("iban") or prev.get("iban") or "").replace(" ", "").strip() or None,
        "bank_name": bank_name or prev.get("bank_name") or cid,
        "account": None,          # wird beim 1. Abruf ermittelt
        "fints_state_enc": None,  # 90-Tage-SCA-State (verschlüsselt)
        "last_sync": prev.get("last_sync"),
        "last_balance": prev.get("last_balance"),
    }
    _bsec.save_connections(conns)
    return _json({"ok": True, **_bank_conn_public(cid, conns[cid])})


@app.route("/api/bank/connection/<cid>", methods=["DELETE"])
def api_bank_connection_delete(cid):
    conns = _bsec.load_connections()
    conns.pop(cid, None)
    _bsec.save_connections(conns)
    return _json({"ok": True})


def _bank_initial_state(conn):
    state_bytes = None
    if conn.get("fints_state_enc"):
        try:
            state_bytes = _bsec.decrypt_bytes(conn["fints_state_enc"])
        except Exception:
            state_bytes = None
    return {"stage": "info", "client_data": state_bytes, "mid_tan": None,
            "results": {}, "account": conn.get("account")}


def _bank_finalize(cid, state, public):
    """Nach erfolgreichem Abruf: State + Konto + Saldo sichern, Umsätze einspeisen."""
    conns = _bsec.load_connections()
    c = conns.get(cid) or {}
    try:
        if state.get("client_data") is not None:
            c["fints_state_enc"] = _bsec.encrypt_bytes(state["client_data"])
    except Exception as e:
        print(f"[bank-fints] State-Sicherung übersprungen: {e}")
    if state.get("account"):
        c["account"] = state["account"]
    c["last_sync"] = datetime.now().isoformat(timespec="seconds")
    c["last_balance"] = public.get("balance")
    if public.get("balances"):
        c["last_balances"] = public["balances"]
    # SCA-Freigabe datieren (28.07.2026): Nach EINER TAN-Bestätigung liefert die Bank
    # ~90 Tage TAN-freie Abrufe (PSD2 Art. 10). Wir merken den Zeitpunkt der letzten
    # TAN-Freigabe und rechnen das Ablaufdatum aus — so weiß die Oberfläche (und der
    # Cron-Bericht), wann die Sparkasse wieder eine Bestätigung verlangt, statt es
    # erst beim fehlgeschlagenen Abruf zu merken.
    if state.get("_tan_freigegeben_am"):
        c["sca_freigabe_am"] = state["_tan_freigegeben_am"]
        try:
            _d = datetime.fromisoformat(state["_tan_freigegeben_am"])
            c["sca_gueltig_bis"] = (_d + timedelta(days=90)).isoformat(timespec="seconds")
        except Exception:
            pass
    conns[cid] = c
    _bsec.save_connections(conns)
    bank = c.get("bank_name") or cid
    stats = _ingest_bank_records(public.get("transactions", []), f"FinTS-Abruf ({bank})")
    return {"status": "done", "bank": bank, "balance": public.get("balance"),
            "balances": public.get("balances", []),
            "iban": public.get("account_iban"), **stats}


def _bank_run_step(cid, conn, state, tan=None):
    """Ein FinTS-Schritt; mappt Ergebnis auf JSON-Antwort. PIN wird hier entschlüsselt.

    tan: vom Nutzer eingegebene TAN. Nur nötig, wenn das Verfahren NICHT decoupled ist
    (Sparkasse pushTAN/chipTAN). Postbank BestSign bleibt Freigabe-in-der-App (leere TAN).
    """
    import bank_fints as _bfints
    conn = dict(conn); conn["_id"] = cid          # Bank-Kennung fürs Dialog-Protokoll
    pin = _bsec.decrypt(conn["pin_enc"])
    status, new_state, public = _bfints.step(conn, pin, state, tan=tan)
    del pin
    if status == "tan_pending":
        _bank_pending_gc()
        token = __import__("secrets").token_hex(16)
        _BANK_SYNC_PENDING[token] = {"state": new_state, "conn_id": cid, "ts": datetime.now()}
        _dec = public.get("decoupled", True)
        return _json({"status": "tan_pending", "token": token,
                      "challenge": public.get("challenge") or "",
                      "decoupled": _dec,
                      "tan_erforderlich": bool(public.get("tan_erforderlich") or not _dec),
                      "message": ("Bitte den Abruf in der Banking-App freigeben."
                                  if _dec else
                                  "Bitte die TAN aus Ihrer Banking-App bzw. vom chipTAN-Generator eingeben.")})
    return _json(_bank_finalize(cid, new_state, public))


def _bank_volltrace_datei(cid):
    """Pfad der FinTS-Volltrace-Datei einer Bankverbindung."""
    return _DATA / "data" / f"fints-dialog-{cid}.log"


def _bank_dialog_mitschnitt(cid, stufe, voll=False):
    """Schneidet die Bank-Rückmeldungen (HIRMG/HIRMS „Dialog response: CODE - Text")
    eines Abrufs mit und schreibt sie ins Dialog-Protokoll.

    Die Codes sind bei FinTS die eigentliche Fehlerauskunft (3920 zugelassene
    TAN-Verfahren, 9010 Auftrag abgelehnt, 9931 TAN falsch, 9050 Produkt nicht
    registriert). Bisher standen sie nur im journalctl und NUR im Fehlerfall —
    damit war nach einem Versuch oft nicht mehr rekonstruierbar, was die Bank
    gesagt hat. Bei Banken, die den Zugang nach wenigen Fehlversuchen sperren,
    muss ein einziger Versuch alles hergeben.
    """
    import logging as _logging
    from contextlib import contextmanager as _cm

    @_cm
    def _lauf():
        cap = []

        class _H(_logging.Handler):
            def emit(self, rec):
                try:
                    m = rec.getMessage()
                except Exception:
                    return
                if "Dialog response" in m and m.strip() not in cap:
                    cap.append(m.strip())

        log = _logging.getLogger("fints")
        h = _H()
        prev = log.level
        log.addHandler(h)
        # Volltrace: die komplette FinTS-Unterhaltung Segment fuer Segment in eine
        # nur fuer root lesbare Datei. python-fints schreibt diese Ausgabe innerhalb
        # von `Password.protect()` — PIN und TAN sind darin bereits unkenntlich.
        # Nur einschalten, wenn die Verbindung `dialog_mitschnitt` gesetzt hat.
        fh = None
        if voll:
            try:
                datei = _bank_volltrace_datei(cid)
                datei.parent.mkdir(parents=True, exist_ok=True)
                if datei.exists() and datei.stat().st_size > 4 * 1024 * 1024:
                    datei.replace(str(datei) + ".1")
                fh = _logging.FileHandler(str(datei), encoding="utf-8")
                fh.setFormatter(_logging.Formatter("%(asctime)s %(name)s %(message)s"))
                fh.setLevel(_logging.DEBUG)
                log.addHandler(fh)
                os.chmod(str(datei), 0o600)
            except Exception as e:
                print(f"[bank-fints] Volltrace nicht moeglich: {e}")
                fh = None
        log.setLevel(_logging.DEBUG if fh else _logging.INFO)
        try:
            yield cap
        finally:
            log.removeHandler(h)
            if fh:
                log.removeHandler(fh)
                try:
                    fh.close()
                except Exception:
                    pass
            log.setLevel(prev)
            if cap:
                try:
                    import bank_fints as _bf
                    _bf.protokoll(cid, "bank_rueckmeldungen", stufe=stufe,
                                  meldungen=cap[-20:])
                except Exception:
                    pass
    return _lauf()


def _bank_echter_pin_fehler(meldungen):
    """Sagt die Bank wirklich „PIN falsch"?

    python-fints wirft `FinTSClientPINError("… PIN wrong?")` bei JEDEM gescheiterten
    Dialogaufbau — auch wenn die Bank etwas voellig anderes gemeldet hat (bei der
    Sparkasse am 31.08.2026: 9340 „Ungueltige Signatur"). Wird daraufhin die
    Verbindung gesperrt, steht die Diagnose still, obwohl die PIN nie im Spiel war.
    Deshalb entscheiden die Rueckmeldecodes der Bank, nicht der Ausnahmetext.

    Ohne Rueckmeldungen bleibt es beim vorsichtigen Verhalten: sperren.
    """
    if not meldungen:
        return True
    for m in meldungen:
        t = str(m).upper()
        if "9931" in t or "9942" in t:
            return True
        if "PIN" in t and any(w in t for w in ("FALSCH", "UNGÜLTIG", "UNGUELTIG",
                                               "GESPERRT", "WRONG")):
            return True
    return False


def _bank_pin_sperre_setzen(cid, meldung):
    """Verbindung nach einem PIN-Fehler sperren (28.07.2026).
    Banken sperren den Zugang nach wenigen Fehlversuchen — deshalb NIE automatisch
    weiterprobieren. Entsperrt wird bewusst über POST /api/bank/connection/<id>/entsperren
    (nachdem die PIN im Online-Banking geprüft wurde)."""
    try:
        conns = _bsec.load_connections()
        c = conns.get(cid) or {}
        c["pin_gesperrt"] = True
        c["pin_gesperrt_am"] = datetime.now().isoformat(timespec="seconds")
        c["pin_gesperrt_grund"] = str(meldung)[:200]
        conns[cid] = c
        _bsec.save_connections(conns)
    except Exception as e:
        print(f"[bank-fints] Sperre konnte nicht gesetzt werden: {e}")


@app.route("/api/bank/connection/<cid>/entsperren", methods=["POST"])
def api_bank_connection_entsperren(cid):
    """PIN-Sperre bewusst aufheben — erst NACHDEM die PIN im Online-Banking geprüft wurde."""
    conns = _bsec.load_connections()
    c = conns.get(cid)
    if not c:
        return _json({"error": "Bankverbindung nicht gefunden"}, 400)
    for k in ("pin_gesperrt", "pin_gesperrt_am", "pin_gesperrt_grund"):
        c.pop(k, None)
    conns[cid] = c
    _bsec.save_connections(conns)
    return _json({"ok": True, "id": cid})


@app.route("/api/bank/sync/start", methods=["POST"])
def api_bank_sync_start():
    data = request.get_json(silent=True) or {}
    cid = (data.get("id") or "").strip()
    conn = _bsec.load_connections().get(cid) if cid else None
    if not conn:
        return _json({"error": "Bankverbindung nicht gefunden"}, 400)
    if conn.get("pin_gesperrt"):
        return _json({"status": "error", "error":
                      "Abruf gesperrt: Die Bank hat einen PIN-Fehler gemeldet ("
                      + str(conn.get("pin_gesperrt_am") or "") + "). Bitte die PIN zuerst im "
                      "Online-Banking prüfen — nach mehreren Fehlversuchen sperrt die Bank den Zugang. "
                      "Danach Verbindung entsperren."}, 423)
    conn = _bank_ensure_product_id(cid, conn)
    with _bank_dialog_mitschnitt(cid, "sync/start",
                                  voll=bool(conn.get("dialog_mitschnitt"))) as _cap:
      try:
        return _bank_run_step(cid, conn, _bank_initial_state(conn))
      except Exception as e:
        msg = str(e)
        detail = " · ".join(_cap[-6:])
        print(f"[bank-fints] sync/start Fehler ({cid}): {type(e).__name__}: {msg} || {detail}")
        if ((getattr(e, "pin_fehler", False) or type(e).__name__ == "FinTSClientPINError")
                and _bank_echter_pin_fehler(_cap)):
            _bank_pin_sperre_setzen(cid, msg)
            return _json({"status": "error", "error":
                          "Die Bank meldet einen PIN-Fehler. Der Abruf wurde GESPERRT, um eine "
                          "Kontosperre nach mehreren Fehlversuchen zu verhindern. Bitte die PIN im "
                          "Online-Banking prüfen, dann die Verbindung entsperren."
                          + (f" — Bank meldet: {detail}" if detail else "")}, 423)
        full = f"FinTS-Fehler: {msg}" + (f" — Bank meldet: {detail}" if detail else "")
        return _json({"status": "error", "error": full}, 502)


@app.route("/api/bank/log", methods=["GET"])
def api_bank_log():
    """Dialog-Protokoll der Bank-Abrufe (28.07.2026) — für die Sparkassen-Diagnose.
    Zeigt Stufe, TAN-Verfahren/-Medium, Bank-Rückmeldecodes (HIRMS) und Fehlerursachen.
    PIN/TAN sind im Protokoll nie enthalten (Redigierung schon beim Schreiben)."""
    import bank_fints as _bfints
    cid = (request.args.get("id") or "").strip() or None
    try:
        limit = max(1, min(500, int(request.args.get("limit") or 200)))
    except Exception:
        limit = 200
    return _json({"eintraege": _bfints.protokoll_lesen(cid, limit)})


@app.route("/api/bank/sync/poll", methods=["POST"])
def api_bank_sync_poll():
    data = request.get_json(silent=True) or {}
    token = data.get("token")
    entry = _BANK_SYNC_PENDING.get(token) if token else None
    if not entry:
        return _json({"status": "error", "error": "Sitzung abgelaufen — bitte Abruf neu starten."}, 410)
    cid = entry.get("conn_id")
    conn = _bsec.load_connections().get(cid)
    if not conn:
        return _json({"error": "Bankverbindung nicht gefunden"}, 400)
    with _bank_dialog_mitschnitt(cid, "sync/poll",
                                  voll=bool(conn.get("dialog_mitschnitt"))) as _cap:
      try:
        resp = _bank_run_step(cid, conn, entry["state"], tan=(data.get("tan") or "").strip() or None)
        # Token nach Abschluss (done/neuer tan_pending-Token) aufräumen
        _BANK_SYNC_PENDING.pop(token, None)
        return resp
      except Exception as e:
        _BANK_SYNC_PENDING.pop(token, None)
        _detail = " · ".join(_cap[-6:])
        print(f"[bank-fints] sync/poll Fehler: {type(e).__name__}: {e} || {_detail}")
        if ((getattr(e, "pin_fehler", False) or type(e).__name__ == "FinTSClientPINError")
                and _bank_echter_pin_fehler(_cap)):
            _bank_pin_sperre_setzen(cid, e)
            return _json({"status": "error", "error":
                          "Die Bank meldet einen PIN-Fehler. Der Abruf wurde GESPERRT, um eine "
                          "Kontosperre nach mehreren Fehlversuchen zu verhindern. Bitte PIN im "
                          "Online-Banking prüfen, dann Verbindung entsperren."
                          + (f" — Bank meldet: {_detail}" if _detail else "")}, 423)
        return _json({"status": "error", "error": f"FinTS-Fehler: {e}"
                       + (f" — Bank meldet: {_detail}" if _detail else "")}, 502)


@app.route("/api/external/bank-sync", methods=["POST"])
def api_external_bank_sync():
    """Headless FinTS-Abruf für den täglichen Cron — über ALLE Bankverbindungen.
    Nutzt den gespeicherten 90-Tage-State (TAN-frei). Verlangt eine Bank erneut SCA
    (tan_pending), wird NICHT weiter gepusht → status 'sca_required' (Hinweis an den Betreiber)."""
    expected = _load_external_api_key()
    provided = request.headers.get("X-API-Key", "")
    if not expected or provided != expected:
        return _json({"error": "Ungültiger oder fehlender API-Key"}, 401)
    import bank_fints as _bfints
    conns = _bsec.load_connections()
    if not conns:
        return _json({"status": "no_connection", "results": []})
    results = []
    for cid, conn in conns.items():
        bank = conn.get("bank_name") or cid
        if conn.get("pin_gesperrt"):
            # Nach einem PIN-Fehler NIEMALS automatisch weiterprobieren (Kontosperre!)
            results.append({"bank": bank, "status": "pin_gesperrt",
                            "seit": conn.get("pin_gesperrt_am")})
            continue
        if not conn.get("last_sync"):
            # Noch nie erfolgreich abgerufen: nicht unbeaufsichtigt probieren. Banken
            # sperren den Zugang nach wenigen Fehlversuchen — solche Versuche gehoeren
            # an den Schreibtisch, wo die pushTAN-Freigabe auch jemand bestaetigen kann.
            # Nach dem ersten erfolgreichen Abruf laeuft die Bank automatisch mit.
            results.append({"bank": bank, "status": "erstabruf_manuell",
                            "hinweis": "Erster Abruf bitte manuell in der App"})
            continue
        try:
            conn = _bank_ensure_product_id(cid, conn)
            pin = _bsec.decrypt(conn["pin_enc"])
            status, state, public = _bfints.step(conn, pin, _bank_initial_state(conn))
            del pin
            if status == "tan_pending":
                results.append({"bank": bank, "status": "sca_required"})
            else:
                fin = _bank_finalize(cid, state, public)
                results.append({"bank": bank, "status": "done",
                                "imported": fin.get("imported", 0),
                                "duplicates": fin.get("duplicates", 0),
                                "auto_matched_high": fin.get("auto_matched_high", 0),
                                "balance": fin.get("balance")})
        except Exception as e:
            print(f"[bank-fints] auto-sync {cid} Fehler: {type(e).__name__}: {e}")
            results.append({"bank": bank, "status": "error", "error": str(e)})
    return _json({"status": "done", "results": results})


@app.route("/api/bank/dedupe", methods=["POST"])
def api_bank_dedupe():
    """Entfernt quellenübergreifende Doppel-Einträge (CAMT-Upload ↔ FinTS)."""
    return _json({"ok": True, **_bank_dedupe_existing()})


# ── API: Buchhaltung (Modul, liest aus Postgres-Schema buch) ───────────
@app.route("/api/buch/auswertung")
def api_buch_auswertung():
    try:
        import buch_engine
        data = buch_engine.auswertung()
        if data is None:
            return _json({"error": "Buchhaltungs-DB nicht erreichbar"}, 503)
        return _json(data)
    except Exception as e:
        return _json({"error": str(e)}, 500)


@app.route("/api/buch/transaktionen")
def api_buch_transaktionen():
    try:
        import buch_engine
        status = request.args.get("status") or None
        return _json({"buchungen": buch_engine.liste_buchungen(status=status)})
    except Exception as e:
        return _json({"error": str(e)}, 500)


@app.route("/api/buch/raw")
def api_buch_raw():
    """Liefert alle Buchungen im txData-Format für die Cashflow-Analyse-App (14 Tabs)."""
    try:
        import buch_engine
        return _json({"txData": buch_engine.raw_transaktionen()})
    except Exception as e:
        return _json({"error": str(e), "txData": []}, 500)


# ── Alt-Rechnungen (PDF) den Eingangsbeträgen zuordnen ────────────────────
# Rechnungen VOR dem 01.06.2026 existieren nur als PDF/Excel. Sie werden hier
# hochgeladen (PDF bleibt für den Steuerberater erhalten) und dem passenden
# Bank-Eingang in buch.transaktion zugeordnet, damit jede Einnahme einen Beleg hat.
ALTRECHNUNG_DIR = _BASE / "data" / "altrechnungen"
_AUSGENOMMEN_PFAD = _BASE / "data" / "altrechnungen_ausgenommen.json"


def _ausgenommen_set():
    """Dateinamen, die aus der Arbeitsliste rausgenommen wurden (PDFs bleiben erhalten)."""
    if _AUSGENOMMEN_PFAD.exists():
        try:
            return set(json.loads(_AUSGENOMMEN_PFAD.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def _ausgenommen_save(s):
    _AUSGENOMMEN_PFAD.write_text(json.dumps(sorted(s), ensure_ascii=False, indent=2), encoding="utf-8")


def _name_tokens(s):
    import re as _re
    return set(t for t in _re.sub(r"[^a-zäöüß0-9 ]", " ", (s or "").lower()).split() if len(t) > 2)


def _name_score(a, b):
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return 0.0
    return round(len(ta & tb) / max(1, min(len(ta), len(tb))), 2)


def _altrechnung_kandidaten(cur, betrag_pdf, name):
    """Bank-Eingänge (credit) ohne zugeordnete PDF-Rechnung, deren Betrag zum PDF-Betrag
    passt — netto/brutto-bewusst (PDF kann netto ODER brutto sein): geprüft werden der
    Betrag selbst sowie ×/÷ 1,19 (19 %) und 1,07 (7 %). Sortiert nach Namens-Score.
    Ausgeschlossen: bereits zugeordnete, Charlie Manz >1000 € (storniert), S-Storno-Referenzen."""
    ziele = set()
    for faktor in (1.0, 1.19, 1.07, 1 / 1.19, 1 / 1.07):
        ziele.add(round(betrag_pdf * faktor, 2))
    conds = " OR ".join(["betrag BETWEEN %s AND %s"] * len(ziele))
    params = []
    for z in sorted(ziele):
        params += [z - 0.01, z + 0.01]
    cur.execute(f"""
        SELECT transaktion_id, datum, betrag, name, zweck
        FROM buch.transaktion
        WHERE betrag > 0 AND status <> 'intern'
          AND ({conds})
          AND (roh_json->>'pdf_rechnung') IS NULL          AND zweck !~* '(^|[^A-Za-z])S-[A-Za-zÄÖÜäöüß]'
        ORDER BY datum DESC
    """, params)
    out = []
    for tid, datum, betrag, tname, zweck in cur.fetchall():
        out.append({"transaktion_id": tid, "datum": str(datum), "betrag": float(betrag),
                    "name": tname or "", "zweck": (zweck or "")[:60],
                    "score": _name_score(name, (tname or "") + " " + (zweck or ""))})
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def _de_amount(s):
    try:
        return float(str(s).replace(".", "").replace(",", "."))
    except Exception:
        return None


def _altrechnung_extract(data, fname):
    """Beste-Mühe-Extraktion aus einem Rechnungs-PDF: zuerst der spezielle
    Energieberatungs-Parser (eigene alte Rechnungen), sonst generisch
    (Betrag bei Gesamt/Brutto/total, sonst größter €-Betrag; Rechnungsnr-Muster)."""
    import tempfile
    import re as _re
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
            tmp.write(data)
            tmp.flush()
            inv = parse_energieberatung_pdf(tmp.name)
        return {"nummer": inv.invoice_number, "brutto": float(inv.tax_inclusive_amount()),
                "kunde": inv.buyer.name, "quelle": "parser"}
    except Exception:
        pass
    try:
        text, _pages = _extract_pdf_text(data)
    except Exception:
        text = ""
    text = text or ""
    brutto = None
    m = _re.findall(r"(?:Gesamt|Brutto|Rechnungsbetrag|Zahlbetrag|total|Summe)[^\d]{0,20}([\d.]+,\d{2})",
                    text, _re.IGNORECASE)
    if m:
        brutto = _de_amount(m[-1])
    if brutto is None:
        alle = [a for a in (_de_amount(x) for x in _re.findall(r"([\d.]+,\d{2})\s*€", text)) if a]
        brutto = max(alle) if alle else None
    if brutto is None:
        return None
    # Rechnungsnummer: Token nach "Rechnung..." das mindestens eine Ziffer enthält
    mnr = _re.search(r"Rechnung\S*\.?\s*(?:Nr\.?|Nummer)?\s*:?\.?\s*"
                     r"([A-Za-z0-9][A-Za-z0-9\-/]*\d[A-Za-z0-9\-/]*)", text)
    nummer = mnr.group(1) if mnr else _re.sub(r"\.pdf$", "", fname, flags=_re.IGNORECASE)
    return {"nummer": nummer, "brutto": round(brutto, 2), "kunde": "", "quelle": "generisch"}


def _altrechnung_kandidaten_name(cur, suchname):
    """Eingänge (credit, unzugeordnet) deren Name/Zweck den Kundennamen enthält — für
    manuelle Zuordnung, wenn der Betrag abweicht. Gleiche Ausschlüsse wie Betrags-Suche."""
    suchname = (suchname or "").strip()
    if len(suchname) < 3:
        return []
    like = "%" + suchname + "%"
    cur.execute("""
        SELECT transaktion_id, datum, betrag, name, zweck
        FROM buch.transaktion
        WHERE betrag > 0 AND status <> 'intern'
          AND (roh_json->>'pdf_rechnung') IS NULL          AND zweck !~* '(^|[^A-Za-z])S-[A-Za-zÄÖÜäöüß]'
          AND (name ILIKE %s OR zweck ILIKE %s)
        ORDER BY datum DESC LIMIT 12
    """, (like, like))
    return [{"transaktion_id": r[0], "datum": str(r[1]), "betrag": float(r[2]),
             "name": r[3] or "", "zweck": (r[4] or "")[:60], "score": 0.4} for r in cur.fetchall()]


def _altrechnung_buchen(cur, tid, nummer, datei):
    """Verbucht den Eingang als Einnahme (F/4000) und hängt die PDF-Rechnung an."""
    import buch_engine
    cur.execute("SELECT betrag, mwst FROM buch.transaktion WHERE transaktion_id=%s", (tid,))
    row = cur.fetchone()
    if not row:
        return False
    betrag = float(row[0] or 0)
    mwst = float(row[1]) if row[1] is not None else 19.0
    cur.execute("""
        UPDATE buch.transaktion
        SET tag='F', konto='4000', mwst=%s, ust_betrag=%s, netto_betrag=%s,
            status='gebucht', manuell=true, regel_treffer='altrechnung-pdf',
            roh_json = coalesce(roh_json,'{}'::jsonb) || %s::jsonb
        WHERE transaktion_id=%s
    """, (mwst, buch_engine.vat_of(betrag, mwst), buch_engine.netto_of(betrag, mwst),
          json.dumps({"pdf_rechnung": nummer, "pdf_datei": datei}), tid))
    return cur.rowcount > 0


def _altrechnung_ins_register(pdf_bytes, dateiname):
    """Legt eine Alt-Ausgangsrechnung im Rechnungsregister an (Kennzeichen Altbestand).

    Ohne Registereintrag findet der Bank-Zuordner die Rechnung nie — die Zahlungen
    nennen ihre Nummer im Verwendungszweck, aber es gab nichts zum Verknuepfen
    (01.09.2026). Rueckgabe: (invoice|None, hinweis).
    """
    res = _ocr_extract_ausgang(pdf_bytes)
    if res.get("error"):
        return None, res["error"]
    d = res.get("extracted") or {}
    nummer = (d.get("invoice_number") or "").strip()
    if not nummer:
        return None, "Rechnungsnummer nicht erkannt"

    # Dublette: dieselbe Nummer ist schon im Register
    vorhanden = next((i for i in invoices.values()
                      if (getattr(i, "invoice_number", "") or "").strip().lower()
                      == nummer.lower()), None)
    if vorhanden is not None:
        return vorhanden, "bereits im Register"

    ms = _load_mandant_settings()
    try:
        inv_datum = date.fromisoformat((d.get("invoice_date") or "")[:10])
    except Exception:
        inv_datum = None
    if inv_datum is None:
        m = re.search(r"(20\d{2})(\d{2})(\d{2})", nummer + " " + (dateiname or ""))
        inv_datum = (date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                     if m else date.today())

    faellig = None
    try:
        faellig = date.fromisoformat((d.get("due_date") or "")[:10])
    except Exception:
        pass

    inv = Invoice(
        invoice_number=nummer,
        invoice_date=inv_datum,
        invoice_type_code="380",
        currency_code=(d.get("currency") or "EUR")[:3],
        buyer_reference=(d.get("buyer_name") or "")[:60],
        note=(d.get("note") or "Alt-Ausgangsrechnung, per Texterkennung erfasst")[:300],
        seller=Seller(
            name=ms.get("name", ""),
            address=Address(street=ms.get("street", ""), house_number=ms.get("house_number", ""),
                            city=ms.get("city", ""), post_code=ms.get("post_code", "")),
            electronic_address=ms.get("email", ""), electronic_address_scheme="EM",
            contact=Contact(ms.get("contact_name", ""), ms.get("contact_phone", ""), ms.get("email", "")),
            vat_id=ms.get("vat_id", ""), tax_registration_id=ms.get("tax_registration_id", "")),
        buyer=Buyer(
            name=(d.get("buyer_name") or "Unbekannter Kunde").strip(),
            address=Address(street=(d.get("buyer_street") or ""),
                            house_number=(d.get("buyer_house_number") or ""),
                            city=(d.get("buyer_city") or ""),
                            post_code=(d.get("buyer_postcode") or "")),
            electronic_address=(d.get("buyer_email") or "kunde@example.com"),
            electronic_address_scheme="EM",
            vat_id=(d.get("buyer_vat_id") or "")),
        payment=PaymentInfo(means_code="58", iban=ms.get("iban", ""),
                            payment_terms=(d.get("payment_terms") or ""), due_date=faellig),
    )

    zeilen = d.get("lines") or []
    if not zeilen:
        netto = d.get("net_amount")
        satz = d.get("tax_rate")
        brutto = d.get("gross_amount")
        if netto is None and brutto is not None:
            satz = satz if satz is not None else 19
            netto = round(float(brutto) / (1 + float(satz) / 100.0), 2)
        if netto is None:
            return None, "Betrag nicht erkannt"
        zeilen = [{"description": "Leistung laut Rechnung", "quantity": 1,
                   "unit_price": netto, "net_amount": netto,
                   "tax_rate": satz if satz is not None else 19}]
    for i, z in enumerate(zeilen, 1):
        netto_z = z.get("net_amount")
        if netto_z is None:
            netto_z = float(z.get("quantity") or 1) * float(z.get("unit_price") or 0)
        inv.lines.append(InvoiceLine(
            line_id=str(i), quantity=Decimal(str(z.get("quantity") or 1)),
            item_name=(z.get("description") or "Leistung")[:200],
            unit_price=Decimal(str(z.get("unit_price") or netto_z or 0)),
            line_net_amount=Decimal(str(netto_z or 0)),
            tax_rate=Decimal(str(z.get("tax_rate") if z.get("tax_rate") is not None else 19))))

    inv._direction = "AUSGANG"
    inv._altbestand = True
    inv._source_file = dateiname or ""
    inv.status = "FREIGEGEBEN"      # nicht BEZAHLT: sonst verknuepft der Zuordner nie automatisch
    inv.add_audit("ALTBESTAND_ERFASST", user="system",
                  comment=f"Alt-Ausgangsrechnung aus {dateiname} per Texterkennung erfasst")
    report = validate_invoice(inv)
    try:
        xml_bytes = generate_and_serialize(inv)
    except Exception:
        xml_bytes = b""
    invoices[inv._id] = inv
    try:
        inbox.duplicates.register(inv, inv._id)
    except Exception:
        pass
    _archive_ausgang(inv, xml_bytes, report, pdf_bytes=pdf_bytes)
    auto_save()
    return inv, "angelegt"


@app.route("/api/buch/altrechnung", methods=["POST"])
def api_buch_altrechnung():
    """Lädt alte Ausgangsrechnungs-PDFs hoch, speichert sie (Steuerberater),
    parst Nr/Betrag und ordnet sie dem passenden Eingangsbetrag zu (Auto bei eindeutig)."""
    import db_pg, re as _re
    files = request.files.getlist("files")
    if not files and "file" in request.files:
        files = [request.files["file"]]
    if not files:
        return _json({"error": "Keine Datei hochgeladen"}, 400)
    ALTRECHNUNG_DIR.mkdir(parents=True, exist_ok=True)
    conn = db_pg._connect()
    if not conn:
        return _json({"error": "Buchhaltungs-DB nicht erreichbar"}, 503)
    ergebnisse = []
    try:
        with conn.cursor() as cur:
            for f in files:
                fname = f.filename or "rechnung.pdf"
                data = f.read()
                res = {"datei": fname}
                erk = _altrechnung_extract(data, fname)
                if not erk:
                    res["status"] = "fehler"
                    res["fehler"] = "Betrag im PDF nicht erkannt"
                    ergebnisse.append(res); continue
                nummer, brutto, kunde = erk["nummer"], erk["brutto"], erk["kunde"]
                res["erkannt"] = {"nummer": nummer, "brutto": round(brutto, 2),
                                  "kunde": kunde, "quelle": erk["quelle"]}
                safe = _re.sub(r"[^A-Za-z0-9._-]", "_", nummer or fname)
                pfad = ALTRECHNUNG_DIR / (safe + ".pdf")
                pfad.write_bytes(data)
                res["gespeichert"] = pfad.name
                kand = _altrechnung_kandidaten(cur, brutto, kunde)
                auto = None
                if len(kand) == 1:
                    auto = kand[0]
                elif len(kand) >= 2 and kand[0]["score"] >= 0.5 and kand[0]["score"] > kand[1]["score"]:
                    auto = kand[0]
                if auto:
                    _altrechnung_buchen(cur, auto["transaktion_id"], nummer, pfad.name)
                    res["status"] = "gebucht"; res["zuordnung"] = auto
                elif kand:
                    res["status"] = "mehrere"; res["kandidaten"] = kand[:8]
                else:
                    res["status"] = "keiner"
                # Zusaetzlich ins Rechnungsregister, damit der Bank-Zuordner die
                # Zahlung finden kann (die Verwendungszwecke nennen die Nummer).
                try:
                    inv_neu, hinweis = _altrechnung_ins_register(data, fname)
                    res["register"] = hinweis
                    if inv_neu is not None:
                        res["register_nummer"] = inv_neu.invoice_number
                except Exception as e:
                    res["register"] = f"Fehler: {e}"
                ergebnisse.append(res)
            conn.commit()
        return _json({"ergebnisse": ergebnisse})
    finally:
        conn.close()


@app.route("/api/buch/altrechnung/register-nachtragen", methods=["POST"])
def api_altrechnung_register_nachtragen():
    """Traegt bereits hochgeladene Alt-Rechnungs-PDFs ins Rechnungsregister nach.

    Die PDFs liegen laengst unter data/altrechnungen — sie noch einmal durch den
    Browser zu schicken ist unnoetig und bei 123 Dateien fehleranfaellig. Arbeitet
    portionsweise (limit), damit keine Anfrage in eine Zeitgrenze laeuft, und meldet,
    wie viele noch offen sind (01.09.2026).
    """
    daten = request.get_json(silent=True) or {}
    try:
        limit = max(1, min(50, int(daten.get("limit") or 10)))
    except Exception:
        limit = 10
    import re as _re

    def _norm(x):
        return _re.sub(r"[^a-z0-9]", "", (x or "").lower())

    # Vergleich ohne Trennzeichen: die Dateien heissen mal „R-Bguila-_20251114-01",
    # die Rechnung darin „R-BGUILA-20251114-01" — mit blossem Kleinschreiben trifft
    # sich das nie, und der Nachtrag hat dieselbe PDF bei jedem Lauf erneut durch die
    # Texterkennung geschickt (01.09.2026). Zusaetzlich zaehlt die Quelldatei.
    vorhandene = {_norm(getattr(i, "invoice_number", "")) for i in invoices.values()}
    vorhandene |= {_norm(os.path.splitext(getattr(i, "_source_file", "") or "")[0])
                   for i in invoices.values()}
    vorhandene.discard("")
    dateien = sorted(ALTRECHNUNG_DIR.glob("*.pdf"))
    offen_gesamt = 0
    ergebnisse = []
    for pfad in dateien:
        if _norm(pfad.stem) in vorhandene:
            continue
        offen_gesamt += 1
        if len(ergebnisse) >= limit:
            continue
        try:
            inv_neu, hinweis = _altrechnung_ins_register(pfad.read_bytes(), pfad.name)
            ergebnisse.append({"datei": pfad.name, "status": hinweis,
                                "nummer": getattr(inv_neu, "invoice_number", None)})
            if inv_neu is not None:
                vorhandene.add(_norm(inv_neu.invoice_number))
                vorhandene.add(_norm(pfad.stem))
        except Exception as e:
            ergebnisse.append({"datei": pfad.name, "status": f"Fehler: {e}"})
    angelegt = sum(1 for e in ergebnisse if e["status"] == "angelegt")
    return _json({"ok": True, "verarbeitet": len(ergebnisse), "angelegt": angelegt,
                   "offen": max(0, offen_gesamt - len(ergebnisse)),
                   "gesamt_dateien": len(dateien), "ergebnisse": ergebnisse})


@app.route("/api/buch/altrechnung/kandidaten")
def api_buch_altrechnung_kandidaten():
    """Passende Eingangsbeträge für eine bereits hochgeladene Rechnung (zum manuellen Zuordnen
    aus der Arbeitsliste). Parst das PDF erneut und sucht netto/brutto-bewusst."""
    import db_pg, os
    datei = os.path.basename(request.args.get("datei", ""))
    p = ALTRECHNUNG_DIR / datei
    if not datei or not p.exists():
        return _json({"error": "PDF nicht gefunden"}, 404)
    erk = _altrechnung_extract(p.read_bytes(), datei)
    if not erk:
        return _json({"erkannt": None, "kandidaten": [], "hinweis": "Betrag im PDF nicht erkannt"})
    import re as _re
    conn = db_pg._connect()
    if not conn:
        return _json({"error": "Buchhaltungs-DB nicht erreichbar"}, 503)
    try:
        with conn.cursor() as cur:
            such = (erk["kunde"] or "") + " " + (erk["nummer"] or "")
            # Namens-Treffer (gleicher Kunde aus R-<Name>-Datum) ZUERST — relevanter als
            # reine Betragstreffer; danach Betragstreffer (netto/brutto) als Ergänzung.
            kand = []
            seen = set()
            m = _re.match(r"^[RS]-\s*([A-Za-zÄÖÜäöüß]{3,})", erk["nummer"] or "")
            if m:
                for k in _altrechnung_kandidaten_name(cur, m.group(1)):
                    if k["transaktion_id"] not in seen:
                        kand.append(k); seen.add(k["transaktion_id"])
            for k in _altrechnung_kandidaten(cur, erk["brutto"], such):
                if k["transaktion_id"] not in seen:
                    kand.append(k); seen.add(k["transaktion_id"])
    finally:
        conn.close()
    return _json({"erkannt": {"nummer": erk["nummer"], "brutto": round(erk["brutto"], 2),
                              "kunde": erk["kunde"]}, "kandidaten": kand[:15]})


@app.route("/api/buch/altrechnung/liste")
def api_buch_altrechnung_liste():
    """Arbeitsliste aller hochgeladenen Ausgangsrechnungs-PDFs mit Zuordnungsstatus.
    Offene (noch keinem Eingang zugeordnet) zuerst — bleibt bestehen bis alles erledigt ist."""
    import db_pg, re as _re
    pdfs = sorted(ALTRECHNUNG_DIR.glob("*.pdf")) if ALTRECHNUNG_DIR.exists() else []
    zuord = {}
    conn = db_pg._connect()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT roh_json->>'pdf_datei', datum, betrag, name, "
                            "roh_json->>'pdf_rechnung' FROM buch.transaktion "
                            "WHERE roh_json->>'pdf_datei' IS NOT NULL")
                for datei, datum, betrag, name, nr in cur.fetchall():
                    zuord[datei] = {"datum": str(datum), "betrag": float(betrag),
                                    "name": name or "", "nummer": nr}
        finally:
            conn.close()
    ausg = _ausgenommen_set()
    items = []
    for p in pdfs:
        z = zuord.get(p.name)
        items.append({"datei": p.name, "nummer": _re.sub(r"\.pdf$", "", p.name, flags=_re.I),
                      "zugeordnet": bool(z), "buchung": z, "ausgenommen": p.name in ausg})
    # Reihenfolge: offene zuerst, dann zugeordnete, dann ausgenommene ganz unten
    items.sort(key=lambda x: (x["zugeordnet"] or x["ausgenommen"], x["ausgenommen"], x["nummer"].lower()))
    offen = sum(1 for i in items if not i["zugeordnet"] and not i["ausgenommen"])
    return _json({"items": items, "gesamt": len(items), "offen": offen,
                  "ausgenommen": sum(1 for i in items if i["ausgenommen"]),
                  "zugeordnet": sum(1 for i in items if i["zugeordnet"])})


@app.route("/api/buch/altrechnung/ausnehmen", methods=["POST"])
def api_buch_altrechnung_ausnehmen():
    """Nimmt eine Rechnung aus der Arbeitsliste raus (oder wieder rein). PDF bleibt erhalten."""
    import os
    b = request.get_json(force=True, silent=True) or {}
    datei = os.path.basename(b.get("datei", ""))
    if not datei:
        return _json({"error": "datei fehlt"}, 400)
    s = _ausgenommen_set()
    if b.get("raus", True):
        s.add(datei)
    else:
        s.discard(datei)
    _ausgenommen_save(s)
    return _json({"ok": True, "ausgenommen": datei in s})


@app.route("/api/buch/altrechnung/pdf/<path:datei>")
def api_buch_altrechnung_pdf(datei):
    """Liefert ein zugeordnetes Alt-Rechnungs-PDF zur Anzeige im Browser."""
    import os
    safe = os.path.basename(datei)  # kein Pfad-Ausbruch
    p = ALTRECHNUNG_DIR / safe
    if not p.exists() or p.suffix.lower() != ".pdf":
        return _json({"error": "PDF nicht gefunden"}, 404)
    return Response(p.read_bytes(), mimetype="application/pdf",
                    headers={"Content-Disposition": "inline; filename=" + safe})


@app.route("/api/buch/altrechnungen/zip")
def api_buch_altrechnungen_zip():
    """Lädt alle gespeicherten Alt-Rechnungs-PDFs als ZIP herunter (für den Steuerberater)."""
    import io, zipfile
    if not ALTRECHNUNG_DIR.exists():
        return _json({"error": "Noch keine Alt-Rechnungen hochgeladen"}, 404)
    pdfs = sorted(ALTRECHNUNG_DIR.glob("*.pdf"))
    if not pdfs:
        return _json({"error": "Noch keine Alt-Rechnungen hochgeladen"}, 404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in pdfs:
            z.write(p, p.name)
    buf.seek(0)
    return Response(buf.read(), mimetype="application/zip",
                    headers={"Content-Disposition": "attachment; filename=alt-rechnungen.zip"})


@app.route("/api/buch/altrechnung/confirm", methods=["POST"])
def api_buch_altrechnung_confirm():
    """Ordnet eine PDF-Rechnung manuell einem gewählten Eingangsbetrag zu."""
    import db_pg
    b = request.get_json(force=True, silent=True) or {}
    tid = b.get("transaktion_id"); nummer = b.get("nummer")
    if not (tid and nummer):
        return _json({"error": "transaktion_id/nummer fehlt"}, 400)
    conn = db_pg._connect()
    if not conn:
        return _json({"error": "Buchhaltungs-DB nicht erreichbar"}, 503)
    try:
        with conn.cursor() as cur:
            ok = _altrechnung_buchen(cur, tid, nummer, b.get("datei") or "")
        conn.commit()
        return _json({"ok": ok})
    finally:
        conn.close()


# ── Belegprüfung: hat jede Buchung ein Dokument? (31.07.2026) ──────────
# Logik in buch_engine.beleg_map() — dort eigenständig testbar, ohne webapp zu laden
# (ein webapp-Import im Zweitprozess löst auto_save aus und leert invoices.json).


@app.route("/api/buch/belege")
def api_buch_belege():
    """Belegprüfung für die Buchungen-Liste: welche Buchung hat ein Dokument?"""
    try:
        import buch_engine
        return _json(buch_engine.beleg_map(str(_BASE / "data")))
    except Exception as e:
        return _json({"error": str(e), "belege": {}, "statistik": {}}, 500)


@app.route("/api/buch/beleg/<rid>/anlagen")
def api_buch_beleg_anlagen(rid):
    """Die WEITEREN Dokumente einer Rechnung — Zahlungsbelege, Nachweise, Anhaenge.

    Zu einer Rechnung gehoert oft mehr als das eine Druckstueck; die Buchhaltung
    soll alles zeigen, was zum Beleg gehoert (07.09.2026).
    """
    try:
        liste = archive.anlagen(rid)
    except Exception as e:
        return _json({"error": str(e), "anlagen": []}, 500)
    return _json({"rechnung_id": rid, "anzahl": len(liste),
                  "anlagen": [{"datei": a.get("datei"), "rolle": a.get("rolle"),
                               "groesse": a.get("groesse"),
                               "hinzugefuegt_am": a.get("hinzugefuegt_am"),
                               "url": f"/api/buch/beleg/{rid}/anlage/{a.get('datei')}"}
                              for a in liste]})


@app.route("/api/buch/beleg/<rid>/anlage/<path:name>")
def api_buch_beleg_anlage(rid, name):
    """Eine einzelne Anlage ausliefern. Der Name muss aus dem Verzeichnis stammen —
    kein Pfad von aussen, sonst waere das Archiv ueber ../ auslesbar."""
    from flask import send_file
    liste = archive.anlagen(rid)
    treffer = next((a for a in liste if a.get("datei") == name), None)
    if not treffer:
        return _json({"error": "Anlage nicht gefunden"}, 404)
    pfad = archive.root / rid / "anlagen" / treffer["datei"]   # Wurzel vom Archiv selbst, nicht geraten
    if not pfad.exists():
        return _json({"error": "Datei fehlt im Archiv"}, 404)
    art = "application/pdf" if pfad.suffix.lower() == ".pdf" else "application/octet-stream"
    return send_file(str(pfad), mimetype=art, as_attachment=False, download_name=pfad.name)


@app.route("/api/buch/beleg/<rid>")
def api_buch_beleg_datei(rid):
    """Beleg zu einer Rechnung anzeigen — als PDF, damit man ihn wirklich lesen kann.

    Eingangsrechnungen liegen mit Original-PDF im Archiv. AUSGANGSRECHNUNGEN
    liegen dort nur als XRechnung-XML (die ist der GoBD-Originalbeleg, der
    Browser zeigt sie aber nicht an) — für die wird das PDF aus der Rechnung
    erzeugt, genau wie beim Versand. ?original=1 liefert immer die archivierte
    Originaldatei.
    """
    from flask import send_file
    import buch_engine
    pfad, art = buch_engine.archiv_beleg(str(_BASE / "data"), rid)

    if request.args.get("original") in ("1", "true", "yes"):
        if not pfad:
            return _json({"error": "Kein archivierter Beleg zu dieser Rechnung"}, 404)
        return send_file(str(pfad),
                         mimetype="application/pdf" if art == "pdf" else "application/xml",
                         as_attachment=False, download_name=pfad.name)

    if pfad and art == "pdf":
        return send_file(str(pfad), mimetype="application/pdf",
                         as_attachment=False, download_name=pfad.name)

    inv = invoices.get(rid)
    if inv:
        try:
            filename, pdf_bytes = _build_invoice_pdf(inv)
            return Response(pdf_bytes, mimetype="application/pdf",
                            headers={"Content-Disposition": f'inline; filename="{filename}"'})
        except Exception as e:
            print(f"[beleg] PDF-Erzeugung fehlgeschlagen für {rid}: {e}", flush=True)

    if pfad:   # letzter Ausweg: die XML ausliefern
        return send_file(str(pfad), mimetype="application/xml",
                         as_attachment=False, download_name=pfad.name)
    return _json({"error": "Kein Beleg zu dieser Rechnung"}, 404)


@app.route("/api/buch/retag", methods=["POST"])
def api_buch_retag():
    """Schreibt eine manuelle Zuordnung (Entität/SKR03-Konto/MwSt/intern) aus der
    Cashflow-App zurück nach Postgres."""
    try:
        import buch_engine
        b = request.get_json(force=True, silent=True) or {}
        tid = b.get("id")
        if not tid:
            return _json({"error": "id fehlt"}, 400)
        n = buch_engine.retag(tid, tag=b.get("tag"), kto=b.get("kto"),
                              mwst=b.get("mwst"), intern=b.get("intern"))
        if n is None:
            return _json({"error": "Buchhaltungs-DB nicht erreichbar"}, 503)
        return _json({"ok": True, "updated": n})
    except Exception as e:
        return _json({"error": str(e)}, 500)


@app.route("/api/buch/regeln", methods=["GET", "POST", "DELETE"])
def api_buch_regeln():
    """Kategorisierungsregeln (buch.regel) für den Regel-Editor der Cashflow-App.
    GET → Liste; POST {key,tag,kto?,mwst?,front?} → upsert; DELETE {key} → löschen."""
    try:
        import buch_engine
        if request.method == "GET":
            return _json({"regeln": buch_engine.liste_regeln()})
        b = request.get_json(force=True, silent=True) or {}
        key = b.get("key")
        if not key:
            return _json({"error": "key fehlt"}, 400)
        if request.method == "DELETE":
            n = buch_engine.delete_regel(key)
            if n is None:
                return _json({"error": "Buchhaltungs-DB nicht erreichbar"}, 503)
            return _json({"ok": True, "deleted": n})
        if not b.get("tag"):
            return _json({"error": "tag fehlt"}, 400)
        ok = buch_engine.upsert_regel(key, b.get("tag"), kto=b.get("kto"),
                                      mwst=b.get("mwst"), front=bool(b.get("front")))
        if ok is None:
            return _json({"error": "Buchhaltungs-DB nicht erreichbar"}, 503)
        return _json({"ok": True})
    except Exception as e:
        return _json({"error": str(e)}, 500)


@app.route("/buchhaltung-app")
def buchhaltung_app():
    """Die vollständige Cashflow-/Buchhaltungs-App (14 Analyse-Tabs), gespeist aus Postgres.
    Liegt unter data/ (gitignored, da personenbezogen) — nicht im öffentlichen Repo."""
    app_file = _BASE / "data" / "buchhaltung_app.html"
    if not app_file.exists():
        return Response("Buchhaltungs-App nicht installiert.", status=404, mimetype="text/plain")
    return Response(app_file.read_text(encoding="utf-8"), mimetype="text/html")


@app.route("/api/buch/buchen", methods=["POST"])
def api_buch_buchen():
    """Verbucht alle Zahlungseingänge/Umsätze neu (idempotent)."""
    try:
        import buch_engine
        buch_engine.seed_regeln()
        buch_engine.buche_alle()
        data = buch_engine.auswertung()
        return _json({"ok": True, "auswertung": data})
    except Exception as e:
        return _json({"error": str(e)}, 500)


@app.route("/api/buch/forecast", methods=["GET", "POST", "DELETE"])
def api_buch_forecast():
    """Plan-Positionen für die Liquiditätsbetrachtung (Tab „Liquidität").
    GET → Liste; POST {typ,bezeichnung,betrag,rhythmus,start_monat,ende_monat?,notiz?,id?}
    legt an bzw. aktualisiert (id gesetzt); DELETE {id} löscht."""
    import db_pg
    conn = db_pg._connect()
    if conn is None:
        return _json({"error": "Postgres nicht verfügbar"}, 503)
    try:
        if request.method == "GET":
            with conn.cursor() as cur:
                cur.execute("SELECT id,typ,bezeichnung,betrag,rhythmus,start_monat,ende_monat,notiz,aktiv "
                            "FROM buch.forecast ORDER BY typ, start_monat, id")
                rows = [{"id": r[0], "typ": r[1], "bezeichnung": r[2], "betrag": float(r[3]),
                         "rhythmus": r[4], "start_monat": r[5].isoformat() if r[5] else None,
                         "ende_monat": r[6].isoformat() if r[6] else None,
                         "notiz": r[7] or "", "aktiv": bool(r[8])} for r in cur.fetchall()]
            return _json({"ok": True, "items": rows})

        d = request.get_json(silent=True) or {}
        if request.method == "DELETE":
            with conn.cursor() as cur:
                cur.execute("DELETE FROM buch.forecast WHERE id=%s", (int(d.get("id") or 0),))
                n = cur.rowcount
            conn.commit()
            return _json({"ok": True, "deleted": n})

        # POST — anlegen/ändern
        typ = (d.get("typ") or "").strip()
        if typ not in ("einnahme", "ausgabe", "saldo"):
            return _json({"error": "typ muss einnahme/ausgabe/saldo sein"}, 400)
        bez = (d.get("bezeichnung") or "").strip()
        if not bez:
            return _json({"error": "bezeichnung erforderlich"}, 400)
        try:
            betrag = round(float(d.get("betrag")), 2)
        except Exception:
            return _json({"error": "betrag ungültig"}, 400)
        rhythmus = (d.get("rhythmus") or "monatlich").strip()
        if rhythmus not in ("einmalig", "monatlich", "quartal", "halbjahr", "jahr"):
            return _json({"error": "rhythmus ungültig"}, 400)
        sm = (d.get("start_monat") or "")[:7]           # 'YYYY-MM'
        if not sm or len(sm) != 7:
            return _json({"error": "start_monat (JJJJ-MM) erforderlich"}, 400)
        start_monat = sm + "-01"
        em = (d.get("ende_monat") or "")[:7]
        ende_monat = (em + "-01") if em else None
        notiz = (d.get("notiz") or "").strip()
        aktiv = bool(d.get("aktiv", True))
        with conn.cursor() as cur:
            if d.get("id"):
                cur.execute("UPDATE buch.forecast SET typ=%s,bezeichnung=%s,betrag=%s,rhythmus=%s,"
                            "start_monat=%s,ende_monat=%s,notiz=%s,aktiv=%s WHERE id=%s RETURNING id",
                            (typ, bez, betrag, rhythmus, start_monat, ende_monat, notiz, aktiv, int(d["id"])))
            else:
                cur.execute("INSERT INTO buch.forecast (typ,bezeichnung,betrag,rhythmus,start_monat,ende_monat,notiz,aktiv) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                            (typ, bez, betrag, rhythmus, start_monat, ende_monat, notiz, aktiv))
            row = cur.fetchone()
        conn.commit()
        return _json({"ok": True, "id": row[0] if row else None})
    except Exception as e:
        try: conn.rollback()
        except Exception: pass
        return _json({"error": str(e)}, 500)
    finally:
        try: conn.close()
        except Exception: pass


class _EingangError(Exception):
    """Fachlicher Fehler bei der Eingangsrechnungs-Anlage (mit HTTP-Status).

    `details` traegt fachliche Zusatzangaben an den Aufrufer — z. B. dass die
    abgewiesene PDF der schon vorhandenen Rechnung als Anlage beigelegt wurde.
    """
    def __init__(self, msg, status=500, details=None):
        super().__init__(msg)
        self.status = status
        self.details = details or {}


def _match_eingang_supplier(extracted):
    """Matcht den erkannten Lieferanten gegen die Stammdaten (USt-ID > Name)."""
    try:
        suppliers = supplier_mgr.list_all() if hasattr(supplier_mgr, "list_all") else []
    except Exception:
        return None
    vat = (extracted.get("seller_vat_id") or "").replace(" ", "").upper()
    nm = (extracted.get("seller_name") or "").strip().lower()
    matched = None
    for s in (suppliers or []):
        sup_vat = (s.get("vat_id") or "").replace(" ", "").upper()
        sup_nm = (s.get("name") or "").strip().lower()
        if vat and sup_vat and vat == sup_vat:
            return {"id": s.get("id"), "name": s.get("name"), "match_by": "vat_id"}
        if nm and sup_nm and (nm == sup_nm or (len(nm) > 6 and nm in sup_nm) or (len(sup_nm) > 6 and sup_nm in nm)):
            matched = {"id": s.get("id"), "name": s.get("name"), "match_by": "name"}
    return matched


def _ocr_extract_ausgang(pdf_bytes):
    """Texterkennung fuer eine EIGENE Rechnung (Kunde statt Lieferant)."""
    if not pdf_bytes.startswith(b"%PDF"):
        return {"error": "Datei ist keine PDF"}
    try:
        text, seiten = _extract_pdf_text(pdf_bytes)
    except Exception as e:
        return {"error": f"PDF nicht lesbar: {e}"}
    if len(text.strip()) < 30:
        return {"error": "Kein verwertbarer Text im PDF (evtl. Scan)"}
    try:
        return {"extracted": _call_claude_extract(text, _EXTRACT_AUSGANG_PROMPT),
                "page_count": seiten}
    except Exception as e:
        return {"error": f"KI-Aufruf fehlgeschlagen: {e}"}


def _ocr_extract_from_pdf(pdf_bytes):
    """OCR/KI-Extraktion aus PDF-Bytes. Liefert dict; bei Fehler zusätzlich '_status'."""
    if len(pdf_bytes) > 15 * 1024 * 1024:
        return {"error": "PDF zu groß (>15 MB)", "_status": 413}
    if not pdf_bytes.startswith(b"%PDF"):
        return {"error": "Datei ist keine PDF", "_status": 400}
    try:
        text, page_count = _extract_pdf_text(pdf_bytes)
    except Exception as e:
        return {"error": f"PDF nicht lesbar: {e}", "_status": 400}
    if not text.strip() or len(text.strip()) < 30:
        return {"error": "Kein verwertbarer Text im PDF — möglicherweise gescanntes Dokument. "
                         "OCR-Fallback (Vision) noch nicht implementiert.",
                "text_length": len(text), "page_count": page_count, "_status": 422}
    try:
        extracted = _call_claude_extract(text)
    except json.JSONDecodeError as e:
        return {"error": f"KI lieferte kein gültiges JSON: {e}", "_status": 502}
    except Exception as e:
        return {"error": f"KI-Aufruf fehlgeschlagen: {e}", "_status": 502}
    return {"extracted": extracted, "matched_supplier": _match_eingang_supplier(extracted),
            "page_count": page_count, "text_length": len(text)}


@app.route("/api/eingang/extract", methods=["POST"])
def api_eingang_extract():
    """Extrahiert strukturierte Rechnungsdaten aus einer hochgeladenen PDF — speichert nichts."""
    f = request.files.get("file")
    if not f or not f.filename:
        return _json({"error": "Keine Datei"}, 400)
    res = _ocr_extract_from_pdf(f.read())
    return _json(res, res.pop("_status", 200))


def _create_eingang_invoice(pdf_bytes, d, transaction_id=None):
    """Legt aus bestätigten OCR-Daten eine EINGANG-Rechnung an. Returns (inv, report).
    Wirft _EingangError bei Duplikat o.ä."""
    ms = _load_mandant_settings()

    def _parse_d(s, default):
        if not s:
            return default
        try:
            return date.fromisoformat(s[:10])
        except Exception:
            return default

    inv_date = _parse_d(d.get("invoice_date"), date.today())
    due_date = _parse_d(d.get("due_date"), inv_date + timedelta(days=30))
    inv_number = (d.get("invoice_number") or "").strip() or ("OCR-" + datetime.now().strftime("%Y%m%d-%H%M%S"))

    inv = Invoice(
        invoice_number=inv_number,
        invoice_date=inv_date,
        invoice_type_code="380",
        currency_code=d.get("currency") or "EUR",
        buyer_reference=ms.get("name", "") or "Eingang",
        note=(d.get("note") or "Importiert via OCR-Extraktion")[:300],
        seller=Seller(
            name=(d.get("seller_name") or "Unbekannter Lieferant").strip(),
            address=Address(
                street=d.get("seller_street") or "",
                house_number=d.get("seller_house_number") or "",
                city=d.get("seller_city") or "",
                post_code=d.get("seller_postcode") or "",
            ),
            electronic_address=(d.get("seller_email") or "noemail@invalid"),
            electronic_address_scheme="EM",
            contact=Contact(
                d.get("seller_name") or "",
                d.get("seller_phone") or "",
                d.get("seller_email") or "",
            ),
            vat_id=(d.get("seller_vat_id") or "").replace(" ", ""),
            tax_registration_id=d.get("seller_tax_id") or "",
        ),
        buyer=Buyer(
            name=ms.get("name", "") or "Mandant",
            address=Address(
                street=ms.get("street", ""),
                house_number=ms.get("house_number", ""),
                city=ms.get("city", ""),
                post_code=ms.get("post_code", ""),
            ),
            electronic_address=ms.get("email", "") or "buyer@invalid",
            electronic_address_scheme="EM",
            buyer_reference=ms.get("name", ""),
            vat_id=ms.get("vat_id", ""),
        ),
        payment=PaymentInfo(
            means_code="58",
            iban=(d.get("seller_iban") or "").replace(" ", ""),
            payment_terms=d.get("payment_terms") or "Zahlbar nach Erhalt",
            due_date=due_date,
        ),
    )

    # Positionen — entweder aus Lines oder als Sammelposition
    lines = d.get("lines") or []
    for i, line in enumerate(lines, 1):
        try:
            inv.lines.append(InvoiceLine(
                line_id=str(i),
                quantity=Decimal(str(line.get("quantity") or 1)),
                item_name=(line.get("description") or "Position")[:200],
                unit_price=Decimal(str(line.get("unit_price") or 0)),
                line_net_amount=Decimal(str(line.get("net_amount") or 0)),
                tax_rate=Decimal(str(line.get("tax_rate") if line.get("tax_rate") is not None else (d.get("tax_rate") or 19))),
            ))
        except Exception:
            continue

    if not inv.lines:
        net = d.get("net_amount") or d.get("gross_amount") or 0
        inv.lines.append(InvoiceLine(
            line_id="1",
            quantity=Decimal("1"),
            item_name="Rechnungsbetrag (Sammelposition aus OCR)",
            unit_price=Decimal(str(net)),
            line_net_amount=Decimal(str(net)),
            tax_rate=Decimal(str(d.get("tax_rate") or 19)),
        ))

    # Duplikat-Check — zwei Regeln, beide aus echten Faellen (07.09.2026):
    #
    # (1) whitespace-normalisiert: 'EJFMQ0RR 0055' und 'EJFMQ0RR0055' sind dieselbe
    #     Rechnung. Anthropic schickt je Rechnung ZWEI PDF (Rechnung + Zahlungsbeleg);
    #     die OCR liest die Nummer im Beleg mit, in der Rechnung ohne Leerzeichen.
    #
    # (2) NUR beim GLEICHEN LIEFERANTEN. Eingangsrechnungsnummern kommen von
    #     fremden Nummernkreisen und sind systemweit NICHT eindeutig — es wurde
    #     allein bei Anthropic zwei Konten (E1GWKJN1 / EJFMQ0RR), deren Zaehler
    #     beide bei 0001 beginnen; 24 Endnummern gibt es in beiden. Ohne den
    #     Lieferantenvergleich wuerde eine fremde '0044' als Dublette abgewiesen —
    #     und buero.py bucht ein 409 als 'uebergeben' ab, die Rechnung waere
    #     lautlos verschwunden. Bei AUSGANG bleibt die Pruefung systemweit
    #     (eigener Nummernkreis, dort MUSS die Nummer eindeutig sein).
    def _norm_nr(s):
        return "".join((s or "").split()).upper()

    #     Der Lieferant wird ueber NAME ODER USt-IdNr erkannt, nicht ueber die
    #     USt-IdNr allein: derselbe Anthropic-Bestand traegt drei Varianten
    #     (DE354366307, IE4276970QH und leer), weil die OCR mal die eine, mal
    #     die andere Zeile erwischt. Wer nur die USt-IdNr vergleicht, zerlegt
    #     EINEN Lieferanten in drei — und laesst die Doppel wieder durch.
    def _lief_name(v):
        return " ".join((getattr(getattr(v, "seller", None), "name", "") or "").split()).upper()

    def _lief_ust(v):
        return _norm_nr(getattr(getattr(v, "seller", None), "vat_id", "") or "")

    def _gleicher_lieferant(a, b):
        na, nb = _lief_name(a), _lief_name(b)
        if na and na == nb:
            return True
        ua, ub = _lief_ust(a), _lief_ust(b)
        return bool(ua) and ua == ub

    _nr = _norm_nr(inv.invoice_number)
    _treffer = next((v for v in invoices.values()
                     if _norm_nr(v.invoice_number) == _nr
                     and getattr(v, "_direction", "") == "EINGANG"
                     and _gleicher_lieferant(v, inv)), None)
    if _treffer is not None:
        # Die PDF gehoert zu einer Rechnung, die es schon gibt — verwerfen waere
        # Belegverlust. Sie wird der vorhandenen Rechnung als Anlage beigelegt
        # (07.09.2026: "auf jeden Fall alle Rechnungen muessen angehaengt
        # werden"). Typischer Fall: der Zahlungsbeleg zur selben Rechnung.
        _det = {"invoice_id": _treffer._id, "invoice_number": _treffer.invoice_number}
        if pdf_bytes:
            # Die Buero-App stellt dem Dateinamen ihren Hash voran
            # ("71280ccf98_Receipt-….pdf") — der faellt hier weg, sonst heisst
            # die Anlage im Archiv kryptisch und die Rolle wird nicht erkannt.
            _name = re.sub(r"^[0-9a-f]{6,}_", "",
                           (d.get("_dateiname") or "").strip()) or f"{_nr}-beleg.pdf"
            _rolle = ("zahlungsbeleg" if _name.lower().startswith(("receipt", "beleg", "quittung"))
                      else "beleg")
            try:
                _an = archive.anlage_hinzufuegen(_treffer._id, _name, pdf_bytes,
                                                 rolle=_rolle,
                                                 quelle=d.get("_quelle") or "Eingangsrechnungs-Uebergabe")
                _det.update(angehaengt=not _an.get("schon_vorhanden"),
                            anlage=_an.get("datei"), rolle=_rolle,
                            schon_vorhanden=bool(_an.get("schon_vorhanden")))
            except Exception as _e:
                print(f"[eingang] Anlage konnte nicht abgelegt werden: {_e}", flush=True)
                _det.update(angehaengt=False, anlage_fehler=str(_e))
        raise _EingangError(f"Rechnungsnummer '{inv.invoice_number}' existiert bereits.",
                            409, _det)

    # XRechnung erzeugen + validieren
    xml_bytes = generate_and_serialize(inv)
    report = validate_invoice(inv)
    inv._direction = "EINGANG"
    if transaction_id:
        try:
            inv._transaction_id = transaction_id
        except Exception:
            pass

    # In System einhängen
    invoices[inv._id] = inv
    dash.add_invoice(inv)
    wf_engine.start_workflow(inv)
    archive.archive_invoice(inv, xml_bytes, report, direction="EINGANG")

    # Original-PDF zusätzlich ablegen (im Archiv-Verzeichnis der Rechnung).
    # pdf_bytes darf leer sein (reine Daten-Ablage via externer API ohne PDF).
    if pdf_bytes:
        try:
            arch_dir = _DATA / "data" / "archiv" / inv._id
            arch_dir.mkdir(parents=True, exist_ok=True)
            (arch_dir / "original.pdf").write_bytes(pdf_bytes)
        except Exception as ex:
            print(f"[eingang] PDF-Backup fehlgeschlagen: {ex}")

    return inv, report


@app.route("/api/eingang/confirm", methods=["POST"])
def api_eingang_confirm():
    """Übernimmt vom User bestätigte Extraktions-Daten + Original-PDF und legt eine Eingangsrechnung an.
    Multipart: 'file' (PDF) + 'data' (JSON-String mit bestätigten Feldern)."""
    f = request.files.get("file")
    data_raw = request.form.get("data")
    if not f or not data_raw:
        return _json({"error": "file und data sind Pflicht"}, 400)
    try:
        d = json.loads(data_raw)
    except Exception as e:
        return _json({"error": f"Ungültiges JSON in data: {e}"}, 400)
    try:
        inv, report = _create_eingang_invoice(f.read(), d)
    except _EingangError as e:
        return _json({"error": str(e)}, e.status)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return _json({"error": f"Anlage fehlgeschlagen: {e}"}, 500)
    return _json({
        "ok": True,
        "invoice_id": inv._id,
        "invoice_number": inv.invoice_number,
        "valid": report.is_valid if report else None,
        "errors": report.error_count if report else 0,
        "format": "PDF+OCR (XRechnung)",
    }, 201)


def _offener_betrag(inv, storno_targets=None):
    """Was von einer Rechnung noch offen ist: brutto minus bereits gezahlter Teil
    minus Gutschriften, die auf die Rechnung verweisen (storno_targets aus
    _collect_storno_targets; ohne Uebergabe zaehlen nur Zahlungen).

    Abschlagszahlungen liefen bisher nirgends in die offenen Posten ein — eine
    Steuerberater-Rechnung ueber 3.268,45 EUR stand mit dem vollen Betrag unter
    „zu zahlen", obwohl 3.000,00 EUR laengst geflossen waren (02.09.2026).
    """
    try:
        # amount_due statt tax_inclusive_amount: Anzahlungen (BT-113) sind schon
        # gefordert und bezahlt worden, sie stehen nicht noch einmal offen.
        brutto = float(inv.amount_due())
    except Exception:
        return 0.0
    try:
        gezahlt = float(getattr(inv, "_paid_amount", 0) or 0)
    except Exception:
        gezahlt = 0.0
    rest = round(brutto - gezahlt - _gutgeschrieben(inv, storno_targets), 2)
    return rest if rest > 0 else 0.0


@app.route("/api/eingang/offen")
def api_eingang_offen():
    """Offene Eingangsrechnungen (Verbindlichkeiten – zu zahlen). Für Dashboard + Buchhaltung."""
    out = []
    for i in invoices.values():
        if (getattr(i, "_direction", "") or "AUSGANG") != "EINGANG":
            continue
        if getattr(i, "status", "") in ("BEZAHLT", "STORNIERT", "ZURUECKGEWIESEN"):
            continue
        try:
            gross = float(i.tax_inclusive_amount())
        except Exception:
            gross = 0.0
        due = ""
        try:
            if getattr(i, "payment", None) and i.payment.due_date:
                due = i.payment.due_date.isoformat()
        except Exception:
            pass
        gezahlt = round(gross - _offener_betrag(i), 2)
        out.append({
            "invoice_id": i._id,
            "invoice_number": i.invoice_number,
            "supplier": i.seller.name,
            "gross": gross,
            "gezahlt": gezahlt,
            "offen": _offener_betrag(i),
            "invoice_date": i.invoice_date.isoformat() if i.invoice_date else "",
            "due_date": due,
            "transaction_id": getattr(i, "_transaction_id", None),
        })
    out.sort(key=lambda x: (x["due_date"] or x["invoice_date"] or "9999"))
    total = round(sum(x["offen"] for x in out), 2)
    return _json({"count": len(out), "total": total, "posten": out})


# ── Eingangsrechnung als offener Posten im Vorgangs-Schritt ──────────────
def _step_latest_pdf(tid, step_key):
    """Neueste PDF-Anlage eines Steps → (bytes, attachment_dict, error)."""
    txn = txn_mgr.get(tid)
    if not txn:
        return None, None, "Vorgang nicht gefunden"
    step = (txn.get("steps") or {}).get(step_key, {})
    pdfs = [a for a in (step.get("attachments") or []) if (a.get("filename", "").lower().endswith(".pdf"))]
    if not pdfs:
        return None, None, "Keine PDF in diesem Schritt angehängt — bitte zuerst die Rechnung anhängen."
    a = pdfs[-1]
    fp = _DATA / "data" / "attachments" / tid / step_key / a["filename"]
    if not fp.exists():
        return None, None, "Angehängte Datei nicht gefunden"
    return fp.read_bytes(), a, None


def _eingang_status_payload(tid, step_key, txn=None):
    """Aktueller Zustand des offenen Postens eines Schritts (Status live aus der Rechnung)."""
    txn = txn or txn_mgr.get(tid)
    step = (txn.get("steps") or {}).get(step_key, {}) if txn else {}
    iid = step.get("eingangsrechnung_id")
    if not iid:
        return {"linked": False}
    inv = invoices.get(iid)
    if not inv:
        return {"linked": True, "invoice_id": iid, "missing": True,
                "invoice_number": step.get("eingang_invoice_number"),
                "supplier": step.get("eingang_supplier"),
                "amount_gross": step.get("eingang_amount"),
                "status": "unbekannt", "paid": False}
    status = getattr(inv, "status", "") or ""
    try:
        gross = float(inv.tax_inclusive_amount())
    except Exception:
        gross = step.get("eingang_amount")
    return {
        "linked": True, "invoice_id": iid,
        "invoice_number": inv.invoice_number,
        "supplier": inv.seller.name,
        "amount_gross": gross,
        "status": status,
        "paid": (status == "BEZAHLT"),
        "paid_at": getattr(inv, "_paid_at", None),
        "paid_via_booking": getattr(inv, "_paid_via_booking", None),
    }


@app.route("/api/transactions/<tid>/steps/<step_key>/ocr", methods=["POST"])
def api_txn_step_ocr(tid, step_key):
    """OCR/KI-Erkennung der im Schritt angehängten PDF (speichert nichts)."""
    pdf_bytes, a, err = _step_latest_pdf(tid, step_key)
    if err:
        return _json({"error": err}, 400)
    res = _ocr_extract_from_pdf(pdf_bytes)
    if a:
        res["source_filename"] = a.get("original_name") or a.get("filename")
    return _json(res, res.pop("_status", 200))


@app.route("/api/transactions/<tid>/steps/<step_key>/eingang")
def api_txn_step_eingang_status(tid, step_key):
    txn = txn_mgr.get(tid)
    if not txn:
        return _json({"error": "Vorgang nicht gefunden"}, 404)
    return _json(_eingang_status_payload(tid, step_key, txn))


@app.route("/api/transactions/<tid>/steps/<step_key>/eingang", methods=["POST"])
def api_txn_step_eingang_create(tid, step_key):
    """Legt aus den (ggf. korrigierten) OCR-Daten die Eingangsrechnung als offenen Posten an
    und verknüpft sie mit dem Vorgangs-Schritt."""
    txn = txn_mgr.get(tid)
    if not txn:
        return _json({"error": "Vorgang nicht gefunden"}, 404)
    if (txn.get("steps") or {}).get(step_key, {}).get("eingangsrechnung_id"):
        return _json({"error": "Für diesen Schritt ist bereits ein offener Posten angelegt."}, 409)
    d = request.json or {}
    pdf_bytes, a, err = _step_latest_pdf(tid, step_key)
    if err:
        return _json({"error": err}, 400)
    try:
        inv, report = _create_eingang_invoice(pdf_bytes, d, transaction_id=tid)
    except _EingangError as e:
        return _json({"error": str(e)}, e.status)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return _json({"error": f"Anlage fehlgeschlagen: {e}"}, 500)
    gross = float(inv.tax_inclusive_amount())
    txn_mgr.update_step(tid, step_key, {
        "eingangsrechnung_id": inv._id,
        "eingang_invoice_number": inv.invoice_number,
        "eingang_supplier": inv.seller.name,
        "eingang_amount": gross,
    })
    payload = _eingang_status_payload(tid, step_key)
    payload["ok"] = True
    payload["valid"] = report.is_valid if report else None
    return _json(payload, 201)


@app.route("/api/transactions/<tid>/steps/<step_key>/eingang/mark-paid", methods=["POST"])
def api_txn_step_eingang_mark_paid(tid, step_key):
    """Markiert die verknüpfte Eingangsrechnung manuell als bezahlt."""
    txn = txn_mgr.get(tid)
    if not txn:
        return _json({"error": "Vorgang nicht gefunden"}, 404)
    iid = (txn.get("steps") or {}).get(step_key, {}).get("eingangsrechnung_id")
    inv = invoices.get(iid) if iid else None
    if not inv:
        return _json({"error": "Keine verknüpfte Eingangsrechnung"}, 404)
    d = request.json or {}
    setattr(inv, "_paid_at", (d.get("paid_at") or date.today().isoformat())[:10])
    try:
        setattr(inv, "_paid_amount", float(inv.tax_inclusive_amount()))
    except Exception:
        pass
    setattr(inv, "_paid_via_booking", "manuell")
    try:
        inv.status = "BEZAHLT"
    except Exception:
        pass
    auto_save()
    return _json(_eingang_status_payload(tid, step_key))


@app.route("/api/transactions/<tid>/steps/<step_key>/eingang/unpay", methods=["POST"])
def api_txn_step_eingang_unpay(tid, step_key):
    """Nimmt die Bezahlt-Markierung der verknüpften Eingangsrechnung zurück."""
    txn = txn_mgr.get(tid)
    if not txn:
        return _json({"error": "Vorgang nicht gefunden"}, 404)
    iid = (txn.get("steps") or {}).get(step_key, {}).get("eingangsrechnung_id")
    if not iid or iid not in invoices:
        return _json({"error": "Keine verknüpfte Eingangsrechnung"}, 404)
    _undo_invoice_paid(iid)
    return _json(_eingang_status_payload(tid, step_key))


@app.route("/api/transactions/<tid>/steps/<step_key>/eingang", methods=["DELETE"])
def api_txn_step_eingang_unlink(tid, step_key):
    """Löst die Verknüpfung (die Eingangsrechnung selbst bleibt im System)."""
    txn = txn_mgr.get(tid)
    if not txn:
        return _json({"error": "Vorgang nicht gefunden"}, 404)
    txn_mgr.update_step(tid, step_key, {
        "eingangsrechnung_id": None,
        "eingang_invoice_number": None,
        "eingang_supplier": None,
        "eingang_amount": None,
    })
    return _json({"ok": True, "linked": False})


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

    # Archivieren als Ausgangsrechnung (FR-500) — mit PDF für die Buchhaltung
    _archive_ausgang(inv, xml_bytes, report)

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


# ── ZUGFeRD: asynchrone Erzeugung mit Job-Queue ────────────────────────
# Jobs im Speicher: { job_id: {"status": "running|done|error", "pdf": bytes|None, "error": str|None, "filename": str, "created": timestamp} }
_zugferd_jobs = {}
_zugferd_lock = __import__("threading").Lock()


def _zugferd_worker(job_id: str, inv, xml_bytes: bytes, filename: str):
    """Erzeugt ZUGFeRD-PDF im Hintergrund."""
    try:
        try:
            from weasyprint import HTML
            from zugferd_writer import _embed_xml_in_pdf
            html_str = _render_invoice_html(inv)
            visible_pdf = HTML(string=html_str).write_pdf()
            pdf_bytes = _embed_xml_in_pdf(visible_pdf, xml_bytes, filename="factur-x.xml")
        except ImportError:
            pdf_bytes = generate_zugferd_pdf(inv, xml_bytes)

        with _zugferd_lock:
            _zugferd_jobs[job_id] = {
                "status": "done", "pdf": pdf_bytes, "error": None,
                "filename": filename, "created": _zugferd_jobs[job_id]["created"],
            }
    except Exception as e:
        with _zugferd_lock:
            _zugferd_jobs[job_id] = {
                "status": "error", "pdf": None, "error": str(e),
                "filename": filename, "created": _zugferd_jobs[job_id]["created"],
            }


def _zugferd_cleanup():
    """Entfernt fertige Jobs älter als 10 Minuten."""
    import time
    now = time.time()
    with _zugferd_lock:
        stale = [jid for jid, job in _zugferd_jobs.items()
                 if now - job.get("created", now) > 600]
        for jid in stale:
            del _zugferd_jobs[jid]


@app.route("/api/invoices/<inv_id>/zugferd/start", methods=["POST"])
def api_zugferd_start(inv_id):
    """Startet asynchrone ZUGFeRD-PDF-Erzeugung, liefert job_id zurück."""
    import uuid, threading, time
    _zugferd_cleanup()

    inv = invoices.get(inv_id)
    if not inv:
        return _json({"error": "Rechnung nicht gefunden"}, 404)

    try:
        xml_bytes = generate_and_serialize(inv)
    except Exception as e:
        return _json({"error": f"XML-Erzeugung fehlgeschlagen: {e}"}, 500)

    job_id = uuid.uuid4().hex[:12]
    filename = f"{inv.invoice_number}_zugferd.pdf"

    with _zugferd_lock:
        _zugferd_jobs[job_id] = {
            "status": "running", "pdf": None, "error": None,
            "filename": filename, "created": time.time(),
        }

    t = threading.Thread(target=_zugferd_worker,
                         args=(job_id, inv, xml_bytes, filename),
                         daemon=True)
    t.start()

    return _json({"job_id": job_id, "status": "running"})


@app.route("/api/invoices/<inv_id>/zugferd/status/<job_id>")
def api_zugferd_status(inv_id, job_id):
    """Prüft Status eines ZUGFeRD-Jobs."""
    with _zugferd_lock:
        job = _zugferd_jobs.get(job_id)
    if not job:
        return _json({"error": "Job nicht gefunden"}, 404)
    return _json({
        "status": job["status"],
        "ready": job["status"] == "done",
        "error": job.get("error"),
    })


@app.route("/api/invoices/<inv_id>/zugferd/download/<job_id>")
def api_zugferd_download(inv_id, job_id):
    """Lädt fertiges ZUGFeRD-PDF herunter."""
    from flask import Response
    with _zugferd_lock:
        job = _zugferd_jobs.get(job_id)
    if not job:
        return _json({"error": "Job nicht gefunden"}, 404)
    if job["status"] != "done":
        return _json({"error": "PDF noch nicht fertig", "status": job["status"]}, 202)

    return Response(
        job["pdf"],
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{job["filename"]}"'},
    )


@app.route("/api/invoices/<inv_id>/zugferd", methods=["GET"])
def api_download_zugferd(inv_id):
    """
    Synchrone Variante (Rückwärtskompatibilität).
    Neue Frontend-Versionen sollten /zugferd/start + /status + /download nutzen.
    """
    from flask import Response
    inv = invoices.get(inv_id)
    if not inv:
        return _json({"error": "Rechnung nicht gefunden"}, 404)
    try:
        xml_bytes = generate_and_serialize(inv)
        try:
            from weasyprint import HTML
            from zugferd_writer import _embed_xml_in_pdf
            html_str = _render_invoice_html(inv)
            visible_pdf = HTML(string=html_str).write_pdf()
            pdf_bytes = _embed_xml_in_pdf(visible_pdf, xml_bytes, filename="factur-x.xml")
        except ImportError:
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
        "report_xml": (result.report_xml[:5000] if result.report_xml else ""),  # Erste 5000 Zeichen zum Debug
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


@app.route("/api/invoices/<inv_id>/mark-paid", methods=["POST"])
def api_invoice_mark_paid(inv_id):
    """Setzt eine Rechnung manuell auf BEZAHLT — v.a. für Eingangsrechnungen,
    deren Zahlung nicht Cent-genau im Bank-Abgleich auftaucht (Kartenzahlung,
    Fremdwährung, Zahlung vor Beginn der Bankhistorie).
    Body optional: {date: 'YYYY-MM-DD', amount: 123.45, comment: '...'}"""
    inv = invoices.get(inv_id)
    if not inv:
        return _json({"error": "Nicht gefunden"}, 404)
    if getattr(inv, "status", "") in ("STORNIERT", "ZURUECKGEWIESEN"):
        return _json({"error": f"Status {inv.status} kann nicht auf BEZAHLT gesetzt werden"}, 400)
    data = request.get_json(silent=True) or {}
    setattr(inv, "_paid_at", (data.get("date") or date.today().isoformat())[:10])
    if data.get("amount") is not None:
        try:
            setattr(inv, "_paid_amount", float(data["amount"]))
        except Exception:
            pass
    if data.get("comment"):
        setattr(inv, "_paid_note", str(data["comment"])[:300])
    try:
        inv.status = "BEZAHLT"
    except Exception:
        pass
    auto_save()
    return _json({"ok": True, "status": inv.status, "paid_at": getattr(inv, "_paid_at", None)})


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
        _type_code = str(data.get("type", "380") or "380")
        _ist_gutschrift = (_type_code == "381")
        _preceding = (data.get("preceding_invoice", "") or "").strip()
        if _ist_gutschrift and _preceding:
            # Bezug muss auf eine bekannte Ausgangsrechnung zeigen — sonst Tippfehler
            _orig = next((v for v in invoices.values()
                          if v.invoice_number == _preceding), None)
            if not _orig:
                return _json({"error": f"Bezugsrechnung {_preceding} ist nicht im System. "
                                       f"Feld leer lassen (freie Gutschrift) oder Nummer pruefen."}, 400)
            if getattr(_orig, "invoice_type_code", "380") == "381":
                return _json({"error": f"{_preceding} ist selbst eine Gutschrift."}, 400)
            # Deckel wie im Gutschrift-Endpoint: nicht mehr gutschreiben als die
            # Rechnung (abzueglich frueherer Gutschriften) hergibt — VOR der
            # Nummernvergabe, damit ein abgelehnter Versuch keine GS-Nummer verbrennt.
            try:
                _gs_brutto = 0.0
                for _ld in data.get("lines", []):
                    _n = float(_ld.get("net") or 0) or float(_ld.get("quantity", 1) or 0) * float(_ld.get("price", 0) or 0)
                    _gs_brutto += _n * (1 + float(_ld.get("tax_rate", 19) or 0) / 100)
                _orig_brutto = float(_orig.tax_inclusive_amount())
                _schon = _gutgeschrieben(_orig, _collect_storno_targets(invoices.values()))
                _rest = round(_orig_brutto - _schon, 2)
                if round(_gs_brutto, 2) > _rest + 0.01:
                    return _json({"error": f"Gutschrift ({_gs_brutto:.2f} EUR) uebersteigt den noch offenen Rest "
                                           f"der Rechnung {_preceding} ({_rest:.2f} EUR; Brutto {_orig_brutto:.2f}, "
                                           f"bereits gutgeschrieben {_schon:.2f})."}, 400)
            except (TypeError, ValueError):
                pass

        # Rechnungsnummer: automatisch vergeben wenn leer
        number = data.get("number", "").strip()
        if not number:
            number = (_next_gutschrift_number(advance=True) if _ist_gutschrift
                      else _next_invoice_number(advance=True))
        else:
            # Dubletten-Check VOR dem Hochzaehlen — ein abgelehnter Versuch darf
            # keine Nummer verbrennen (sonst Luecke im Nummernkreis)
            if any(v.invoice_number == number for v in invoices.values()):
                return _json({"error": f"Rechnungsnummer {number} existiert bereits im System."}, 409)
            # Zaehler hochzaehlen wenn die Nummer mit RE-YYYY-XXXXX / GS-YYYY-XXXXX beginnt
            import re
            if re.match(r'^RE-\d{4}-\d{5}', number):
                _next_invoice_number(advance=True)
            elif re.match(r'^GS-\d{4}-\d{5}', number):
                _next_gutschrift_number(advance=True)

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
        if _ist_gutschrift:
            # Kein Zahlungsziel — aber BR-CO-25 verlangt bei positivem Betrag
            # Faelligkeit ODER Zahlungsbedingung, daher der Verrechnungshinweis.
            _due_date = None
            _terms = GUTSCHRIFT_TERMS

        inv = Invoice(
            invoice_number=number,
            invoice_date=_inv_date,
            tax_point_date=date.fromisoformat(data["delivery_date"]) if data.get("delivery_date") else None,
            period_end=date.fromisoformat(data["delivery_date"]) if data.get("delivery_date") else None,
            invoice_type_code=_type_code,
            preceding_invoice=_preceding if _ist_gutschrift else "",
            currency_code=data.get("currency", "EUR"),
            buyer_reference=data.get("buyer_reference", "").strip() or "PRIV-" + (data.get("buyer_id", "") or "00000"),
            note=data.get("note", ""),
            seller=Seller(
                name=data.get("seller_name", ""),
                address=Address(street=data.get("seller_street", ""),
                                house_number=data.get("seller_house_number", ""),
                                city=data.get("seller_city", ""),
                                post_code=data.get("seller_postcode", "")),
                electronic_address=data.get("seller_email", ""),
                electronic_address_scheme="EM",
                contact=Contact(data.get("seller_contact", ""),
                                data.get("seller_phone", ""),
                                data.get("seller_contact_email", "")),
                vat_id=data.get("seller_vat", ""),
            ),
            buyer=Buyer(
                name=_buyer_display,
                address=Address(street=data.get("buyer_street", ""),
                                house_number=data.get("buyer_house_number", ""),
                                city=data.get("buyer_city", ""),
                                post_code=data.get("buyer_postcode", "")),
                electronic_address=data.get("buyer_email", ""),
                electronic_address_scheme="EM",
                buyer_reference=data.get("buyer_reference", "").strip() or "PRIV-" + (data.get("buyer_id", "") or "00000"),
                vat_id=data.get("buyer_vat", ""),
            ),
            payment=PaymentInfo(
                means_code=data.get("payment_code", "58"),
                iban=data.get("iban", data.get("seller_iban", "")),
                bic=data.get("bic", data.get("seller_bic", "")),
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

        # Archivieren — mit PDF für die Buchhaltung
        _archive_ausgang(inv, xml_bytes, report)

        # Sofort persistieren. Ohne das lebt eine frisch erstellte Rechnung nur
        # im Speicher und im Archiv — ein Neustart vor der naechsten Aktion
        # (Freigabe, Versand) loeschte sie aus invoices.json.
        auto_save()

        # Kaeufer automatisch speichern (falls neu)
        _save_buyer_if_new(
            name=data.get("buyer_name", ""),
            street=data.get("buyer_street", ""),
            house_number=data.get("buyer_house_number", ""),
            post_code=data.get("buyer_postcode", ""),
            city=data.get("buyer_city", ""),
            email=data.get("buyer_email", ""),
            phone=data.get("buyer_phone", ""),
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

    # Positionen werden 1:1 übernommen. Die Gutschrift-Semantik kommt
    # aus invoice_type_code="381"; Mengen/Preise/Netto bleiben positiv
    # (EN-16931-konform, Validator BR-22/BR-26/BR-CO-10 bleibt erfüllt).
    for line in inv.lines:
        storno.lines.append(InvoiceLine(
            line_id=line.line_id,
            quantity=line.quantity,
            unit_code=line.unit_code,
            item_name=line.item_name,
            item_description=f"Storno: {line.item_description or line.item_name}",
            unit_price=line.unit_price,
            line_net_amount=line.line_net_amount,
            tax_category=line.tax_category,
            tax_rate=line.tax_rate,
        ))

    # Nachlässe/Zuschläge unverändert übernehmen (Vorzeichen kommt aus 381)
    for ac in inv.allowances_charges:
        storno.allowances_charges.append(AllowanceCharge(
            is_charge=ac.is_charge,
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

    # Beide archivieren — mit PDF für die Buchhaltung
    _archive_ausgang(storno, xml_bytes, report)

    # Benachrichtigung
    notifier.notify_new_invoice(storno, report.is_valid, report.error_count)

    # Dashboard
    dash.add_invoice(storno)

    # Sofort persistieren — der Storno und der STORNIERT-Status des Originals
    # lebten bis zur naechsten fremden Speicherung nur im RAM (Befund 03.09.2026).
    auto_save()

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


# ── API: Gutschrift (Teil-/Kulanzgutschrift mit Bezug) ────────────────

@app.route("/api/invoices/<inv_id>/gutschrift", methods=["POST"])
def api_gutschrift(inv_id):
    """
    Erzeugt eine Gutschrift (Typ 381, Nummer GS-JJJJ-NNNNN) mit Bezug auf die
    Originalrechnung. Anders als der Storno bleibt die Originalrechnung gueltig;
    die Gutschrift mindert nur den offenen Betrag (Teilgutschrift) oder deckt ihn
    vollstaendig (dann gilt die Rechnung als erledigt, siehe _is_invoice_paid).

    Body: { reason, date?, note?, user?, lines: [ {name, description?, quantity,
            price, tax_rate?, unit?} ] }
    Ohne lines werden alle Positionen der Rechnung 1:1 uebernommen.

    Regeln:
    - nur Ausgangsrechnungen (Typ 380/384), Status FREIGEGEBEN/EXPORTIERT/BEZAHLT
    - Gutschriftsbetrag darf den noch nicht gutgeschriebenen Rest nicht uebersteigen
    - Mengen/Preise bleiben positiv (EN 16931: das Vorzeichen kommt aus 381)
    """
    inv = invoices.get(inv_id)
    if not inv:
        return _json({"error": "Rechnung nicht gefunden"}, 404)
    if inv.invoice_type_code == "381":
        return _json({"error": "Zu einer Gutschrift kann keine weitere Gutschrift erstellt werden."}, 400)
    if (inv._direction or "AUSGANG") == "EINGANG":
        return _json({"error": "Gutschriften sind nur zu eigenen Ausgangsrechnungen moeglich."}, 400)
    if inv.status == "STORNIERT":
        return _json({"error": "Die Rechnung ist storniert — sie ist bereits in voller Hoehe gutgeschrieben."}, 400)
    if inv.status not in ("FREIGEGEBEN", "EXPORTIERT", "BEZAHLT"):
        return _json({"error": f"Gutschrift nur zu freigegebenen, exportierten oder bezahlten Rechnungen "
                               f"(aktuell: {inv.status})."}, 400)

    data = request.get_json(silent=True) or {}
    user = data.get("user", "web-user")
    reason = (data.get("reason", "") or "").strip()
    if not reason:
        return _json({"error": "Bitte einen Grund fuer die Gutschrift angeben."}, 400)

    import copy
    try:
        gs_date = date.fromisoformat(data["date"]) if data.get("date") else date.today()
    except Exception:
        return _json({"error": "Ungueltiges Datum."}, 400)

    # Deckelung: Brutto der Rechnung minus bereits gutgeschriebene Betraege
    _map = _collect_storno_targets(invoices.values())
    brutto_orig = float(inv.tax_inclusive_amount())
    bereits = _gutgeschrieben(inv, _map)
    rest = round(brutto_orig - bereits, 2)
    if rest <= 0.01:
        return _json({"error": f"Rechnung {inv.invoice_number} ist bereits vollstaendig gutgeschrieben "
                               f"({bereits:.2f} von {brutto_orig:.2f} EUR)."}, 400)

    gs = Invoice(
        invoice_number=_next_gutschrift_number(advance=False),  # erst nach Pruefung hochzaehlen
        invoice_date=gs_date,
        invoice_type_code="381",
        currency_code=inv.currency_code,
        buyer_reference=inv.buyer_reference or inv.buyer.buyer_reference,
        preceding_invoice=inv.invoice_number,
        note=(data.get("note") or "").strip() or f"Gutschrift zu Rechnung {inv.invoice_number}: {reason}",
        seller=copy.deepcopy(inv.seller),
        buyer=copy.deepcopy(inv.buyer),
        payment=copy.deepcopy(inv.payment),
    )
    gs.payment.due_date = None
    gs.payment.payment_terms = GUTSCHRIFT_TERMS   # BR-CO-25: Zahlungsbedingung statt Faelligkeit

    lines_in = data.get("lines")
    if lines_in:
        for i, ld in enumerate(lines_in, 1):
            try:
                qty = Decimal(str(ld.get("quantity", 1)))
                price = Decimal(str(ld.get("price", 0)))
                rate = Decimal(str(ld.get("tax_rate", 19)))
            except Exception:
                return _json({"error": f"Position {i}: Menge/Preis/Steuersatz ungueltig."}, 400)
            if qty <= 0 or price < 0:
                return _json({"error": f"Position {i}: Menge muss > 0 und Preis >= 0 sein "
                                       f"(das Vorzeichen ergibt sich aus der Gutschrift selbst)."}, 400)
            name = (ld.get("name") or "").strip()
            if not name:
                return _json({"error": f"Position {i}: Bezeichnung fehlt."}, 400)
            net = (qty * price).quantize(Decimal("0.01"))
            gs.lines.append(InvoiceLine(
                line_id=str(i), quantity=qty,
                unit_code=(ld.get("unit") or "C62"),
                item_name=name,
                item_description=(ld.get("description") or "").strip(),
                unit_price=price, line_net_amount=net,
                tax_category=("S" if rate > 0 else "Z"), tax_rate=rate,
            ))
    else:
        for line in inv.lines:
            gs.lines.append(InvoiceLine(
                line_id=line.line_id, quantity=line.quantity, unit_code=line.unit_code,
                item_name=line.item_name, item_description=line.item_description,
                unit_price=line.unit_price, line_net_amount=line.line_net_amount,
                tax_category=line.tax_category, tax_rate=line.tax_rate,
            ))
        for ac in inv.allowances_charges:
            gs.allowances_charges.append(AllowanceCharge(
                is_charge=ac.is_charge, amount=ac.amount, base_amount=ac.base_amount,
                percentage=ac.percentage, reason=ac.reason,
                tax_category=ac.tax_category, tax_rate=ac.tax_rate,
            ))
    if not gs.lines:
        return _json({"error": "Eine Gutschrift braucht mindestens eine Position."}, 400)

    brutto_gs = float(gs.tax_inclusive_amount())
    if brutto_gs <= 0:
        return _json({"error": "Der Gutschriftsbetrag muss groesser als 0 sein."}, 400)
    if brutto_gs > rest + 0.01:
        return _json({"error": f"Gutschrift ({brutto_gs:.2f} EUR) uebersteigt den noch offenen Rest der "
                               f"Rechnung ({rest:.2f} EUR; Brutto {brutto_orig:.2f}, bereits "
                               f"gutgeschrieben {bereits:.2f})."}, 400)

    # Jetzt erst die Nummer verbrauchen
    gs.invoice_number = _next_gutschrift_number(advance=True)

    report = validate_invoice(gs)
    xml_bytes = generate_and_serialize(gs)

    gs._direction = "AUSGANG"
    gs.status = "FREIGEGEBEN"
    invoices[gs._id] = gs
    gs.add_audit("GUTSCHRIFT_ERSTELLT", user,
                 f"Gutschrift zu {inv.invoice_number} ueber {brutto_gs:.2f} {gs.currency_code}: {reason}")
    inbox.duplicates.register(gs, gs._id)

    voll = brutto_gs >= rest - 0.01
    inv.add_audit("GUTSCHRIFT_ERHALTEN", user,
                  f"{'Vollstaendig' if voll else 'Teilweise'} gutgeschrieben durch {gs.invoice_number} "
                  f"({brutto_gs:.2f} {gs.currency_code}). Grund: {reason}")

    _archive_ausgang(gs, xml_bytes, report)
    notifier.notify_new_invoice(gs, report.is_valid, report.error_count)
    dash.add_invoice(gs)
    auto_save()

    return _json({
        "success": True,
        "gutschrift_id": gs._id,
        "gutschrift_number": gs.invoice_number,
        "original_number": inv.invoice_number,
        "original_status": inv.status,
        "type_code": "381",
        "gross": brutto_gs,
        "remaining_open": round(max(rest - brutto_gs, 0.0), 2),
        "full_credit": voll,
        "valid": report.is_valid,
        "errors": report.error_count,
        "xml_b64": base64.b64encode(xml_bytes).decode(),
    })


# ── API: Archiv ────────────────────────────────────────────────────────

@app.route("/api/archive")
def api_archive():
    records = archive.list_all()
    for rec in records:
        inv_id = rec.get("invoice_id", "")
        inv = invoices.get(inv_id)
        meta = rec.setdefault("invoice_metadata", {})
        if inv:
            rec["live_status"] = inv.status
            type_code = meta.get("type_code", "380")
            rec["stornierbar"] = (
                rec.get("direction") == "AUSGANG"
                and type_code != "381"
                and inv.status in ("FREIGEGEBEN", "EXPORTIERT")
            )
            due = _invoice_due_date(inv)
            if due:
                meta["due_date"] = due.isoformat() if hasattr(due, "isoformat") else str(due)
            rec["paid_at"] = getattr(inv, "_paid_at", None)
            rec["paid_amount"] = getattr(inv, "_paid_amount", None)
            rec["overpaid"] = _is_invoice_overpaid(inv)
        else:
            rec["live_status"] = meta.get("status", "")
            rec["stornierbar"] = False
            rec["paid_at"] = None
            rec["paid_amount"] = None
            rec["overpaid"] = False
        rec["hard_error"] = _hard_validation_error(rec.get("validation_report") or {})
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

def _build_invoice_pdf(inv):
    """Erzeugt das PDF zur Rechnung (oder Storno) und liefert (filename, bytes).

    Logik in invoice_pdf.build() — dort ohne webapp-Import nutzbar (Backfill,
    Wartungsskripte). Wirft eine Exception, wenn die Erzeugung fehlschlägt.
    """
    import invoice_pdf
    return invoice_pdf.build(inv, doc_gen, _load_buyers(), _load_mandant_settings())


@app.route("/api/invoices/<inv_id>/send", methods=["POST"])
def api_send_invoice(inv_id):
    """Versendet eine Rechnung per E-Mail (PDF + XRechnung-XML)."""
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

    # PDF erzeugen (Helper, wird auch vom PDF-Download-Endpoint genutzt).
    # Strikt: Ohne PDF wird NICHT versendet — der Empfänger muss in jedem Fall
    # ein echtes PDF (zusätzlich zum XRechnung-XML) erhalten.
    try:
        pdf_filename, pdf_bytes = _build_invoice_pdf(inv)
        pdf_attachments = [(pdf_filename, pdf_bytes)]
    except Exception as e:
        import traceback
        msg = f"PDF-Erzeugung fehlgeschlagen: {e}"
        print(f"[/send] {msg} (Rechnung {inv.invoice_number})", flush=True)
        print(traceback.format_exc(), flush=True)
        return _json({
            "error": "Versand abgebrochen: Das PDF konnte nicht erzeugt werden. "
                     "Der Empfänger soll IMMER ein PDF erhalten — bitte den Fehler "
                     "beheben und erneut versenden.",
            "details": msg,
        }, 500)

    send_log = email_sender.send_invoice(
        invoice=inv, recipient=recipient,
        subject=subject, body_text=body, xml_bytes=xml_bytes,
        additional_attachments=pdf_attachments,
    )

    # Versand im Audit-Trail protokollieren
    if send_log.success:
        att_info = "PDF + XML" if pdf_attachments else "XML"
        inv.add_audit("EMAIL_VERSENDET",
                      comment=f"An {recipient} ({att_info}), Message-ID: {send_log.message_id}")
        _log_sent_mail(recipient, send_log.subject or subject, body,
                       doc_type="invoice", reference=inv.invoice_number,
                       attachments=[send_log.attachment_filename] +
                                   [a[0] for a in pdf_attachments],
                       from_addr=getattr(getattr(email_sender, "config", None),
                                         "smtp_from_address", ""),
                       message_id=send_log.message_id)
        # Option 3: Versand und Workflow synchron halten. Eine versendete
        # Rechnung darf nicht in einem Vor-Freigabe-Status hängen bleiben –
        # sonst bietet die UI "Ablehnen" (das eine versandte Rechnung still
        # entwertet) statt "Storno". Daher Status auf FREIGEGEBEN heben.
        if inv.status in ("NEU", "IN_PRUEFUNG", "IN_FREIGABE"):
            _old_status = inv.status
            inv.status = "FREIGEGEBEN"
            inv.add_audit("FREIGEGEBEN", user="system",
                          comment="Automatisch freigegeben durch Versand",
                          old_value=_old_status)
        # WICHTIG: Versand persistieren, damit das EMAIL_VERSENDET-Audit
        # (und damit die "versendet"-Einstufung) einen Neustart überlebt.
        auto_save()

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


@app.route("/api/sent-mails")
def api_sent_mails_list():
    """Alle protokollierten Ausgangsmails – neueste zuerst (Menüpunkt 'Mails')."""
    mails = sorted(_load_sent_mails(), key=lambda m: m.get("sent_at", ""), reverse=True)
    return _json({"mails": mails, "count": len(mails)})


@app.route("/api/sent-mails/<mid>", methods=["DELETE"])
def api_sent_mails_delete(mid):
    mails = _load_sent_mails()
    remaining = [m for m in mails if m.get("id") != mid]
    if len(remaining) < len(mails):
        _save_sent_mails(remaining)
        return _json({"deleted": True})
    return _json({"error": "Nicht gefunden"}, 404)


@app.route("/api/invoices/<inv_id>/pdf")
def api_invoice_pdf(inv_id):
    """Liefert das Rechnungs-PDF als Download. Dateiname = <Rechnungsnummer>.pdf.

    Frontend nutzt diesen Endpoint, um nach dem Mail-Versand automatisch eine
    lokale Kopie in den Downloads-Ordner des Browsers ablegen zu lassen
    (opt-in pro Nutzer im Send-Dialog).
    """
    inv = invoices.get(inv_id)
    if not inv:
        return _json({"error": "Rechnung nicht gefunden"}, 404)
    try:
        filename, pdf_bytes = _build_invoice_pdf(inv)
    except Exception as e:
        import traceback
        print(f"[/pdf] PDF-Erzeugung fehlgeschlagen für Rechnung {inv.invoice_number}: {e}")
        print(traceback.format_exc())
        return _json({"error": f"PDF-Erzeugung fehlgeschlagen: {e}"}, 500)
    # ?inline=1 → im Browser-Tab anzeigen (Vorschau vor Versand); sonst Download.
    disposition = "inline" if request.args.get("inline") in ("1", "true", "yes") else "attachment"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


@app.route("/api/email/send-log")
def api_email_send_log():
    """Zeigt das Versandprotokoll."""
    return _json({"logs": email_sender.get_logs(), "count": len(email_sender.logs)})


@app.route("/api/email/delivery-check", methods=["POST"])
def api_email_delivery_check():
    """Zustellprüfung: gleicht das Versandprotokoll gegen Bounces im
    Absender-Postfach ab und markiert jede Mail als zugestellt / angenommen /
    unzustellbar. 'HTTP 200 beim Senden' heißt nur 'vom Relay angenommen' —
    erst dieser Abgleich bestätigt die tatsächliche Zustellung."""
    import mail_check
    cfg = {
        "imap_host": email_config.imap_host,
        "imap_port": email_config.imap_port,
        "imap_user": email_config.imap_user,
        "imap_password": email_config.imap_password,
        "smtp_user": email_config.smtp_user,
        "smtp_password": email_config.smtp_password,
    }
    if not cfg["imap_host"] or not (cfg["smtp_password"] or cfg["imap_password"]):
        return _json({"success": False,
                      "error": "Postfach (IMAP) nicht konfiguriert – Zustellprüfung "
                               "braucht Zugriff auf das Absender-Postfach."}, 400)
    try:
        bounces = mail_check.scan_bounces(cfg)
    except Exception as e:
        return _json({"success": False,
                      "error": f"Postfach-Zugriff fehlgeschlagen: {e}"}, 502)

    mails = _load_sent_mails()
    summary = mail_check.reconcile(mails, bounces)
    _save_sent_mails(mails)
    summary["success"] = True
    return _json(summary)


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


# ── Externer API-Endpunkt: Energieausweis-Backoffice (optional) ─────
EXTERNAL_API_KEY_FILE = _DATA / "data" / "external-api.json"

VARIANT_TO_ART_NR = {
    "WG-V": "P-1001",
    "WG-B": "P-1002",
    "NWG-V": "P-1003",
}

# Nicht zulässige Ausweis-Varianten – werden nicht angeboten und NICHT berechnet.
# NWG-B (Nichtwohngebäude Bedarfsausweis) ist gesperrt.
BLOCKED_VARIANTS = {"NWG-B"}

# Bedarf (B) + Verbrauch (V) je Gebäudetyp – für den "beides"-Fall (cert.bothMethods),
# bei dem EIN Ausweis Bedarf UND Verbrauch abdeckt und BEIDE Beträge fällig werden.
# NWG nur Verbrauch (NWG-B gesperrt) → "beides" gilt nur für Wohngebäude.
BUILDINGTYPE_BOTH_VARIANTS = {
    "WG": ["WG-B", "WG-V"],
}


def _epbd_invoice_lines(cert, products):
    """Bestimmt die Rechnungspositionen aus der Art des Energieausweises.

    - Normalfall: eine Position aus cert.variant (WG-V/WG-B/NWG-V).
    - "beides" (cert.bothMethods=True): zwei Positionen – Bedarf + Verbrauch
      desselben Gebäudetyps; beide Beträge werden fällig.

    Rückgabe: (lines, total_net, tax_rate, label, missing_variants)
    """
    variant = cert.get("variant") or ""
    building = "NWG" if variant.startswith("NWG") else "WG"
    wanted = (BUILDINGTYPE_BOTH_VARIANTS.get(building, [variant])
              if cert.get("bothMethods") else [variant])

    lines, used, missing = [], [], []
    total_net = 0.0
    tax_rate = 19.0
    for v in wanted:
        if v in BLOCKED_VARIANTS:
            continue  # gesperrte Variante (z. B. NWG-B) – nie berechnen
        art = VARIANT_TO_ART_NR.get(v)
        prod = next((p for p in products if p.get("art_nr") == art), None) if art else None
        if not prod:
            missing.append(v)
            continue
        net = float(prod.get("vk_price") or 0)
        tax_rate = float(prod.get("tax_rate") or 19)
        lines.append({
            "quantity": 1,
            "name": prod.get("name") or f"Energieausweis {v}",
            "price": net,
            "net": net,
            "tax_rate": tax_rate,
        })
        used.append(v)
        total_net += net
    label = " + ".join(used) if used else variant
    return lines, round(total_net, 2), tax_rate, label, missing

def _load_external_api_key():
    try:
        with open(EXTERNAL_API_KEY_FILE) as f:
            return json.load(f).get("apiKey")
    except Exception:
        return None

# Eigener, unabhängig rotierbarer Key NUR für die read-only Vorgangs-API
# (Büroassistent). Wird für Schreib-Endpoints NICHT akzeptiert.
ASSISTANT_API_KEY_FILE = _DATA / "data" / "assistant-api.json"

def _load_assistant_api_key():
    try:
        with open(ASSISTANT_API_KEY_FILE) as f:
            return json.load(f).get("apiKey")
    except Exception:
        return None

def _read_api_key_ok():
    """Read-only Vorgangs-API akzeptiert den Assistenten-Key ODER den Haupt-Key.
    Key kommt per Header `X-API-Key` ODER Query-Parameter `apiKey`/`api_key`."""
    provided = (request.headers.get("X-API-Key")
                or request.args.get("apiKey")
                or request.args.get("api_key") or "").strip()
    if not provided:
        return False
    return any(k and provided == k
               for k in (_load_assistant_api_key(), _load_external_api_key()))

def _load_products() -> list[dict]:
    p = _DATA / "data" / "products.json"
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return []

@app.route("/api/external/create-from-epbd", methods=["POST"])
def api_external_create_from_epbd():
    expected = _load_external_api_key()
    provided = request.headers.get("X-API-Key", "")
    if not expected or provided != expected:
        return _json({"error": "Ungültiger oder fehlender API-Key"}, 401)

    data = request.get_json(silent=True) or {}
    cert = data.get("cert") or {}
    customer = data.get("customer") or {}
    if not cert or not customer:
        return _json({"error": "cert und customer im Body erforderlich"}, 400)

    variant = cert.get("variant") or ""
    if variant in BLOCKED_VARIANTS:
        return _json({"error": "Variante NWG-B (Nichtwohngebäude Bedarfsausweis) ist "
                               "nicht zulässig und gesperrt – es wurde keine Rechnung erstellt."}, 400)
    products = _load_products()
    inv_lines, total_net, tax, line_label, missing_variants = _epbd_invoice_lines(cert, products)
    if not inv_lines:
        return _json({"error": f"Keine Produkt-Zuordnung für Variante '{variant}' "
                               f"(fehlend: {', '.join(missing_variants) or variant}; "
                               f"verfügbar: WG-V, WG-B, NWG-V)"}, 400)

    # Kunden-Existenz-Check per E-Mail (case-insensitive); falls vorhanden → bestehende Daten nutzen
    email = (customer.get("email") or "").strip()
    vorname = (customer.get("vorname") or "").strip()
    nachname = (customer.get("nachname") or customer.get("firma") or "").strip()
    full_name = (vorname + " " + nachname).strip() or "Unbekannt"
    firma = (customer.get("firma") or "").strip()

    buyers = _load_buyers()
    existing_buyer = None
    if email:
        for b in buyers:
            if (b.get("email") or "").strip().lower() == email.lower():
                existing_buyer = b
                break

    # Adressfelder: bestehende Buyer-Daten haben Vorrang, sonst aus customer (jetzt mit getrennter house_number + phone)
    customer_street = (customer.get("strasse") or "").strip()
    customer_house  = (customer.get("hausnummer") or "").strip()
    customer_phone  = (customer.get("tel") or customer.get("telefon") or "").strip()

    if existing_buyer:
        b_street = existing_buyer.get("street") or customer_street
        b_house  = existing_buyer.get("house_number") or customer_house
        b_plz    = existing_buyer.get("post_code") or (customer.get("plz") or "")
        b_city   = existing_buyer.get("city") or (customer.get("ort") or "")
        b_phone  = existing_buyer.get("phone") or customer_phone
        b_firma  = existing_buyer.get("name") or firma
        b_anrede = existing_buyer.get("salutation") or (customer.get("anrede") or "")
        b_contact = existing_buyer.get("contact_name") or full_name
        b_vat    = existing_buyer.get("vat_id") or ""
        b_id     = existing_buyer.get("id")
    else:
        b_street = customer_street
        b_house  = customer_house
        b_plz    = (customer.get("plz") or "").strip()
        b_city   = (customer.get("ort") or "").strip()
        b_phone  = customer_phone
        b_firma  = firma
        b_anrede = (customer.get("anrede") or "").strip()
        b_contact = full_name
        b_vat    = ""
        b_id     = ""

    # Mandant-Daten (Verkäufer / Bankverbindung)
    try:
        with open(_DATA / "data" / "mandant_settings.json") as f:
            mandant = json.load(f)
    except Exception:
        mandant = {}

    today_iso = date.today().isoformat()
    addr = (cert.get("gebaeude") or {}).get("adresseFormatiert") or ""
    note_subject = f"Energieausweis {line_label}"
    if addr:
        note_subject += f" · {addr}"
    # EA-ID IMMER in die Notiz (zusätzlich zur buyer_reference) — damit Bank-Zahlungen,
    # die die EA-ID im Verwendungszweck tragen, im Zahlungslauf zuverlässig matchen.
    if cert.get("id"):
        note_subject += f" · EA-ID {cert['id']}"
    if cert.get("vorgangsnummer"):
        note_subject += f" · Vorgang {cert['vorgangsnummer']}"
    if cert.get("vorlRegistriernummer"):
        note_subject += f" · Reg-Nr (vorl.) {cert['vorlRegistriernummer']}"

    # Payload für /api/generate zusammenbauen
    inv_payload = {
        "date": today_iso,
        "delivery_date": today_iso,
        "type": "380",
        "currency": "EUR",
        "buyer_reference": cert.get("vorgangsnummer") or cert.get("id") or "",
        "note": note_subject,
        # Seller (Mandant)
        "seller_name": mandant.get("name", ""),
        "seller_street": mandant.get("street", ""),
        "seller_house_number": mandant.get("house_number", ""),
        "seller_postcode": mandant.get("post_code", ""),
        "seller_city": mandant.get("city", ""),
        "seller_email": mandant.get("email", ""),
        "seller_contact": mandant.get("contact_name", ""),
        "seller_phone": mandant.get("contact_phone", ""),
        "seller_contact_email": mandant.get("email", ""),
        "seller_vat": mandant.get("vat_id", ""),
        "seller_iban": (mandant.get("iban") or "").replace(" ", ""),
        "seller_bic": mandant.get("bic", ""),
        # Buyer
        "buyer_id": b_id,
        "buyer_name": b_firma,
        "buyer_salutation": b_anrede,
        "buyer_contact_name": b_contact,
        "buyer_street": b_street,
        "buyer_house_number": b_house,
        "buyer_postcode": b_plz,
        "buyer_city": b_city,
        "buyer_email": email,
        "buyer_phone": b_phone,
        "buyer_vat": b_vat,
        # Payment
        "payment_code": "58",
        "payment_terms": "Zahlbar sofort",
        # Lines – eine Position, bei "beides" (bothMethods) zwei (Bedarf + Verbrauch)
        "lines": inv_lines,
    }

    # Über internen Test-Client an /api/generate weiterleiten — gleiche Logik wie UI-Aufruf.
    # Wichtig: Session als logged_in markieren, sonst greift @before_request require_login.
    send_result = None
    try:
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["logged_in"] = True
                sess["username"]  = "api:external-create-from-epbd"
            r = client.post("/api/generate", json=inv_payload)
            inv_resp = r.get_json() or {}
            if r.status_code != 200 or not inv_resp.get("number"):
                return _json({"error": f"Rechnungs-Erzeugung fehlgeschlagen: {inv_resp.get('error') or r.status_code}"}, 500)
            # Optional (send=true): Rechnung direkt freigeben + per E-Mail an den Kunden senden
            if data.get("send") and email and inv_resp.get("id"):
                _sr = client.post(f"/api/invoices/{inv_resp['id']}/send", json={"recipient": email})
                send_result = _sr.get_json() or {}
    except Exception as e:
        return _json({"error": f"Interner Aufruf gescheitert: {e}"}, 500)

    inv_id = inv_resp.get("id")
    inv_number = inv_resp.get("number")
    gross = float(inv_resp.get("gross") or 0)

    resp = {
        "ok": True,
        "invoice_id": inv_id,
        "invoice_number": inv_number,
        "buyer_existed": bool(existing_buyer),
        "buyer_id": b_id or None,
        "buyer_name": b_firma or full_name,
        "amount_net": total_net,
        "tax_rate": tax,
        "amount_gross": gross,
        "positions": line_label,
        "subject": note_subject,
        "url": f"/#inv/{inv_id}",
        "sent": bool(send_result and send_result.get("success")),
        "send_recipient": (send_result.get("recipient") if send_result else None),
        "send_error": (send_result.get("error") if send_result and not send_result.get("success") else None),
    }
    if missing_variants:
        # z. B. NWG-B hat (noch) kein Produkt → nur die verfügbare Position berechnet.
        resp["warning"] = ("Für folgende Ausweis-Art(en) ist kein Produkt/Preis "
                           "hinterlegt, daher nicht berechnet: " + ", ".join(missing_variants))
    return _json(resp)

# ── Generische externe Rechnungserstellung (z. B. Seminar-/Schulungs-Buchungen) ──
# ── Externe READ-ONLY Vorgangs-API (für Büroassistent) ───────────────
# Fragt die aktuelle Vorgangssituation ab. Auth wie alle /api/external/*
# per X-API-Key (data/external-api.json). KEIN Schreibzugriff.

_VORGANG_STATUS_TEXT = {
    "NEU": "Neu", "IN_BEARBEITUNG": "In Bearbeitung",
    "ABGESCHLOSSEN": "Abgeschlossen", "STORNIERT": "Storniert",
}


def _vorgang_situation(txn, detail=False):
    """Kompakte Situations-Sicht eines Vorgangs für die externe API."""
    steps = txn.get("steps", {}) or {}
    has_supplier = bool((txn.get("supplier_id") or txn.get("supplier_name") or "").strip())
    supplier_steps = ("supplier_quote", "purchase_order", "supplier_invoice")
    # Relevante Schritte: Lieferantenschritte nur, wenn ein Lieferant hinterlegt ist
    relevant = [k for k in STEP_KEYS if has_supplier or k not in supplier_steps]

    # Aktueller Schritt = erster relevanter, der weder freigegeben noch übersprungen ist
    current = None
    for k in relevant:
        s = steps.get(k, {}) or {}
        if not s.get("approved") and s.get("status") != "UEBERSPRUNGEN":
            current = k
            break
    erledigt = sum(1 for k in relevant if (steps.get(k, {}) or {}).get("approved"))

    # Betrag (brutto) vom weitest fortgeschrittenen Dokument mit Positionen
    betrag = 0.0
    for k in ("invoice", "order_confirmation", "customer_quote"):
        st = steps.get(k, {}) or {}
        if st.get("positions"):
            try:
                betrag = calc_step_totals(st).get("total_gross", 0.0)
            except Exception:
                betrag = 0.0
            break

    status = txn.get("status", "")
    if status == "STORNIERT":
        wartet_auf, naechste_aktion = None, "Storniert (Angebot nicht angenommen)"
    elif status == "ABGESCHLOSSEN" or current is None:
        wartet_auf, naechste_aktion = None, "Vorgang abgeschlossen"
    elif current == "order_intake":
        wartet_auf, naechste_aktion = "kunde", "Warten auf Auftragsbestätigung des Kunden"
    else:
        wartet_auf = "uns"
        naechste_aktion = f"{STEP_LABELS.get(current, current)} bearbeiten / freigeben"

    # Rechnung + Fälligkeit (datumsbasiert; keine Zahlungsprüfung)
    inv = steps.get("invoice", {}) or {}
    rechnung = None
    if inv.get("reference") or inv.get("approved"):
        faellig = inv.get("due_date")
        ueberfaellig, tage = False, None
        if faellig and status != "ABGESCHLOSSEN":
            try:
                delta = (date.today() - date.fromisoformat(str(faellig)[:10])).days
                if delta > 0:
                    ueberfaellig, tage = True, delta
            except Exception:
                pass
        rechnung = {
            "referenz": inv.get("reference"),
            "freigegeben": bool(inv.get("approved")),
            "faellig_am": faellig,
            "ueberfaellig_nach_datum": ueberfaellig,
            "tage_ueberfaellig": tage,
        }

    out = {
        "id": txn.get("id"),
        "betreff": txn.get("subject", ""),
        "kunde": txn.get("buyer_name", ""),
        "lieferant": txn.get("supplier_name", "") if has_supplier else "",
        "status": status,
        "status_text": _VORGANG_STATUS_TEXT.get(status, status),
        "aktueller_schritt": current,
        "aktueller_schritt_text": STEP_LABELS.get(current) if current else None,
        "naechste_aktion": naechste_aktion,
        "wartet_auf": wartet_auf,
        "fortschritt": {"erledigt": erledigt, "relevant": len(relevant),
                        "text": f"{erledigt}/{len(relevant)}"},
        "betrag_brutto": round(betrag, 2),
        "waehrung": "EUR",
        "angelegt": txn.get("created_at"),
        "aktualisiert": txn.get("updated_at"),
        "rechnung": rechnung,
    }
    if detail:
        out["schritte"] = [{
            "key": k,
            "label": STEP_LABELS.get(k, k),
            "status": (steps.get(k, {}) or {}).get("status"),
            "freigegeben": bool((steps.get(k, {}) or {}).get("approved")),
            "freigegeben_am": (steps.get(k, {}) or {}).get("approved_at"),
            "referenz": (steps.get(k, {}) or {}).get("reference"),
        } for k in relevant]
    return out


def _vorgaenge_alle():
    """Alle Vorgänge als Situations-Sicht, neueste zuerst."""
    all_sit = [_vorgang_situation(t) for t in txn_mgr.list_all()]
    all_sit.sort(key=lambda s: (s.get("angelegt") or "", s.get("id") or ""), reverse=True)
    return all_sit


def _vorgaenge_zusammenfassung(all_sit):
    from collections import Counter
    sc = Counter(s["status"] for s in all_sit)
    return {
        "gesamt": len(all_sit),
        "neu": sc.get("NEU", 0),
        "in_bearbeitung": sc.get("IN_BEARBEITUNG", 0),
        "abgeschlossen": sc.get("ABGESCHLOSSEN", 0),
        "storniert": sc.get("STORNIERT", 0),
        "offen": sc.get("NEU", 0) + sc.get("IN_BEARBEITUNG", 0),
        "wartet_auf_kunde": sum(1 for s in all_sit if s["wartet_auf"] == "kunde"),
        "rechnungen_ueberfaellig": sum(
            1 for s in all_sit if (s.get("rechnung") or {}).get("ueberfaellig_nach_datum")),
        "offener_betrag_brutto": round(sum(
            s["betrag_brutto"] for s in all_sit
            if s["status"] in ("NEU", "IN_BEARBEITUNG")), 2),
    }


@app.route("/api/external/vorgaenge")
@app.route("/api/assistant/vorgaenge")
def api_external_vorgaenge():
    """Aktuelle Vorgangssituation (Liste + Zusammenfassung). Read-only.
    Filter: ?status=NEU|IN_BEARBEITUNG|ABGESCHLOSSEN|STORNIERT, ?offen=1.
    Auth: Header X-API-Key ODER ?apiKey=."""
    if not _read_api_key_ok():
        return _json({"error": "Ungültiger oder fehlender API-Key"}, 401)

    all_sit = _vorgaenge_alle()
    result = all_sit
    status = (request.args.get("status") or "").strip().upper()
    if status:
        result = [s for s in result if s["status"] == status]
    if request.args.get("offen") in ("1", "true", "yes"):
        result = [s for s in result if s["status"] in ("NEU", "IN_BEARBEITUNG")]

    return _json({
        "stand": datetime.now().isoformat(timespec="seconds"),
        "zusammenfassung": _vorgaenge_zusammenfassung(all_sit),
        "anzahl": len(result),
        "vorgaenge": result,
    })


@app.route("/api/external/status")
@app.route("/api/assistant/status")
def api_external_status():
    """Kurzer Statusüberblick — nur die Zusammenfassung. Read-only."""
    if not _read_api_key_ok():
        return _json({"error": "Ungültiger oder fehlender API-Key"}, 401)
    return _json({
        "stand": datetime.now().isoformat(timespec="seconds"),
        "zusammenfassung": _vorgaenge_zusammenfassung(_vorgaenge_alle()),
    })


@app.route("/api/external/vorgaenge/<vid>")
@app.route("/api/assistant/vorgaenge/<vid>")
def api_external_vorgang(vid):
    """Einzelnen Vorgang inkl. Schritt-Details abfragen. Read-only.
    Auth: Header X-API-Key ODER ?apiKey=."""
    if not _read_api_key_ok():
        return _json({"error": "Ungültiger oder fehlender API-Key"}, 401)
    txn = txn_mgr.get(vid)
    if not txn:
        return _json({"error": "Vorgang nicht gefunden"}, 404)
    return _json({
        "stand": datetime.now().isoformat(timespec="seconds"),
        "vorgang": _vorgang_situation(txn, detail=True),
    })


@app.route("/api/external/create-invoice", methods=["POST"])
def api_external_create_invoice():
    expected = _load_external_api_key()
    provided = request.headers.get("X-API-Key", "")
    if not expected or provided != expected:
        return _json({"error": "Ungültiger oder fehlender API-Key"}, 401)

    data = request.get_json(silent=True) or {}
    customer = data.get("customer") or {}
    lines_in = data.get("lines") or []
    if not customer or not lines_in:
        return _json({"error": "customer und lines im Body erforderlich"}, 400)

    email = (customer.get("email") or "").strip()
    vorname = (customer.get("vorname") or "").strip()
    nachname = (customer.get("nachname") or customer.get("firma") or "").strip()
    full_name = (vorname + " " + nachname).strip() or "Unbekannt"
    firma = (customer.get("firma") or "").strip()
    customer_street = (customer.get("strasse") or "").strip()
    customer_house  = (customer.get("hausnummer") or "").strip()
    customer_phone  = (customer.get("tel") or customer.get("telefon") or "").strip()

    buyers = _load_buyers()
    existing_buyer = None
    if email:
        for b in buyers:
            if (b.get("email") or "").strip().lower() == email.lower():
                existing_buyer = b
                break
    if existing_buyer:
        b_street = existing_buyer.get("street") or customer_street
        b_house  = existing_buyer.get("house_number") or customer_house
        b_plz    = existing_buyer.get("post_code") or (customer.get("plz") or "")
        b_city   = existing_buyer.get("city") or (customer.get("ort") or "")
        b_phone  = existing_buyer.get("phone") or customer_phone
        b_firma  = existing_buyer.get("name") or firma or full_name
        b_anrede = existing_buyer.get("salutation") or (customer.get("anrede") or "")
        b_contact = existing_buyer.get("contact_name") or full_name
        b_vat    = existing_buyer.get("vat_id") or ""
        b_id     = existing_buyer.get("id")
    else:
        b_street = customer_street
        b_house  = customer_house
        b_plz    = (customer.get("plz") or "").strip()
        b_city   = (customer.get("ort") or "").strip()
        b_phone  = customer_phone
        b_firma  = firma or full_name
        b_anrede = (customer.get("anrede") or "").strip()
        b_contact = full_name
        b_vat    = ""
        b_id     = ""

    try:
        with open(_DATA / "data" / "mandant_settings.json") as f:
            mandant = json.load(f)
    except Exception:
        mandant = {}

    inv_date = (data.get("invoice_date") or date.today().isoformat()).strip()
    try:
        payment_days = int(data.get("payment_days") or 7)
    except Exception:
        payment_days = 7

    inv_lines = []
    for ln in lines_in:
        try: qty = float(ln.get("quantity") or 1)
        except Exception: qty = 1.0
        try: price = float(ln.get("unit_price") if ln.get("unit_price") is not None else ln.get("price") or 0)
        except Exception: price = 0.0
        try: tax = float(ln.get("tax_rate") if ln.get("tax_rate") is not None else 19)
        except Exception: tax = 19.0
        inv_lines.append({"name": ln.get("name") or "", "quantity": qty, "price": price,
                          "net": round(price * qty, 2), "tax_rate": tax})
    if not any(l["name"] for l in inv_lines):
        return _json({"error": "Keine gültigen Positionen"}, 400)

    inv_payload = {
        "date": inv_date, "delivery_date": inv_date, "type": "380", "currency": "EUR",
        "buyer_reference": (data.get("buyer_reference") or "").strip(),
        "note": (data.get("note") or "").strip(),
        "seller_name": mandant.get("name", ""), "seller_street": mandant.get("street", ""),
        "seller_house_number": mandant.get("house_number", ""), "seller_postcode": mandant.get("post_code", ""),
        "seller_city": mandant.get("city", ""), "seller_email": mandant.get("email", ""),
        "seller_contact": mandant.get("contact_name", ""), "seller_phone": mandant.get("contact_phone", ""),
        "seller_contact_email": mandant.get("email", ""), "seller_vat": mandant.get("vat_id", ""),
        "seller_iban": (mandant.get("iban") or "").replace(" ", ""), "seller_bic": mandant.get("bic", ""),
        "buyer_id": b_id, "buyer_name": b_firma, "buyer_salutation": b_anrede, "buyer_contact_name": b_contact,
        "buyer_street": b_street, "buyer_house_number": b_house, "buyer_postcode": b_plz, "buyer_city": b_city,
        "buyer_email": email, "buyer_phone": b_phone, "buyer_vat": b_vat,
        "payment_code": "58",
        "payment_terms": "Zahlbar sofort" if payment_days <= 0 else f"Zahlbar innerhalb von {payment_days} Tagen",
        "lines": inv_lines,
    }

    send_result = None
    try:
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["logged_in"] = True
                sess["username"]  = "api:external-create-invoice"
            r = client.post("/api/generate", json=inv_payload)
            inv_resp = r.get_json() or {}
            if r.status_code != 200 or not inv_resp.get("number"):
                return _json({"error": f"Rechnungs-Erzeugung fehlgeschlagen: {inv_resp.get('error') or r.status_code}"}, 500)
            if data.get("send") and email and inv_resp.get("id"):
                _sr = client.post(f"/api/invoices/{inv_resp['id']}/send", json={"recipient": email})
                send_result = _sr.get_json() or {}
    except Exception as e:
        return _json({"error": f"Interner Aufruf gescheitert: {e}"}, 500)

    return _json({
        "ok": True,
        "invoice_id": inv_resp.get("id"),
        "invoice_number": inv_resp.get("number"),
        "amount_gross": float(inv_resp.get("gross") or 0),
        "buyer_existed": bool(existing_buyer),
        "sent": bool(send_result and send_result.get("success")),
        "send_error": (send_result.get("error") if send_result and not send_result.get("success") else None),
    })


@app.route("/api/external/create-offer", methods=["POST"])
def api_external_create_offer():
    """Kundenangebot (customer_quote) von extern anlegen + optional versenden.
    Genutzt von der Förder-Matrix der Beratungs-Web-App des Betreibers: Kunde + Angebotspreis →
    Vorgang mit Angebots-Step → approve-and-send (PDF + E-Mail). 26.07.2026."""
    expected = _load_external_api_key()
    provided = request.headers.get("X-API-Key", "")
    if not expected or provided != expected:
        return _json({"error": "Ungültiger oder fehlender API-Key"}, 401)

    data = request.get_json(silent=True) or {}
    customer = data.get("customer") or {}
    vorname = (customer.get("vorname") or "").strip()
    nachname = (customer.get("nachname") or customer.get("firma") or "").strip()
    full_name = (vorname + " " + nachname).strip() or "Unbekannt"
    email = (customer.get("email") or "").strip()

    # 1. Buyer anlegen/finden (dedup über _save_buyer_if_new; bestehende nicht überschreiben)
    buyer = _save_buyer_if_new(
        name=(customer.get("firma") or "").strip(),
        street=(customer.get("strasse") or "").strip(),
        house_number=(customer.get("hausnummer") or "").strip(),
        post_code=str(customer.get("plz") or "").strip(),
        city=(customer.get("ort") or "").strip(),
        email=email,
        phone=(customer.get("tel") or customer.get("telefon") or "").strip(),
        reference=(customer.get("quelle") or "Förder-Matrix").strip(),
        salutation=(customer.get("anrede") or "").strip(),
        contact_name=full_name,
        is_private=bool(customer.get("is_private", True)),
    )
    if not buyer:
        return _json({"error": "Weder Firma noch Name angegeben"}, 400)
    buyer_id = buyer.get("id", "")
    buyer_name = buyer.get("name") or full_name

    # 2. Positionen: entweder lines/positions-Array ODER ein Einzelposten {leistung, angebotspreis}
    def _mkpos(i, desc, qty, unit, price, tax):
        return {"pos_nr": i, "description": desc, "quantity": qty, "unit": unit, "unit_price": price,
                "discount_percent": 0, "discount_amount": 0, "net_amount": round(qty * price, 2), "tax_rate": tax}
    positions = []
    lines_in = data.get("positions") or data.get("lines") or []
    if lines_in:
        for i, p in enumerate(lines_in, 1):
            try: qty = float(p.get("quantity") or 1)
            except Exception: qty = 1.0
            try: price = float(p.get("unit_price") if p.get("unit_price") is not None else (p.get("price") or 0))
            except Exception: price = 0.0
            try: tax = float(p.get("tax_rate") if p.get("tax_rate") is not None else 19)
            except Exception: tax = 19.0
            positions.append(_mkpos(i, (p.get("description") or p.get("name") or "").strip(), qty, (p.get("unit") or "Stk"), price, tax))
    else:
        try: price = float(data.get("angebotspreis") if data.get("angebotspreis") is not None else (data.get("preis") or 0))
        except Exception: price = 0.0
        try: tax = float(data.get("tax_rate") if data.get("tax_rate") is not None else 19)
        except Exception: tax = 19.0
        desc = (data.get("leistung") or "Energieberatung — Förder- und Finanzierungsberatung").strip()
        positions.append(_mkpos(1, desc, 1, "pauschal", price, tax))
    if not any(p["description"] for p in positions) or not any(p["net_amount"] for p in positions):
        return _json({"error": "Keine gültige Angebotsposition (Leistung/Preis fehlt)"}, 400)

    subject = (data.get("subject") or "Angebot Förder- und Finanzierungsberatung").strip()
    note = (data.get("note") or "").strip()
    send = bool(data.get("send"))

    # 3. Vorgang anlegen → customer_quote-Step befüllen → freigeben (+ senden)
    try:
        txn = txn_mgr.create({"subject": subject, "buyer_id": buyer_id, "buyer_name": buyer_name, "notes": note})
        tid = txn.get("id")
        txn_mgr.update_step(tid, "customer_quote",
                            {"positions": positions, "date": date.today().isoformat(), "intro_text": note},
                            "api:external-create-offer")
        sent = False; send_error = None; offer_ref = None
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["logged_in"] = True
                sess["username"] = "api:external-create-offer"
            if send and email:
                r = client.post(f"/api/transactions/{tid}/steps/customer_quote/approve-and-send",
                                json={"to_email": email, "user": "api:external-create-offer"})
                rj = r.get_json() or {}
                sent = bool(rj.get("sent")); send_error = rj.get("error")
                offer_ref = (rj.get("document") or {}).get("reference") if isinstance(rj.get("document"), dict) else None
            else:
                client.post(f"/api/transactions/{tid}/steps/customer_quote/approve", json={"user": "api:external-create-offer"})
    except Exception as e:
        return _json({"error": f"Angebot-Erzeugung fehlgeschlagen: {e}"}, 500)

    return _json({"ok": True, "transaction_id": tid, "offer_reference": offer_ref,
                  "buyer_id": buyer_id, "sent": sent, "send_error": send_error})


@app.route("/api/external/invoice-status/<inv_id>")
def api_external_invoice_status(inv_id):
    """Read-only Zahlstatus einer Rechnung (per invoice_id ODER Rechnungsnummer).
    Genutzt von der EPBD-App als Zahlungs-Gate vor der Ausweis-Freigabe."""
    if not _read_api_key_ok():
        return _json({"error": "Ungültiger oder fehlender API-Key"}, 401)
    inv = invoices.get(inv_id)
    if not inv:
        wanted = (inv_id or "").strip().upper()
        for i in invoices.values():
            if (getattr(i, "invoice_number", "") or "").upper() == wanted:
                inv = i
                break
    if not inv:
        return _json({"error": "Rechnung nicht gefunden"}, 404)
    return _json({
        "ok": True,
        "invoice_id": inv._id,
        "invoice_number": getattr(inv, "invoice_number", "") or "",
        "status": getattr(inv, "status", "") or "",
        "paid": _is_invoice_paid(inv),
        "paid_at": getattr(inv, "_paid_at", None),
        "gross": float(getattr(inv, "gross", 0) or 0),
    })

@app.route("/api/external/eingangsrechnung", methods=["POST"])
def api_external_eingangsrechnung():
    """Legt eine Eingangsrechnung (Lieferantenrechnung) extern ab.
    Auth: NUR Haupt-Key (X-API-Key = data/external-api.json) — Schreib-Endpoint.

    Zwei Aufruf-Formen:
      (a) multipart/form-data: 'file' = Original-PDF, optional 'data' = JSON-String.
          Fehlen die Kernfelder (seller_name + Betrag), extrahiert die KI sie aus der PDF.
      (b) application/json: nur strukturierte Daten, ohne PDF.

    Felder (wie /api/eingang/confirm): seller_name, seller_street, seller_house_number,
    seller_postcode, seller_city, seller_email, seller_phone, seller_vat_id, seller_iban,
    invoice_number, invoice_date, due_date, currency, note, payment_terms, tax_rate,
    lines:[{description,quantity,unit_price,net_amount,tax_rate}] ODER net_amount/gross_amount.

    Unbekannter Lieferant wird automatisch in den Stammdaten angelegt (unapproved)."""
    expected = _load_external_api_key()
    provided = request.headers.get("X-API-Key", "")
    if not expected or provided != expected:
        return _json({"error": "Ungültiger oder fehlender API-Key"}, 401)

    pdf_bytes = b""
    d = {}
    f = request.files.get("file")
    if f and f.filename:
        pdf_bytes = f.read()
        raw = request.form.get("data")
        if raw:
            try:
                d = json.loads(raw)
            except Exception as e:
                return _json({"error": f"Ungültiges JSON in data: {e}"}, 400)
        # Dateiname mitfuehren: er benennt die Anlage und verraet ihre Rolle
        # ('Receipt-….pdf' = Zahlungsbeleg).
        d["_dateiname"] = f.filename
    else:
        d = request.get_json(silent=True) or {}

    def _has_amount(x):
        return bool(x.get("lines") or x.get("net_amount") or x.get("gross_amount"))

    # Kernfelder fehlen, aber PDF da → KI-Extraktion; explizit übergebene Felder gewinnen
    ocr_used = False
    if pdf_bytes and not ((d.get("seller_name") or "").strip() and _has_amount(d)):
        res = _ocr_extract_from_pdf(pdf_bytes)
        if res.get("error"):
            return _json({"error": f"KI-Extraktion fehlgeschlagen: {res['error']}"},
                         res.get("_status", 502))
        extracted = res.get("extracted") or {}
        extracted.update({k: v for k, v in d.items() if v not in (None, "", [])})
        d = extracted
        ocr_used = True

    if not (d.get("seller_name") or "").strip():
        return _json({"error": "seller_name fehlt (weder übergeben noch aus PDF extrahierbar)"}, 400)
    if not _has_amount(d):
        return _json({"error": "Betrag fehlt: lines ODER net_amount/gross_amount erforderlich"}, 400)

    # Nur Brutto übergeben → Netto herausrechnen (Helper behandelt den Wert als Netto)
    if not d.get("lines") and not d.get("net_amount") and d.get("gross_amount"):
        try:
            rate = float(d.get("tax_rate") if d.get("tax_rate") is not None else 19)
            d["net_amount"] = round(float(d["gross_amount"]) / (1 + rate / 100), 2)
        except Exception:
            pass

    d.setdefault("note", "Eingangsrechnung abgelegt via externe API")

    # Lieferant matchen (USt-ID > Name) — unbekannt → automatisch anlegen
    supplier = _match_eingang_supplier(d)
    supplier_created = False
    if not supplier:
        try:
            before_ids = {s.get("id") for s in supplier_mgr.list_all()}
            street = " ".join(x for x in [(d.get("seller_street") or "").strip(),
                                          (d.get("seller_house_number") or "").strip()] if x)
            s = supplier_mgr.add({
                "name": (d.get("seller_name") or "").strip(),
                "street": street,
                "post_code": (d.get("seller_postcode") or "").strip(),
                "city": (d.get("seller_city") or "").strip(),
                "email": (d.get("seller_email") or "").strip(),
                "phone": (d.get("seller_phone") or "").strip(),
                "vat_id": (d.get("seller_vat_id") or "").replace(" ", ""),
                "iban": (d.get("seller_iban") or "").replace(" ", ""),
                "notes": "Automatisch angelegt via /api/external/eingangsrechnung",
            })
            supplier_created = s.get("id") not in before_ids
            supplier = {"id": s.get("id"), "name": s.get("name"),
                        "match_by": "auto_created" if supplier_created else "name"}
        except Exception as e:
            return _json({"error": f"Lieferanten-Anlage fehlgeschlagen: {e}"}, 500)

    try:
        inv, report = _create_eingang_invoice(pdf_bytes, d)
    except _EingangError as e:
        if e.status == 409:
            det = getattr(e, "details", {}) or {}
            num = (d.get("invoice_number") or "").strip()
            ex = next((i for i in invoices.values() if i.invoice_number == num), None)
            antwort = {"ok": False, "duplicate": True, "error": str(e),
                       "invoice_id": det.get("invoice_id") or (ex._id if ex else None),
                       "supplier": supplier, "supplier_created": supplier_created}
            for k in ("angehaengt", "anlage", "rolle", "schon_vorhanden", "anlage_fehler"):
                if k in det:
                    antwort[k] = det[k]
            return _json(antwort, 409)
        return _json({"error": str(e), "supplier": supplier,
                      "supplier_created": supplier_created}, e.status)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return _json({"error": f"Anlage fehlgeschlagen: {e}"}, 500)

    try:
        gross = float(inv.tax_inclusive_amount())
    except Exception:
        gross = None
    return _json({
        "ok": True,
        "invoice_id": inv._id,
        "invoice_number": inv.invoice_number,
        "supplier": supplier,
        "supplier_created": supplier_created,
        "gross": gross,
        "due_date": inv.payment.due_date.isoformat() if inv.payment and inv.payment.due_date else None,
        "ocr_used": ocr_used,
        "pdf_stored": bool(pdf_bytes),
        "valid": report.is_valid if report else None,
    }, 201)


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
    app.run(host="127.0.0.1", port=port, debug=False)


# Lokale Erweiterungen laden — am Modulende, damit alle oben definierten
# Namen bereitstehen und jeder Starter sie bekommt.
_load_extensions()
