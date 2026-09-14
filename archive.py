"""
E-Rechnungssystem – Archivierung
FR-500..FR-540 / P-06: GoBD-nahe Archivierung, Hash, Suche, Integritätsprüfung
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import hashlib, json
from models import Invoice
from validator import ValidationReport


@dataclass
class ArchiveRecord:
    invoice_id: str = ""
    invoice_number: str = ""
    format_type: str = "XRechnung"
    format_version: str = "3.0"
    created_at: str = ""
    sha256_hash: str = ""
    xml_filename: str = ""
    validation_status: str = ""
    validation_report: dict = field(default_factory=dict)
    invoice_metadata: dict = field(default_factory=dict)
    send_status: str = "ERSTELLT"
    direction: str = ""  # EINGANG / AUSGANG

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


class InvoiceArchive:
    """Dateisystem-Archiv: archive_root/<invoice_id>/{xml, report, meta}"""

    def __init__(self, archive_root: str = "./archiv"):
        self.root = Path(archive_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / "index.json"
        self._index: list[dict] = []
        if self._index_path.exists():
            self._index = json.loads(self._index_path.read_text(encoding="utf-8"))

    def archive_invoice(self, inv: Invoice, xml_bytes: bytes,
                        report: ValidationReport, direction: str = "AUSGANG",
                        pdf_bytes: bytes | None = None) -> ArchiveRecord:
        """Legt XML (Originalbeleg), Prüfbericht und — sofern übergeben — das PDF ab.

        Das PDF ist das, was Mensch und Buchhaltung ansehen: bei Eingangsrechnungen
        kommt es mit der Mail, bei Ausgangsrechnungen erzeugt es die e-Rechnung
        selbst und gibt es hier mit (31.07.2026 — vorher lag zu eigenen
        Rechnungen nur die XML im Archiv, die kein Browser anzeigt).
        """
        inv_dir = self.root / inv._id
        inv_dir.mkdir(parents=True, exist_ok=True)

        xml_fn = f"{inv.invoice_number.replace('/', '_')}.xml"
        (inv_dir / xml_fn).write_bytes(xml_bytes)

        if pdf_bytes:
            (inv_dir / "original.pdf").write_bytes(pdf_bytes)

        sha256 = hashlib.sha256(xml_bytes).hexdigest()

        (inv_dir / "validation_report.json").write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

        meta = {
            "invoice_number": inv.invoice_number, "invoice_date": str(inv.invoice_date),
            "type_code": inv.invoice_type_code,
            "preceding_invoice": inv.preceding_invoice,
            "seller": inv.seller.name, "seller_name": inv.seller.name,
            "buyer_name": inv.buyer.name,
            "currency": inv.currency_code,
            "net_amount": str(inv.tax_exclusive_amount()),
            "tax_amount": str(inv.tax_amount()),
            "gross_amount": str(inv.tax_inclusive_amount()),
            "due_amount": str(inv.amount_due()),
            "line_count": len(inv.lines), "direction": direction,
            "has_pdf": bool(pdf_bytes) or (inv_dir / "original.pdf").exists(),
            "status": inv.status,
            "audit_trail_count": len(inv.audit_trail),
        }
        (inv_dir / "metadata.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

        record = ArchiveRecord(
            invoice_id=inv._id, invoice_number=inv.invoice_number,
            created_at=datetime.now().isoformat(), sha256_hash=sha256,
            xml_filename=xml_fn,
            validation_status="GÜLTIG" if report.is_valid else "UNGÜLTIG",
            validation_report=report.to_dict(), invoice_metadata=meta,
            direction=direction,
        )
        self._index.append(record.to_dict())
        self._save_index()
        return record

    def anlage_hinzufuegen(self, invoice_id: str, dateiname: str, daten: bytes,
                           rolle: str = "beleg", quelle: str = "") -> dict:
        """Legt ein WEITERES Dokument zu einer schon archivierten Rechnung ab.

        Hintergrund (07.09.2026): Zu einer Rechnung gehoert oft mehr als das
        eine Druckstueck. Anthropic schickt je Rechnung ZWEI PDF — die Rechnung
        und den Zahlungsbeleg dazu. Bisher landete nur die erste im Archiv, die
        zweite wurde als Dublette verworfen; in der Buchhaltung fehlte damit der
        Zahlungsnachweis. Solche Dokumente liegen jetzt unter
        <archiv>/<invoice_id>/anlagen/ und stehen in metadata.json unter
        'anlagen'. Die Rechnung selbst (original.pdf, XML) bleibt unberuehrt —
        eine Anlage ersetzt nie den Originalbeleg.

        Gleicher Inhalt wird nicht zweimal abgelegt: erkannt am SHA-256, damit ein
        wiederholter Mailabruf keine Kopienhalde erzeugt.
        """
        inv_dir = self.root / invoice_id
        if not inv_dir.exists():
            raise FileNotFoundError(f"Rechnung {invoice_id} liegt nicht im Archiv.")
        sha = hashlib.sha256(daten).hexdigest()

        meta_p = inv_dir / "metadata.json"
        meta = {}
        if meta_p.exists():
            try:
                meta = json.loads(meta_p.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        anlagen = meta.get("anlagen") or []

        vorhanden = next((a for a in anlagen if a.get("sha256") == sha), None)
        if vorhanden:
            return dict(vorhanden, schon_vorhanden=True)

        # Dateiname entschaerfen und bei Namensgleichheit durchnummerieren
        sicher = "".join(c for c in (dateiname or "anlage.pdf")
                         if c.isalnum() or c in "._- ").strip() or "anlage.pdf"
        ziel_dir = inv_dir / "anlagen"
        ziel_dir.mkdir(parents=True, exist_ok=True)
        ziel = ziel_dir / sicher
        n = 1
        while ziel.exists():
            stamm, _, endung = sicher.rpartition(".")
            ziel = ziel_dir / (f"{stamm or sicher}-{n}" + (f".{endung}" if stamm else ""))
            n += 1
        ziel.write_bytes(daten)

        eintrag = {
            "datei": ziel.name,
            "rolle": rolle,                 # z. B. 'zahlungsbeleg', 'beleg', 'anhang'
            "quelle": quelle,               # woher das Dokument kam
            "sha256": sha,
            "groesse": len(daten),
            "hinzugefuegt_am": datetime.now().isoformat(),
        }
        anlagen.append(eintrag)
        meta["anlagen"] = anlagen
        meta_p.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        return dict(eintrag, schon_vorhanden=False)

    def anlagen(self, invoice_id: str) -> list[dict]:
        """Die weiteren Dokumente einer Rechnung (leer, wenn keine da sind)."""
        meta_p = self.root / invoice_id / "metadata.json"
        if not meta_p.exists():
            return []
        try:
            return json.loads(meta_p.read_text(encoding="utf-8")).get("anlagen") or []
        except Exception:
            return []

    def _save_index(self):
        self._index_path.write_text(
            json.dumps(self._index, indent=2, ensure_ascii=False), encoding="utf-8")

    def search(self, **kwargs) -> list[dict]:
        """FR-510: Suche nach beliebigen Metadaten-Feldern."""
        results = []
        for rec in self._index:
            match = True
            meta = rec.get("invoice_metadata", {})
            for key, val in kwargs.items():
                if key in rec and str(val).lower() in str(rec[key]).lower():
                    continue
                if key in meta and str(val).lower() in str(meta[key]).lower():
                    continue
                match = False
                break
            if match:
                results.append(rec)
        return results

    def find_by_number(self, number: str) -> list[dict]:
        return [r for r in self._index if r["invoice_number"] == number]

    def list_all(self) -> list[dict]:
        return list(self._index)

    def verify_integrity(self, invoice_id: str) -> tuple[bool, str]:
        matching = [r for r in self._index if r["invoice_id"] == invoice_id]
        if not matching: return False, "Nicht im Archiv."
        rec = matching[0]
        xml_path = self.root / invoice_id / rec["xml_filename"]
        if not xml_path.exists(): return False, "XML fehlt."
        current = hashlib.sha256(xml_path.read_bytes()).hexdigest()
        if current != rec["sha256_hash"]:
            return False, f"Hash-Abweichung! Erwartet: {rec['sha256_hash'][:16]}, Aktuell: {current[:16]}"
        return True, "Integrität bestätigt ✓"
