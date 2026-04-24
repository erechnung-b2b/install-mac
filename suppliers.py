"""
E-Rechnungssystem – Lieferantenverwaltung
Auftragsmanagement Phase 1: CRUD, CSV-Import, Freigabe (Häkchen).
Speicherformat: suppliers.json (analog buyers.json).
"""
from __future__ import annotations
import csv, hashlib, io, json
from datetime import datetime
from pathlib import Path
from typing import Optional


# ── Helpers ──────────────────────────────────────────────────────────

def _generate_id(name: str) -> str:
    """5-stellige numerische ID aus dem Namen (analog _generate_buyer_id)."""
    h = hashlib.md5(name.strip().encode("utf-8")).hexdigest()
    digits = "".join(c for c in h if c.isdigit())
    return (digits[:5] if len(digits) >= 5 else digits.ljust(5, "0"))


def _ts() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── SupplierManager ──────────────────────────────────────────────────

class SupplierManager:
    """Verwaltet Lieferanten-Stammdaten in einer JSON-Datei."""

    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / "suppliers.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ── Persistenz ──

    def _load(self) -> list[dict]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text("utf-8"))
            except Exception:
                pass
        return []

    def _save(self, suppliers: list[dict]):
        self.path.write_text(
            json.dumps(suppliers, indent=2, ensure_ascii=False), "utf-8"
        )

    # ── CRUD ──

    def list_all(self) -> list[dict]:
        return self._load()

    def get(self, supplier_id: str) -> Optional[dict]:
        for s in self._load():
            if s["id"] == supplier_id:
                return s
        return None

    def add(self, data: dict) -> dict:
        suppliers = self._load()
        display = data.get("name", "").strip()
        if not display:
            raise ValueError("Lieferantenname ist Pflichtfeld.")

        supplier = {
            "id": _generate_id(display),
            "name": display,
            "contact_name": data.get("contact_name", "").strip(),
            "street": data.get("street", "").strip(),
            "post_code": data.get("post_code", "").strip(),
            "city": data.get("city", "").strip(),
            "country": data.get("country", "DE").strip() or "DE",
            "email": data.get("email", "").strip(),
            "phone": data.get("phone", "").strip(),
            "vat_id": data.get("vat_id", "").strip(),
            "iban": data.get("iban", "").strip(),
            "bic": data.get("bic", "").strip(),
            "category": data.get("category", "").strip(),
            "notes": data.get("notes", "").strip(),
            "status": "AKTIV",
            "approved": False,
            "approved_at": None,
            "approved_by": None,
            "created_at": _ts(),
        }

        # Duplikat?
        for s in suppliers:
            if s["name"].strip().lower() == display.lower():
                return s  # bestehend zurückgeben

        # Collision bei ID?  Suffix anhängen
        existing_ids = {s["id"] for s in suppliers}
        while supplier["id"] in existing_ids:
            supplier["id"] = str(int(supplier["id"]) + 1).zfill(5)

        suppliers.append(supplier)
        self._save(suppliers)
        return supplier

    def update(self, supplier_id: str, data: dict) -> Optional[dict]:
        suppliers = self._load()
        for s in suppliers:
            if s["id"] == supplier_id:
                for key in ("name", "contact_name", "street", "post_code", "city",
                            "country", "email", "phone", "vat_id", "iban", "bic",
                            "category", "notes", "status"):
                    if key in data:
                        s[key] = data[key].strip() if isinstance(data[key], str) else data[key]
                self._save(suppliers)
                return s
        return None

    def delete(self, supplier_id: str) -> bool:
        suppliers = self._load()
        before = len(suppliers)
        suppliers = [s for s in suppliers if s["id"] != supplier_id]
        if len(suppliers) < before:
            self._save(suppliers)
            return True
        return False

    def delete_all(self) -> int:
        suppliers = self._load()
        count = len(suppliers)
        self._save([])
        return count

    # ── Freigabe (Häkchen) ──

    def approve(self, supplier_id: str, user: str = "system",
                comment: str = "") -> Optional[dict]:
        suppliers = self._load()
        for s in suppliers:
            if s["id"] == supplier_id:
                s["approved"] = True
                s["approved_at"] = _ts()
                s["approved_by"] = user
                if comment:
                    s["notes"] = ((s.get("notes") or "") + f"\n[Freigabe] {comment}").strip()
                self._save(suppliers)
                return s
        return None

    def unapprove(self, supplier_id: str, user: str = "system") -> Optional[dict]:
        suppliers = self._load()
        for s in suppliers:
            if s["id"] == supplier_id:
                s["approved"] = False
                s["approved_at"] = None
                s["approved_by"] = None
                self._save(suppliers)
                return s
        return None

    # ── CSV-Import ──

    def import_csv(self, file_bytes: bytes) -> dict:
        """Importiert Lieferanten aus CSV. Gibt {"imported": n, "total": m} zurück."""
        try:
            text = file_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = file_bytes.decode("latin-1")

        # Zeilenumbrüche normalisieren
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        delimiter = ";" if ";" in text else ","
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        header_map = None
        added = 0
        suppliers = self._load()

        KNOWN = {"firma", "name", "firmenname", "company", "lieferant", "supplier",
                 "strasse", "street", "str",
                 "plz", "post_code", "postleitzahl", "zip",
                 "ort", "city", "stadt",
                 "land", "country",
                 "email", "e-mail", "mail",
                 "telefon", "phone", "tel",
                 "ust-id", "vat_id", "ust_id", "ustid", "vat",
                 "iban", "bic",
                 "kategorie", "category",
                 "ansprechpartner", "contact_name", "kontakt", "contact",
                 "notizen", "notes", "bemerkung"}

        NAME_KEYS = ("firma", "name", "firmenname", "company", "lieferant", "supplier")

        for row_num, row in enumerate(reader):
            if not row or all(not c.strip() for c in row):
                continue

            if row_num == 0:
                first_lower = [c.strip().lower() for c in row]
                if any(h in KNOWN for h in first_lower):
                    header_map = {}
                    for i, h in enumerate(first_lower):
                        if h in NAME_KEYS: header_map["name"] = i
                        elif h in ("strasse", "street", "str"): header_map["street"] = i
                        elif h in ("plz", "post_code", "postleitzahl", "zip"): header_map["post_code"] = i
                        elif h in ("ort", "city", "stadt"): header_map["city"] = i
                        elif h in ("land", "country"): header_map["country"] = i
                        elif h in ("email", "e-mail", "mail"): header_map["email"] = i
                        elif h in ("telefon", "phone", "tel"): header_map["phone"] = i
                        elif h in ("ust-id", "vat_id", "ust_id", "ustid", "vat"): header_map["vat_id"] = i
                        elif h in ("iban",): header_map["iban"] = i
                        elif h in ("bic",): header_map["bic"] = i
                        elif h in ("kategorie", "category"): header_map["category"] = i
                        elif h in ("ansprechpartner", "contact_name", "kontakt", "contact"): header_map["contact_name"] = i
                        elif h in ("notizen", "notes", "bemerkung"): header_map["notes"] = i
                    continue

            def col(idx):
                return row[idx].strip() if idx is not None and idx < len(row) else ""

            if header_map:
                name = col(header_map.get("name"))
            else:
                name = col(0)

            if not name:
                continue

            # Duplikat?
            if any(s["name"].strip().lower() == name.lower() for s in suppliers):
                continue

            sid = _generate_id(name)
            existing_ids = {s["id"] for s in suppliers}
            while sid in existing_ids:
                sid = str(int(sid) + 1).zfill(5)

            supplier = {
                "id": sid,
                "name": name,
                "contact_name": col(header_map.get("contact_name")) if header_map else col(8) if len(row) > 8 else "",
                "street": col(header_map.get("street")) if header_map else col(1) if len(row) > 1 else "",
                "post_code": col(header_map.get("post_code")) if header_map else col(2) if len(row) > 2 else "",
                "city": col(header_map.get("city")) if header_map else col(3) if len(row) > 3 else "",
                "country": (col(header_map.get("country")) if header_map else col(4) if len(row) > 4 else "") or "DE",
                "email": col(header_map.get("email")) if header_map else col(5) if len(row) > 5 else "",
                "phone": col(header_map.get("phone")) if header_map else col(6) if len(row) > 6 else "",
                "vat_id": col(header_map.get("vat_id")) if header_map else col(7) if len(row) > 7 else "",
                "iban": col(header_map.get("iban")) if header_map else "",
                "bic": col(header_map.get("bic")) if header_map else "",
                "category": col(header_map.get("category")) if header_map else "",
                "notes": col(header_map.get("notes")) if header_map else "",
                "status": "AKTIV",
                "approved": False,
                "approved_at": None,
                "approved_by": None,
                "created_at": _ts(),
            }
            suppliers.append(supplier)
            added += 1

        self._save(suppliers)
        return {"imported": added, "total": len(suppliers)}

    # ── Export ──

    def export_csv(self) -> str:
        """Gibt CSV-String mit allen Lieferanten zurück."""
        suppliers = self._load()
        out = io.StringIO()
        out.write("\ufeff")  # BOM
        w = csv.writer(out, delimiter=";")
        w.writerow(["Firma", "Strasse", "PLZ", "Ort", "Land", "Email", "Telefon",
                     "USt-ID", "IBAN", "BIC", "Kategorie", "Ansprechpartner", "Notizen"])
        for s in suppliers:
            w.writerow([s.get("name",""), s.get("street",""), s.get("post_code",""),
                        s.get("city",""), s.get("country",""), s.get("email",""),
                        s.get("phone",""), s.get("vat_id",""), s.get("iban",""),
                        s.get("bic",""), s.get("category",""), s.get("contact_name",""),
                        s.get("notes","")])
        return out.getvalue()
