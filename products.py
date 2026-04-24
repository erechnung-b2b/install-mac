"""
E-Rechnungssystem – Produktkatalog
Digitale Produkte und Dienstleistungen mit EK/VK-Preistrennung.
Bestand wird mitgezählt (informativ, keine Warnung).
"""
from __future__ import annotations
import csv, hashlib, io, json
from datetime import datetime
from pathlib import Path
from typing import Optional


def _ts() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _generate_artnr(name: str, existing: set) -> str:
    """Erzeugt eine Art.-Nr. P-XXXX aus dem Namen."""
    h = hashlib.md5(name.strip().encode("utf-8")).hexdigest()
    digits = "".join(c for c in h if c.isdigit())
    nr = f"P-{digits[:4]}" if len(digits) >= 4 else f"P-{digits.ljust(4, '0')}"
    while nr in existing:
        num = int(nr.split("-")[1]) + 1
        nr = f"P-{num:04d}"
    return nr


class ProductManager:
    """Verwaltet Produkte/Dienstleistungen in products.json."""

    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / "products.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[dict]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text("utf-8"))
            except Exception:
                pass
        return []

    def _save(self, products: list[dict]):
        self.path.write_text(
            json.dumps(products, indent=2, ensure_ascii=False), "utf-8"
        )

    # ── CRUD ──

    def list_all(self, category: str = "", active_only: bool = False) -> list[dict]:
        products = self._load()
        if category:
            products = [p for p in products if p.get("category", "").lower() == category.lower()]
        if active_only:
            products = [p for p in products if p.get("status") == "AKTIV"]
        return products

    def get(self, art_nr: str) -> Optional[dict]:
        for p in self._load():
            if p["art_nr"] == art_nr:
                return p
        return None

    def add(self, data: dict) -> dict:
        products = self._load()
        name = data.get("name", "").strip()
        if not name:
            raise ValueError("Produktbezeichnung ist Pflichtfeld.")

        existing_nrs = {p["art_nr"] for p in products}

        # Art.-Nr: manuell oder auto
        art_nr = data.get("art_nr", "").strip()
        if not art_nr:
            art_nr = _generate_artnr(name, existing_nrs)
        elif art_nr in existing_nrs:
            # Duplikat → bestehend zurückgeben
            for p in products:
                if p["art_nr"] == art_nr:
                    return p

        product = {
            "art_nr": art_nr,
            "name": name,
            "description": data.get("description", "").strip(),
            "category": data.get("category", "").strip(),
            "unit": data.get("unit", "Stk").strip(),
            "ek_price": float(data.get("ek_price", 0)),
            "vk_price": float(data.get("vk_price", 0)),
            "tax_rate": float(data.get("tax_rate", 19)),
            "stock": int(data.get("stock", -1)),
            "supplier_id": data.get("supplier_id", ""),
            "supplier_name": data.get("supplier_name", ""),
            "status": "AKTIV",
            "created_at": _ts(),
        }

        products.append(product)
        self._save(products)
        return product

    def update(self, art_nr: str, data: dict) -> Optional[dict]:
        products = self._load()
        for p in products:
            if p["art_nr"] == art_nr:
                for key in ("name", "description", "category", "unit",
                            "ek_price", "vk_price", "tax_rate", "stock",
                            "supplier_id", "supplier_name", "status"):
                    if key in data:
                        val = data[key]
                        if key in ("ek_price", "vk_price", "tax_rate"):
                            val = float(val)
                        elif key == "stock":
                            val = int(val)
                        elif isinstance(val, str):
                            val = val.strip()
                        p[key] = val
                self._save(products)
                return p
        return None

    def delete(self, art_nr: str) -> bool:
        products = self._load()
        before = len(products)
        products = [p for p in products if p["art_nr"] != art_nr]
        if len(products) < before:
            self._save(products)
            return True
        return False

    def delete_all(self) -> int:
        products = self._load()
        count = len(products)
        self._save([])
        return count

    # ── Bestand ──

    def adjust_stock(self, art_nr: str, delta: int) -> Optional[dict]:
        """Bestand anpassen: +delta = Wareneingang, -delta = Verkauf/Lieferung.
        stock == -1 bedeutet unbegrenzt (Dienstleistungen/digitale Produkte) → wird nicht verändert."""
        products = self._load()
        for p in products:
            if p["art_nr"] == art_nr:
                if p["stock"] == -1:
                    return p  # Unbegrenzt → nichts ändern
                p["stock"] = max(0, p["stock"] + delta)
                self._save(products)
                return p
        return None

    def bulk_stock_in(self, items: list[dict]):
        """Wareneingang für mehrere Produkte. items: [{"art_nr":"P-xxx", "quantity":5}]"""
        products = self._load()
        art_map = {p["art_nr"]: p for p in products}
        changed = False
        for item in items:
            p = art_map.get(item.get("art_nr", ""))
            if p and p["stock"] != -1:
                p["stock"] = max(0, p["stock"] + int(item.get("quantity", 0)))
                changed = True
        if changed:
            self._save(products)

    def bulk_stock_out(self, items: list[dict]):
        """Warenausgang (Lieferschein/Verkauf). stock == -1 wird übersprungen."""
        products = self._load()
        art_map = {p["art_nr"]: p for p in products}
        changed = False
        for item in items:
            p = art_map.get(item.get("art_nr", ""))
            if p and p["stock"] != -1:
                p["stock"] = max(0, p["stock"] - int(item.get("quantity", 0)))
                changed = True
        if changed:
            self._save(products)

    # ── CSV-Import ──

    def import_csv(self, file_bytes: bytes) -> dict:
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
        products = self._load()
        existing_nrs = {p["art_nr"] for p in products}

        KNOWN = {"art_nr", "artikelnr", "artikel", "nr", "name", "bezeichnung",
                 "beschreibung", "description", "kategorie", "category",
                 "einheit", "unit", "ek", "ek_price", "einkaufspreis",
                 "vk", "vk_price", "verkaufspreis", "preis", "price",
                 "ust", "tax_rate", "mwst", "bestand", "stock", "menge",
                 "lieferant", "supplier"}

        for row_num, row in enumerate(reader):
            if not row or all(not c.strip() for c in row):
                continue

            if row_num == 0:
                first_lower = [c.strip().lower() for c in row]
                if any(h in KNOWN for h in first_lower):
                    header_map = {}
                    for i, h in enumerate(first_lower):
                        if h in ("art_nr", "artikelnr", "artikel", "nr"): header_map["art_nr"] = i
                        elif h in ("name", "bezeichnung"): header_map["name"] = i
                        elif h in ("beschreibung", "description"): header_map["description"] = i
                        elif h in ("kategorie", "category"): header_map["category"] = i
                        elif h in ("einheit", "unit"): header_map["unit"] = i
                        elif h in ("ek", "ek_price", "einkaufspreis"): header_map["ek_price"] = i
                        elif h in ("vk", "vk_price", "verkaufspreis", "preis", "price"): header_map["vk_price"] = i
                        elif h in ("ust", "tax_rate", "mwst"): header_map["tax_rate"] = i
                        elif h in ("bestand", "stock", "menge"): header_map["stock"] = i
                        elif h in ("lieferant", "supplier"): header_map["supplier_name"] = i
                    continue

            def col(idx):
                return row[idx].strip() if idx is not None and idx < len(row) else ""

            name = col(header_map.get("name")) if header_map else col(1) if len(row) > 1 else ""
            if not name:
                continue

            art_nr = col(header_map.get("art_nr")) if header_map else col(0)
            if not art_nr or art_nr in existing_nrs:
                art_nr = _generate_artnr(name, existing_nrs)

            # Duplikat-Check Name
            if any(p["name"].strip().lower() == name.lower() for p in products):
                continue

            def num(idx, default=0):
                v = col(idx)
                try:
                    return float(v.replace(",", ".")) if v else default
                except ValueError:
                    return default

            product = {
                "art_nr": art_nr,
                "name": name,
                "description": col(header_map.get("description")) if header_map else "",
                "category": col(header_map.get("category")) if header_map else "",
                "unit": (col(header_map.get("unit")) if header_map else "Stk") or "Stk",
                "ek_price": num(header_map.get("ek_price")) if header_map else 0,
                "vk_price": num(header_map.get("vk_price")) if header_map else num(0),
                "tax_rate": num(header_map.get("tax_rate"), 19) if header_map else 19,
                "stock": int(num(header_map.get("stock"), -1)) if header_map else -1,
                "supplier_id": "",
                "supplier_name": col(header_map.get("supplier_name")) if header_map else "",
                "status": "AKTIV",
                "created_at": _ts(),
            }

            existing_nrs.add(art_nr)
            products.append(product)
            added += 1

        self._save(products)
        return {"imported": added, "total": len(products)}

    # ── Export ──

    def export_csv(self) -> str:
        """Gibt CSV-String mit allen Produkten zurück."""
        products = self._load()
        out = io.StringIO()
        out.write("\ufeff")
        w = csv.writer(out, delimiter=";")
        w.writerow(["Art.-Nr.", "Bezeichnung", "Beschreibung", "Kategorie",
                     "Einheit", "EK-Preis", "VK-Preis", "USt%", "Bestand", "Lieferant"])
        for p in products:
            stock_str = "unbegrenzt" if p.get("stock", -1) == -1 else str(p.get("stock", 0))
            w.writerow([p.get("art_nr",""), p.get("name",""), p.get("description",""),
                        p.get("category",""), p.get("unit",""),
                        f'{p.get("ek_price",0):.2f}'.replace(".",","),
                        f'{p.get("vk_price",0):.2f}'.replace(".",","),
                        p.get("tax_rate",19), stock_str,
                        p.get("supplier_name","")])
        return out.getvalue()

    # ── Kategorien ──

    def get_categories(self) -> list[str]:
        products = self._load()
        cats = sorted(set(p.get("category", "") for p in products if p.get("category")))
        return cats


# ── Demo-Daten ───────────────────────────────────────────────────────

DEMO_PRODUCTS = [
    # Energieberatung
    {"art_nr": "P-1001", "name": "Energieausweis Wohngebäude (Verbrauch)", "category": "Energieberatung",
     "unit": "Stk", "ek_price": 0, "vk_price": 350.00, "tax_rate": 19,
     "description": "Verbrauchsausweis nach GEG für Wohngebäude"},
    {"art_nr": "P-1002", "name": "Energieausweis Wohngebäude (Bedarf)", "category": "Energieberatung",
     "unit": "Stk", "ek_price": 0, "vk_price": 450.00, "tax_rate": 19,
     "description": "Bedarfsausweis nach GEG für Wohngebäude"},
    {"art_nr": "P-1003", "name": "Energieausweis Nichtwohngebäude", "category": "Energieberatung",
     "unit": "Stk", "ek_price": 0, "vk_price": 650.00, "tax_rate": 19,
     "description": "Energieausweis nach GEG für Nichtwohngebäude"},
    {"art_nr": "P-1004", "name": "Sanierungsfahrplan (iSFP)", "category": "Energieberatung",
     "unit": "Stk", "ek_price": 0, "vk_price": 1800.00, "tax_rate": 19,
     "description": "Individueller Sanierungsfahrplan gem. BEG-Richtlinie"},
    {"art_nr": "P-1005", "name": "KfW/BAFA-Förderantrag Begleitung", "category": "Energieberatung",
     "unit": "Stk", "ek_price": 0, "vk_price": 400.00, "tax_rate": 19,
     "description": "Erstellung und Einreichung Förderantrag inkl. Nachweise"},

    # Thermografie
    {"art_nr": "P-2001", "name": "Thermografie Gebäudehülle", "category": "Thermografie",
     "unit": "Stk", "ek_price": 80.00, "vk_price": 380.00, "tax_rate": 19,
     "description": "IR-Aufnahmen Außenhülle, Auswertung, Bericht (EK: Kamera-Verschleiß)"},
    {"art_nr": "P-2002", "name": "Thermografie Einzelraum", "category": "Thermografie",
     "unit": "Stk", "ek_price": 30.00, "vk_price": 150.00, "tax_rate": 19,
     "description": "IR-Aufnahme + Auswertung eines einzelnen Raums"},
    {"art_nr": "P-2003", "name": "Blower-Door-Test", "category": "Thermografie",
     "unit": "Stk", "ek_price": 250.00, "vk_price": 550.00, "tax_rate": 19,
     "description": "Luftdichtheitsmessung nach DIN EN 13829 (EK: Geräte-Miete)"},

    # Beratung / Dienstleistung
    {"art_nr": "P-3001", "name": "Beratungsstunde vor Ort", "category": "Beratung",
     "unit": "Std", "ek_price": 0, "vk_price": 95.00, "tax_rate": 19,
     "description": "Energieberatung beim Kunden, inkl. Anfahrt im Umkreis 30km"},
    {"art_nr": "P-3002", "name": "Beratungsstunde online/telefonisch", "category": "Beratung",
     "unit": "Std", "ek_price": 0, "vk_price": 75.00, "tax_rate": 19,
     "description": "Fernberatung per Videokonferenz oder Telefon"},
    {"art_nr": "P-3003", "name": "Baubegleitung (energetisch)", "category": "Beratung",
     "unit": "Std", "ek_price": 0, "vk_price": 110.00, "tax_rate": 19,
     "description": "Energetische Fachbauleitung gem. BEG, Dokumentation"},
    {"art_nr": "P-3004", "name": "Anfahrtspauschale > 30km", "category": "Beratung",
     "unit": "km", "ek_price": 0.30, "vk_price": 0.52, "tax_rate": 19,
     "description": "Kilometerpauschale ab 30km Entfernung"},

    # Dokumentation / Software
    {"art_nr": "P-4001", "name": "Wärmebrückennachweis", "category": "Dokumentation",
     "unit": "Stk", "ek_price": 0, "vk_price": 280.00, "tax_rate": 19,
     "description": "Detaillierter Wärmebrückennachweis nach DIN 4108 Bbl. 2"},
    {"art_nr": "P-4002", "name": "U-Wert-Berechnung (je Bauteil)", "category": "Dokumentation",
     "unit": "Stk", "ek_price": 0, "vk_price": 85.00, "tax_rate": 19,
     "description": "U-Wert-Nachweis für ein Bauteil (Wand/Dach/Boden/Fenster)"},
    {"art_nr": "P-4003", "name": "Hydraulischer Abgleich (Berechnung)", "category": "Dokumentation",
     "unit": "Stk", "ek_price": 120.00, "vk_price": 450.00, "tax_rate": 19,
     "description": "Berechnung hydraulischer Abgleich nach Verfahren B (EK: Software-Lizenz)"},
    {"art_nr": "P-4004", "name": "Heizlastberechnung nach DIN 12831", "category": "Dokumentation",
     "unit": "Stk", "ek_price": 80.00, "vk_price": 380.00, "tax_rate": 19,
     "description": "Raumweise Heizlastberechnung (EK: Software-Lizenz)"},
    {"art_nr": "P-4005", "name": "GEG-Nachweis Neubau", "category": "Dokumentation",
     "unit": "Stk", "ek_price": 0, "vk_price": 850.00, "tax_rate": 19,
     "description": "Nachweis der Anforderungen nach Gebäudeenergiegesetz für Neubauten"},

    # Schulung
    {"art_nr": "P-5001", "name": "Workshop Energieeffizienz (halbtags)", "category": "Schulung",
     "unit": "Stk", "ek_price": 50.00, "vk_price": 480.00, "tax_rate": 19,
     "description": "4-Stunden-Workshop für Hausverwaltungen/Eigentümer (EK: Raummiete/Material)"},
    {"art_nr": "P-5002", "name": "Schulung E-Rechnungssystem", "category": "Schulung",
     "unit": "Std", "ek_price": 0, "vk_price": 120.00, "tax_rate": 19,
     "description": "Einweisung in das E-Rechnungssystem (online)"},
]


def create_demo_products(data_dir: str | Path):
    """Erzeugt Demo-Produkte."""
    pm = ProductManager(data_dir)
    pm.delete_all()
    for p in DEMO_PRODUCTS:
        pm.add(p)
    return len(DEMO_PRODUCTS)
