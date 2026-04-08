"""
E-Rechnungssystem – REST API
FR-140: API für technischen Import, Validierung, Statusabfrage, Export

Nutzt Pythons http.server (stdlib) – in Produktion: FastAPI oder Flask.
Kann standalone gestartet werden: python api.py
"""
from __future__ import annotations
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json, sys, os

# Projekt-Module importieren
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from models import Invoice
from xrechnung_generator import generate_and_serialize
from xrechnung_parser import parse_xrechnung
from validator import validate_invoice
from inbox import Inbox
from archive import InvoiceArchive
from export import ExportManager
from dashboard import Dashboard


# ── Globaler App-State ─────────────────────────────────────────────────

class AppState:
    def __init__(self):
        self.inbox = Inbox()
        self.archive = InvoiceArchive("./archiv")
        self.exporter = ExportManager("./export")
        self.dashboard = Dashboard()
        self.invoices: dict[str, Invoice] = {}  # id → Invoice

APP = AppState()


# ── Request Handler ────────────────────────────────────────────────────

class APIHandler(BaseHTTPRequestHandler):

    def _json_response(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length > 0 else b""

    def _error(self, msg: str, status: int = 400):
        self._json_response({"error": msg}, status)

    # ── Routing ────────────────────────────────────────────────

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = parse_qs(parsed.query)

        if path == "/api/health":
            self._json_response({"status": "ok", "version": "1.0.0"})

        elif path == "/api/invoices":
            invoices = [{
                "id": inv._id, "number": inv.invoice_number,
                "date": str(inv.invoice_date), "seller": inv.seller.name,
                "gross": str(inv.tax_inclusive_amount()), "status": inv.status,
            } for inv in APP.invoices.values()]
            self._json_response({"invoices": invoices, "count": len(invoices)})

        elif path.startswith("/api/invoices/"):
            inv_id = path.split("/")[-1]
            inv = APP.invoices.get(inv_id)
            if inv:
                self._json_response(inv.to_dict())
            else:
                self._error("Rechnung nicht gefunden.", 404)

        elif path == "/api/archive":
            q = {k: v[0] for k, v in params.items()}
            results = APP.archive.search(**q) if q else APP.archive.list_all()
            self._json_response({"results": results, "count": len(results)})

        elif path == "/api/archive/integrity":
            inv_id = params.get("id", [None])[0]
            if inv_id:
                ok, msg = APP.archive.verify_integrity(inv_id)
                self._json_response({"invoice_id": inv_id, "valid": ok, "message": msg})
            else:
                self._error("Parameter 'id' erforderlich.")

        elif path == "/api/dashboard":
            kpis = APP.dashboard.compute_all()
            self._json_response({
                "kpis": [{"name": k.name, "value": k.value, "unit": k.unit,
                          "detail": k.detail} for k in kpis]
            })

        elif path == "/api/inbox":
            items = [{
                "id": item.item_id, "filename": item.filename,
                "status": item.status, "format": item.format_type,
                "received": item.received_at, "error": item.error,
                "invoice_number": item.invoice.invoice_number if item.invoice else "",
            } for item in APP.inbox.items]
            self._json_response({"items": items, "count": len(items)})

        elif path == "/api/export/log":
            self._json_response({"log": APP.exporter.get_log()})

        else:
            self._error(f"Endpunkt nicht gefunden: {path}", 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/inbox/upload":
            # FR-140: Rechnungs-Import per API
            body = self._read_body()
            content_type = self.headers.get("Content-Type", "")
            filename = self.headers.get("X-Filename", "upload.xml")

            if not body:
                self._error("Leerer Request-Body.")
                return

            item = APP.inbox.receive_file(filename, body)
            if item.invoice:
                APP.invoices[item.invoice._id] = item.invoice
                APP.dashboard.add_invoice(item.invoice)
            APP.dashboard.add_inbox_item(item)

            self._json_response({
                "item_id": item.item_id,
                "status": item.status,
                "format": item.format_type,
                "invoice_number": item.invoice.invoice_number if item.invoice else "",
                "validation_valid": item.validation.is_valid if item.validation else None,
                "error": item.error,
            }, 201 if item.status == "VERARBEITET" else 400)

        elif path == "/api/validate":
            # Validierung ohne Speicherung
            body = self._read_body()
            if not body:
                self._error("XML-Body erforderlich.")
                return
            try:
                inv = parse_xrechnung(body)
                report = validate_invoice(inv)
                self._json_response(report.to_dict())
            except Exception as e:
                self._error(f"Parse-/Validierungsfehler: {e}")

        elif path.startswith("/api/invoices/") and path.endswith("/export"):
            inv_id = path.split("/")[-2]
            inv = APP.invoices.get(inv_id)
            if not inv:
                self._error("Rechnung nicht gefunden.", 404)
                return

            params = parse_qs(urlparse(self.path).query)
            fmt = params.get("format", ["DATEV"])[0]
            result = APP.exporter.export(inv, fmt)
            APP.dashboard.set_export_log(APP.exporter.get_log())

            self._json_response({
                "export_id": result.export_id,
                "success": result.success,
                "filename": result.filename,
                "error": result.error,
            }, 200 if result.success else 400)

        elif path.startswith("/api/invoices/") and path.endswith("/approve"):
            inv_id = path.split("/")[-2]
            inv = APP.invoices.get(inv_id)
            if not inv:
                self._error("Rechnung nicht gefunden.", 404)
                return

            try:
                data = json.loads(self._read_body())
            except (json.JSONDecodeError, Exception):
                data = {}

            user = data.get("user", "api-user")
            approved = data.get("approved", True)
            comment = data.get("comment", "")

            from wf_engine import WorkflowEngine
            wf = WorkflowEngine()

            if inv.status == "NEU":
                wf.start_workflow(inv, user)
            if inv.status == "IN_PRUEFUNG":
                msg = wf.sachliche_pruefung(inv, user, approved, comment)
            elif inv.status == "IN_FREIGABE":
                msg = wf.kaufmaennische_freigabe(inv, user, approved, comment)
            else:
                msg = f"Kein Workflow-Schritt für Status {inv.status}"

            self._json_response({
                "invoice_id": inv_id, "status": inv.status, "message": msg,
            })

        else:
            self._error(f"Endpunkt nicht gefunden: {path}", 404)

    def do_OPTIONS(self):
        """CORS Preflight"""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename")
        self.end_headers()

    def log_message(self, format, *args):
        """Überschrieben für sauberes Logging."""
        print(f"  [{self.log_date_time_string()}] {format % args}")


# ── API-Dokumentation ──────────────────────────────────────────────────

API_DOCS = """
╔══════════════════════════════════════════════════════════════╗
║              E-Rechnungssystem – REST API                   ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  GET  /api/health              → Systemstatus                ║
║  GET  /api/invoices            → Alle Rechnungen             ║
║  GET  /api/invoices/<id>       → Rechnungsdetail             ║
║  GET  /api/inbox               → Eingangspostfach            ║
║  GET  /api/archive             → Archivsuche (?key=value)    ║
║  GET  /api/archive/integrity   → Integritätsprüfung (?id=x)  ║
║  GET  /api/dashboard           → KPIs                        ║
║  GET  /api/export/log          → Export-Protokoll            ║
║                                                              ║
║  POST /api/inbox/upload        → Rechnung importieren        ║
║       Header: X-Filename, Body: XML                          ║
║  POST /api/validate            → XML validieren              ║
║  POST /api/invoices/<id>/approve → Freigabe/Ablehnung        ║
║       Body: {"user":"x","approved":true,"comment":"..."}     ║
║  POST /api/invoices/<id>/export  → Export (?format=DATEV)    ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
"""


def start_server(port: int = 8080):
    print(API_DOCS)
    print(f"  Server startet auf http://localhost:{port}")
    print(f"  Beenden mit Ctrl+C\n")
    server = HTTPServer(("", port), APIHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server beendet.")
        server.server_close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    start_server(port)
