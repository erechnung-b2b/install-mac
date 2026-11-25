# -*- coding: utf-8 -*-
"""
E-Rechnungssystem – Zusatzangaben der Rechnung (EN 16931), Paket B vom 25.11.2026

Ein Modul für alles, was über die Standardrechnung hinausgeht:

  Kunden im Ausland        BT-55 Land des Käufers, grobe Prüfung der USt-ID (BT-48) je Land
  Lieferung                BG-13/BG-15: BT-70 Name, BT-71 Kennung, BT-75 … BT-80 Anschrift des Lieferorts
  Steuerfälle              innergemeinschaftliche Lieferung (K), Ausfuhr (G), nicht steuerbar (O) – Prüfregeln
  Lastschrift              BG-19: BT-89 Mandat, BT-90 Gläubiger-ID, BT-91 IBAN des Zahlungspflichtigen
  Zahlungsempfänger        BG-10: BT-59 Name, BT-60 Kennung, BT-61 Registernummer
  Anhänge                  BG-24: BT-122 … BT-125 (eingebettet oder als Verweis)
  weitere Bezüge           BT-14 Auftragsnummer, BT-15 Wareneingang, BT-16 Versandanzeige, BT-17 Ausschreibung/Los,
                           BT-18 Objektkennung, BT-19 Buchungsreferenz des Käufers
  Währung und Rundung      BT-6 / BT-111 Steuerbetrag in Buchungswährung, BT-114 Rundungsbetrag

Die Fachfelder stehen im Modell (models.py: Invoice.delivery, .payee, .attachments …). Dieses Modul
  - ergänzt die fertige UBL- bzw. CII-Datei um diese Angaben (ubl_ergaenzen, cii_ergaenzen) – eine Rechnung
    OHNE Zusatzangaben bleibt dabei byteweise, wie sie war;
  - liest sie aus fremden Dateien (ubl_lesen, cii_lesen);
  - speichert und lädt sie (speichern, laden) – Anhänge als Dateien im Archivordner der Rechnung, nie als
    base64 in invoices.json;
  - prüft sie (pruefung_ergaenzen) und liefert sie für PDF, Maske, CSV und Rechnungsansicht.

Das Modul importiert webapp NICHT. Die Routen hängt einrichten(app, globals()) am Ende von webapp.py ein.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from lxml import etree

VERSION = "1.0.0"

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Länder (ISO 3166-1 alpha-2) – EU vollständig, dazu die üblichen Drittländer
# ══════════════════════════════════════════════════════════════════════════════════════════════════
EU_LAENDER = ("AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HR", "HU", "IE", "IT",
              "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK")
LAENDER = {
    "DE": "Deutschland", "AT": "Österreich", "BE": "Belgien", "BG": "Bulgarien", "CY": "Zypern",
    "CZ": "Tschechien", "DK": "Dänemark", "EE": "Estland", "ES": "Spanien", "FI": "Finnland",
    "FR": "Frankreich", "GR": "Griechenland", "HR": "Kroatien", "HU": "Ungarn", "IE": "Irland",
    "IT": "Italien", "LT": "Litauen", "LU": "Luxemburg", "LV": "Lettland", "MT": "Malta",
    "NL": "Niederlande", "PL": "Polen", "PT": "Portugal", "RO": "Rumänien", "SE": "Schweden",
    "SI": "Slowenien", "SK": "Slowakei",
    "CH": "Schweiz", "LI": "Liechtenstein", "GB": "Vereinigtes Königreich", "NO": "Norwegen", "IS": "Island",
    "US": "Vereinigte Staaten", "CA": "Kanada", "TR": "Türkei", "UA": "Ukraine", "RS": "Serbien",
    "BA": "Bosnien und Herzegowina", "AL": "Albanien", "MK": "Nordmazedonien", "ME": "Montenegro",
    "MD": "Moldau", "IL": "Israel", "AE": "Vereinigte Arabische Emirate", "SA": "Saudi-Arabien",
    "CN": "China", "JP": "Japan", "KR": "Südkorea", "IN": "Indien", "SG": "Singapur", "AU": "Australien",
    "NZ": "Neuseeland", "BR": "Brasilien", "MX": "Mexiko", "ZA": "Südafrika",
}
_LAND_ALIAS = {"deutschland": "DE", "germany": "DE", "d": "DE", "oesterreich": "AT", "austria": "AT", "a": "AT",
               "schweiz": "CH", "switzerland": "CH", "suisse": "CH", "uk": "GB", "grossbritannien": "GB",
               "großbritannien": "GB", "england": "GB", "usa": "US", "vereinigte staaten von amerika": "US",
               "holland": "NL", "tuerkei": "TR", "turkey": "TR", "türkiye": "TR", "frankreich": "FR", "france": "FR",
               "italien": "IT", "italia": "IT", "spanien": "ES", "polen": "PL", "niederlande": "NL",
               "belgien": "BE", "luxemburg": "LU", "daenemark": "DK", "norwegen": "NO", "schweden": "SE",
               "tschechische republik": "CZ", "el": "GR"}
for _c, _n in list(LAENDER.items()):
    _LAND_ALIAS.setdefault(_n.lower(), _c)
_ISO2 = re.compile(r"^[A-Z]{2}$")


def land_normalisieren(wert, vorgabe: str = "DE") -> str:
    """Eingabe (Code oder deutscher Name) → ISO-Code. Leer = vorgabe. Unbekanntes wird nicht geraten."""
    t = " ".join(str(wert or "").split())
    if not t:
        return vorgabe
    if _ISO2.match(t.upper()) and t.upper() in LAENDER:
        return t.upper()
    if t.lower() in _LAND_ALIAS:
        return _LAND_ALIAS[t.lower()]
    if _ISO2.match(t.upper()):
        return t.upper()          # gültige Form, aber nicht in der Auswahlliste (z. B. „TH“) – so übernehmen
    raise ValueError(f"Land „{t}“ ist unbekannt. Bitte den zweistelligen Ländercode angeben (z. B. DE, AT, FR, CH).")


def land_name(code) -> str:
    c = str(code or "").strip().upper()
    return LAENDER.get(c, c)


def ist_eu(code) -> bool:
    return str(code or "").strip().upper() in EU_LAENDER


# USt-ID je EU-Land: Präfix und grobe Form (ohne Prüfziffer). Griechenland führt das Präfix EL, Nordirland XI.
_USTID_FORM = {
    "AT": r"U\d{8}", "BE": r"[01]\d{9}", "BG": r"\d{9,10}", "CY": r"\d{8}[A-Z]", "CZ": r"\d{8,10}",
    "DE": r"\d{9}", "DK": r"\d{8}", "EE": r"\d{9}", "EL": r"\d{9}", "ES": r"[A-Z0-9]\d{7}[A-Z0-9]",
    "FI": r"\d{8}", "FR": r"[A-Z0-9]{2}\d{9}", "HR": r"\d{11}", "HU": r"\d{8}", "IE": r"[A-Z0-9]{8,9}",
    "IT": r"\d{11}", "LT": r"(\d{9}|\d{12})", "LU": r"\d{8}", "LV": r"\d{11}", "MT": r"\d{8}",
    "NL": r"\d{9}B\d{2}", "PL": r"\d{10}", "PT": r"\d{9}", "RO": r"\d{2,10}", "SE": r"\d{12}",
    "SI": r"\d{8}", "SK": r"\d{10}", "XI": r"(\d{9}|\d{12}|GD\d{3}|HA\d{3})",
}


def ustid_praefix(land) -> str:
    c = str(land or "").strip().upper()
    return "EL" if c == "GR" else c


def ustid_pruefen(ustid, land) -> tuple:
    """(stufe, text): stufe '' = unauffällig, 'praefix' = Länderkennzeichen fehlt/passt nicht, 'form' = Aufbau
    ungewöhnlich. Geprüft wird grob: Präfix und Länge/Zeichen, keine Prüfziffer, keine Abfrage beim BZSt."""
    u = "".join(str(ustid or "").split()).upper().replace(".", "").replace("-", "")
    c = str(land or "").strip().upper()
    if not u:
        return "", ""
    if not re.match(r"^[A-Z]{2}", u):
        return "praefix", f"Die USt-ID „{ustid}“ beginnt nicht mit einem Länderkennzeichen (z. B. {ustid_praefix(c) or 'DE'}…)."
    pre = u[:2]
    erwartet = ustid_praefix(c)
    if ist_eu(c) or c == "GB":
        erlaubt = (erwartet,) if c != "GB" else ("GB", "XI")
        if pre not in erlaubt:
            return "praefix", (f"Die USt-ID „{ustid}“ beginnt mit {pre}, das Land des Kunden ist aber "
                               f"{land_name(c)} ({' oder '.join(erlaubt)}…).")
    form = _USTID_FORM.get(pre)
    if form and not re.fullmatch(form, u[2:]):
        return "form", f"Die USt-ID „{ustid}“ hat nicht den für {pre} üblichen Aufbau – bitte auf Tippfehler prüfen."
    if not form and not re.fullmatch(r"[A-Z0-9+*]{2,15}", u[2:]):
        return "form", f"Die USt-ID „{ustid}“ enthält unerwartete Zeichen."
    return "", ""


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Kleine Helfer
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _s(wert, laenge: int = 200) -> str:
    return " ".join(str(wert or "").split())[:laenge]


def _dez(wert):
    if wert is None or str(wert).strip() == "":
        return None
    t = str(wert).strip().replace(" ", "")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return Decimal(t)
    except InvalidOperation:
        raise ValueError(f"„{wert}“ ist kein Betrag.")


def iban_norm(text) -> str:
    return "".join(str(text or "").split()).upper()


def iban_formal_ok(text) -> bool:
    """Länge 15–34, Länderkennzeichen, Prüfziffer nach ISO 13616 (Modulo 97)."""
    t = iban_norm(text)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", t):
        return False
    zahl = "".join(str(int(c, 36)) for c in t[4:] + t[:4])
    return int(zahl) % 97 == 1


def iban_gekuerzt(text) -> str:
    t = iban_norm(text)
    return (t[:4] + " … " + t[-4:]) if len(t) > 10 else t


BEZUEGE = (
    # (Modellfeld, BT, Bezeichnung für Maske/PDF/Ansicht, Anfrageschlüssel)
    ("sales_order_reference", "BT-14", "Unsere Auftragsnummer", "sales_order_reference"),
    ("receiving_advice_reference", "BT-15", "Wareneingangsmeldung", "receiving_advice_reference"),
    ("despatch_advice_reference", "BT-16", "Lieferschein / Versandanzeige", "despatch_advice_reference"),
    ("tender_reference", "BT-17", "Ausschreibung / Los", "tender_reference"),
    ("object_identifier", "BT-18", "Objektkennung", "object_identifier"),
    ("buyer_accounting_reference", "BT-19", "Kostenstelle / Buchungsreferenz", "buyer_accounting_reference"),
)

# Anhänge (BG-24): zulässige Dateiarten laut Codeliste der EN 16931 (BR-CL-24)
ANHANG_MIME = {
    "application/pdf": (".pdf",),
    "image/png": (".png",),
    "image/jpeg": (".jpg", ".jpeg"),
    "text/csv": (".csv",),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (".xlsx",),
    "application/vnd.oasis.opendocument.spreadsheet": (".ods",),
}
_ENDUNG_MIME = {e: m for m, es in ANHANG_MIME.items() for e in es}
# Größengrenze: 5 MB für alle Anhänge einer Rechnung zusammen (Rohgröße). Eingebettet wachsen sie um ein Drittel
# (base64); die XML geht per Mail hinaus (übliche Postfachgrenze 10–20 MB, dazu PDF und ZUGFeRD-Kopie) und wird
# bei jedem Abruf im Arbeitsspeicher neu aufgebaut. Größere Unterlagen gehören als Verweis (BT-124) an die Rechnung.
ANHANG_MAX_GESAMT = 5 * 1024 * 1024
ANHANG_MAX_ANZAHL = 20


def anhang_mime(dateiname: str, mime: str = "") -> str:
    m = (mime or "").split(";")[0].strip().lower()
    if m in ANHANG_MIME:
        return m
    return _ENDUNG_MIME.get(os.path.splitext(dateiname or "")[1].lower(), m)


def _inhalt_passt(mime: str, daten: bytes) -> bool:
    kopf = daten[:8]
    if mime == "application/pdf":
        return kopf.startswith(b"%PDF")
    if mime == "image/png":
        return kopf.startswith(b"\x89PNG")
    if mime == "image/jpeg":
        return kopf.startswith(b"\xff\xd8")
    if mime in ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/vnd.oasis.opendocument.spreadsheet"):
        return kopf.startswith(b"PK")
    if mime == "text/csv":
        return b"\x00" not in daten[:4096]
    return False


def dateiname_sicher(name: str, vorgabe: str = "anhang") -> str:
    n = os.path.basename(str(name or "").replace("\\", "/"))
    n = "".join(c for c in n if c.isalnum() or c in "._- ").strip(" .")
    return n[:120] or vorgabe


def anhang_bauen(dateiname: str, daten: bytes, mime: str = "", beschreibung: str = "", kennung: str = ""):
    """Prüft eine hochgeladene Datei und liefert ein models.Attachment (ValueError mit Klartext sonst)."""
    from models import Attachment
    name = dateiname_sicher(dateiname)
    m = anhang_mime(name, mime)
    if m not in ANHANG_MIME:
        raise ValueError(f"Die Datei „{name}“ kann nicht angehängt werden. Erlaubt sind PDF, PNG, JPEG, CSV, XLSX und ODS.")
    if not daten:
        raise ValueError(f"Die Datei „{name}“ ist leer.")
    if not _inhalt_passt(m, daten):
        raise ValueError(f"Der Inhalt der Datei „{name}“ passt nicht zur Dateiart ({m}).")
    if os.path.splitext(name)[1].lower() not in ANHANG_MIME[m]:
        name = os.path.splitext(name)[0] + ANHANG_MIME[m][0]
    a = Attachment(reference=_s(kennung, 80) or os.path.splitext(name)[0][:80], description=_s(beschreibung, 200),
                   filename=name, mime=m, size=len(daten))
    a.content = bytes(daten)
    return a


def anhaenge_pruefen(anhaenge) -> list:
    """Regeln für die Anhänge EINER Rechnung → Liste von (regel, text)."""
    aus, namen, gesamt = [], set(), 0
    eingebettet = [a for a in (anhaenge or []) if (a.filename or a.mime or a.path or a.content)]
    if len(anhaenge or []) > ANHANG_MAX_ANZAHL:
        aus.append(("ANHANG-ANZAHL", f"Höchstens {ANHANG_MAX_ANZAHL} Anhänge je Rechnung."))
    for a in (anhaenge or []):
        if not (a.reference or "").strip():
            aus.append(("BR-52", "Ein Anhang braucht eine Kennung (BT-122)."))
        if (a.url or "").strip() and not re.match(r"^https?://\S+$", a.url.strip(), re.I):
            aus.append(("BT-124", f"Der Verweis „{a.url}“ ist keine Internetadresse (https://…)."))
    for a in eingebettet:
        if a.mime not in ANHANG_MIME:
            aus.append(("BR-CL-24", f"Anhang „{a.filename}“: Dateiart {a.mime or 'unbekannt'} ist in der E-Rechnung nicht zulässig "
                                    f"(erlaubt: PDF, PNG, JPEG, CSV, XLSX, ODS)."))
        n = (a.filename or "").lower()
        if n in namen:
            aus.append(("BR-DE-22", f"Der Dateiname „{a.filename}“ kommt mehrfach vor; jeder Anhang braucht einen eigenen Namen."))
        namen.add(n)
        gesamt += int(a.size or 0)
    if gesamt > ANHANG_MAX_GESAMT:
        aus.append(("ANHANG-GROESSE", f"Die Anhänge sind zusammen {gesamt / 1048576:.1f} MB groß; möglich sind höchstens "
                                      f"{ANHANG_MAX_GESAMT // 1048576} MB. Größere Unterlagen bitte als Verweis (Internetadresse) angeben."))
    return aus


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Lieferort
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _kategorien(inv) -> set:
    return {(x.tax_category or "S") for x in list(inv.lines) + list(inv.allowances_charges)}


def lieferort(inv):
    """Wirksamer Lieferort (BG-13/BG-15). Ohne eigene Lieferadresse gilt bei der innergemeinschaftlichen Lieferung
    und bei der Ausfuhr die Anschrift des Käufers als Lieferort (BR-IC-12 verlangt das Lieferland BT-80)."""
    d = getattr(inv, "delivery", None)
    if d is not None and not d.leer():
        return d
    if _kategorien(inv) & {"K", "G"}:
        from models import DeliveryInfo
        import copy
        return DeliveryInfo(name="", address=copy.deepcopy(inv.buyer.address))
    return None


def lieferland(inv) -> str:
    d = lieferort(inv)
    if d is not None and d.address is not None and (d.address.country_code or "").strip():
        return d.address.country_code.strip().upper()
    return ""


def ist_lastschrift(inv) -> bool:
    return str(getattr(inv.payment, "means_code", "") or "") == "59"


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# UBL 2.1 schreiben
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
_CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
_UNS = {"cbc": _CBC, "cac": _CAC}

# Reihenfolge der Kindelemente laut Schema (xsd:sequence) – eine Vertauschung macht die Datei ungültig
_UBL_KOPF = ("UBLExtensions UBLVersionID CustomizationID ProfileID ProfileExecutionID ID CopyIndicator UUID IssueDate "
             "IssueTime DueDate TaxPointDate InvoiceTypeCode CreditNoteTypeCode Note DocumentCurrencyCode TaxCurrencyCode "
             "PricingCurrencyCode PaymentCurrencyCode PaymentAlternativeCurrencyCode AccountingCostCode AccountingCost "
             "LineCountNumeric BuyerReference InvoicePeriod DiscrepancyResponse OrderReference BillingReference "
             "DespatchDocumentReference ReceiptDocumentReference {mitte} Signature AccountingSupplierParty "
             "AccountingCustomerParty PayeeParty BuyerCustomerParty SellerSupplierParty TaxRepresentativeParty Delivery "
             "DeliveryTerms PaymentMeans PaymentTerms PrepaidPayment {ende} TaxTotal WithholdingTaxTotal "
             "LegalMonetaryTotal InvoiceLine CreditNoteLine")
_UBL_FOLGE_RECHNUNG = _UBL_KOPF.format(
    mitte="StatementDocumentReference OriginatorDocumentReference ContractDocumentReference "
          "AdditionalDocumentReference ProjectReference",
    ende="AllowanceCharge TaxExchangeRate PricingExchangeRate PaymentExchangeRate PaymentAlternativeExchangeRate").split()
_UBL_FOLGE_GUTSCHRIFT = _UBL_KOPF.format(
    mitte="ContractDocumentReference AdditionalDocumentReference StatementDocumentReference OriginatorDocumentReference",
    ende="TaxExchangeRate PricingExchangeRate PaymentExchangeRate PaymentAlternativeExchangeRate AllowanceCharge").split()
_UBL_FOLGEN = {
    "PaymentMeans": "ID PaymentMeansCode PaymentDueDate PaymentChannelCode InstructionID InstructionNote PaymentID "
                    "CardAccount PayerFinancialAccount PayeeFinancialAccount CreditAccount PaymentMandate TradeFinancing".split(),
    "Delivery": "ID Quantity MinimumQuantity MaximumQuantity ActualDeliveryDate ActualDeliveryTime LatestDeliveryDate "
                "LatestDeliveryTime ReleaseID TrackingID DeliveryAddress DeliveryLocation AlternativeDeliveryLocation "
                "RequestedDeliveryPeriod PromisedDeliveryPeriod EstimatedDeliveryPeriod CarrierParty DeliveryParty".split(),
    "LegalMonetaryTotal": "LineExtensionAmount TaxExclusiveAmount TaxInclusiveAmount AllowanceTotalAmount "
                          "ChargeTotalAmount PrepaidAmount PayableRoundingAmount PayableAmount".split(),
    "OrderReference": "ID SalesOrderID CopyIndicator UUID IssueDate".split(),
    "Party": "MarkCareIndicator MarkAttentionIndicator WebsiteURI LogoReferenceID EndpointID "
             "IndustryClassificationCode PartyIdentification PartyName Language PostalAddress PhysicalLocation "
             "PartyTaxScheme PartyLegalEntity Contact".split(),
}


def _lokal(el) -> str:
    return etree.QName(el.tag).localname if isinstance(el.tag, str) else ""


def _einordnen(eltern, neu, folge, hinter_gleichen: bool = True):
    """Hängt `neu` an die Stelle, die das Schema vorschreibt: vor das erste Kind, das laut `folge` später kommt
    (gleichnamige Geschwister bleiben davor)."""
    name = _lokal(neu)
    pos = folge.index(name) if name in folge else len(folge)
    for i, kind in enumerate(list(eltern)):
        k = _lokal(kind)
        kp = folge.index(k) if k in folge else len(folge)
        if kp > pos or (kp == pos and not hinter_gleichen):
            eltern.insert(i, neu)
            return neu
    eltern.append(neu)
    return neu


def _u(tag, ns=_CBC, text=None, **attr):
    el = etree.Element(f"{{{ns}}}{tag}")
    if text is not None:
        el.text = " ".join(str(text).split()) if isinstance(text, str) else str(text)
    for k, v in attr.items():
        if v:
            el.set(k, v)
    return el


def _usub(eltern, tag, ns=_CBC, text=None, **attr):
    el = _u(tag, ns, text, **attr)
    eltern.append(el)
    return el


def _ubl_adresse(eltern, addr, tag="Address"):
    a = _usub(eltern, tag, _CAC)
    haus = (getattr(addr, "house_number", "") or "").strip()
    strasse = ((addr.street or "") + (" " + haus if haus else "")).strip()
    if strasse:
        _usub(a, "StreetName", text=strasse)
    if (addr.address_line_2 or "").strip():
        _usub(a, "AdditionalStreetName", text=addr.address_line_2)
    if (addr.city or "").strip():
        _usub(a, "CityName", text=addr.city)
    if (addr.post_code or "").strip():
        _usub(a, "PostalZone", text=addr.post_code)
    land = _usub(a, "Country", _CAC)
    _usub(land, "IdentificationCode", text=(addr.country_code or "DE").strip().upper())
    return a


def _betrag_text(wert) -> str:
    return str(Decimal(str(wert)).quantize(Decimal("0.01")))


def hat_zusatz(inv) -> bool:
    """True, wenn der Beleg irgendeine Angabe trägt, die dieses Modul schreibt. Sonst bleibt die Datei unberührt."""
    if any(getattr(inv, f, "") for f, *_ in BEZUEGE):
        return True
    if getattr(inv, "delivery", None) is not None and not inv.delivery.leer():
        return True
    if getattr(inv, "payee", None) is not None and not inv.payee.leer():
        return True
    if getattr(inv, "attachments", None):
        return True
    if (getattr(inv, "tax_currency_code", "") or "").strip():
        return True
    if Decimal(str(getattr(inv, "rounding_amount", 0) or 0)) != 0:
        return True
    p = inv.payment
    if ist_lastschrift(inv) or (p.mandate_reference or p.creditor_id or p.debited_account):
        return True
    if _kategorien(inv) & {"K", "G", "O"}:
        return True
    return False


def ubl_ergaenzen(root, inv):
    """Ergänzt die fertige UBL-Rechnung bzw. -Gutschrift um die Zusatzangaben. Ohne Zusatzangaben keine Änderung."""
    if not hat_zusatz(inv):
        return root
    gutschrift = _lokal(root) == "CreditNote"
    folge = _UBL_FOLGE_GUTSCHRIFT if gutschrift else _UBL_FOLGE_RECHNUNG
    cur = inv.currency_code or "EUR"
    kats = _kategorien(inv)

    def kopf(el):
        return _einordnen(root, el, folge)

    # BT-6 Steuerwährung, BT-19 Buchungsreferenz des Käufers
    steuerwaehrung = (getattr(inv, "tax_currency_code", "") or "").strip().upper()
    if steuerwaehrung and steuerwaehrung != cur:
        kopf(_u("TaxCurrencyCode", text=steuerwaehrung))
    if getattr(inv, "buyer_accounting_reference", ""):
        kopf(_u("AccountingCost", text=inv.buyer_accounting_reference))

    # BT-14 Auftragsnummer des Verkäufers: in OrderReference; ohne Bestellnummer (BT-13) verlangt UBL dort
    # trotzdem eine ID – die Syntaxbindung der Norm sieht dafür „NA“ vor.
    if getattr(inv, "sales_order_reference", ""):
        oref = root.find("cac:OrderReference", _UNS)
        if oref is None:
            oref = kopf(_u("OrderReference", _CAC))
            _usub(oref, "ID", text="NA")
        _einordnen(oref, _u("SalesOrderID", text=inv.sales_order_reference), _UBL_FOLGEN["OrderReference"])
    for feld, tag in (("despatch_advice_reference", "DespatchDocumentReference"),      # BT-16
                      ("receiving_advice_reference", "ReceiptDocumentReference"),      # BT-15
                      ("tender_reference", "OriginatorDocumentReference")):            # BT-17
        if getattr(inv, feld, ""):
            _usub(kopf(_u(tag, _CAC)), "ID", text=getattr(inv, feld))
    # BT-18 Objektkennung: AdditionalDocumentReference mit Dokumenttyp 130
    if getattr(inv, "object_identifier", ""):
        adr = kopf(_u("AdditionalDocumentReference", _CAC))
        _usub(adr, "ID", text=inv.object_identifier, schemeID=(getattr(inv, "object_identifier_scheme", "") or "").strip())
        _usub(adr, "DocumentTypeCode", text="130")
    # BG-24 Anhänge
    for a in (getattr(inv, "attachments", None) or []):
        adr = kopf(_u("AdditionalDocumentReference", _CAC))
        _usub(adr, "ID", text=a.reference or a.filename or "Anhang")
        if (a.description or "").strip():
            _usub(adr, "DocumentDescription", text=a.description)
        daten = a.daten()
        if daten or (a.url or "").strip():
            att = _usub(adr, "Attachment", _CAC)
            if daten:
                bo = _usub(att, "EmbeddedDocumentBinaryObject", mimeCode=a.mime, filename=a.filename)
                bo.text = base64.b64encode(daten).decode("ascii")
            if (a.url or "").strip():
                _usub(_usub(att, "ExternalReference", _CAC), "URI", text=a.url.strip())

    # BG-10 Zahlungsempfänger
    payee = getattr(inv, "payee", None)
    hat_payee = payee is not None and not payee.leer()
    lastschrift = ist_lastschrift(inv)
    glaeubiger = (inv.payment.creditor_id or "").strip() if lastschrift else ""
    if hat_payee:
        pp = kopf(_u("PayeeParty", _CAC))
        if (payee.payee_id or "").strip():
            _usub(_usub(pp, "PartyIdentification", _CAC), "ID", text=payee.payee_id)
        if glaeubiger:      # BT-90 gehört zum Zahlungsempfänger, wenn es einen gibt
            _usub(_usub(pp, "PartyIdentification", _CAC), "ID", text=glaeubiger, schemeID="SEPA")
        _usub(_usub(pp, "PartyName", _CAC), "Name", text=payee.name)
        if (payee.company_id or "").strip():
            _usub(_usub(pp, "PartyLegalEntity", _CAC), "CompanyID", text=payee.company_id)
    verkaeufer = root.find("cac:AccountingSupplierParty/cac:Party", _UNS)
    if glaeubiger and not hat_payee and verkaeufer is not None:
        pid = _u("PartyIdentification", _CAC)
        _usub(pid, "ID", text=glaeubiger, schemeID="SEPA")
        _einordnen(verkaeufer, pid, _UBL_FOLGEN["Party"])

    # BG-13 / BG-15 Lieferung
    d = lieferort(inv)
    if d is not None:
        dl = root.find("cac:Delivery", _UNS)
        if dl is None:
            dl = kopf(_u("Delivery", _CAC))
        if d.address is not None or (d.location_id or "").strip():
            loc = _einordnen(dl, _u("DeliveryLocation", _CAC), _UBL_FOLGEN["Delivery"])
            if (d.location_id or "").strip():
                _usub(loc, "ID", text=d.location_id)
            if d.address is not None:
                _ubl_adresse(loc, d.address)
        if (d.name or "").strip():
            dp = _einordnen(dl, _u("DeliveryParty", _CAC), _UBL_FOLGEN["Delivery"])
            _usub(_usub(dp, "PartyName", _CAC), "Name", text=d.name)

    # BG-19 Lastschrift: Mandat, IBAN des Zahlungspflichtigen – und KEIN Überweisungskonto (BR-DE-25-b)
    pm = root.find("cac:PaymentMeans", _UNS)
    if lastschrift and pm is not None:
        for alt in pm.findall("cac:PayeeFinancialAccount", _UNS):
            pm.remove(alt)
        mand = _einordnen(pm, _u("PaymentMandate", _CAC), _UBL_FOLGEN["PaymentMeans"])
        if (inv.payment.mandate_reference or "").strip():
            _usub(mand, "ID", text=inv.payment.mandate_reference)
        if iban_norm(inv.payment.debited_account):
            _usub(_usub(mand, "PayerFinancialAccount", _CAC), "ID", text=iban_norm(inv.payment.debited_account))

    # Kategorie O (nicht steuerbar): keine USt-ID von Verkäufer und Käufer (BR-O-02), kein Satz in der Position
    # (BR-O-05); die Aufschlüsselung trägt den Satz 0 (BR-DE-14). Der Verkäufer bleibt über Steuernummer (BT-32)
    # und Kennung (BT-29) bestimmt.
    if "O" in kats:
        for party in root.findall("cac:AccountingSupplierParty/cac:Party", _UNS) + \
                root.findall("cac:AccountingCustomerParty/cac:Party", _UNS):
            for pts in party.findall("cac:PartyTaxScheme", _UNS):
                if (pts.findtext("cac:TaxScheme/cbc:ID", "", _UNS) or "").strip() == "VAT":
                    party.remove(pts)
        if verkaeufer is not None and not (
                [x for x in verkaeufer.findall("cac:PartyIdentification/cbc:ID", _UNS) if x.get("schemeID") != "SEPA"]
                or verkaeufer.find("cac:PartyLegalEntity/cbc:CompanyID", _UNS) is not None):
            kennung = (inv.seller.tax_registration_id or "").strip()
            if kennung:
                pid = _u("PartyIdentification", _CAC)
                _usub(pid, "ID", text=kennung)
                _einordnen(verkaeufer, pid, _UBL_FOLGEN["Party"], hinter_gleichen=False)
        for zeile in root.iter(f"{{{_CAC}}}ClassifiedTaxCategory"):
            if (zeile.findtext("cbc:ID", "", _UNS) or "").strip() == "O":
                for pz in zeile.findall("cbc:Percent", _UNS):
                    zeile.remove(pz)
        for ac in root.findall("cac:AllowanceCharge/cac:TaxCategory", _UNS):
            if (ac.findtext("cbc:ID", "", _UNS) or "").strip() == "O":
                for pz in ac.findall("cbc:Percent", _UNS):
                    ac.remove(pz)

    # BT-111 Steuerbetrag in Buchungswährung: zweites TaxTotal nur mit dem Betrag
    if steuerwaehrung and steuerwaehrung != cur and getattr(inv, "tax_amount_accounting", None) is not None:
        tt = _u("TaxTotal", _CAC)
        _usub(tt, "TaxAmount", text=_betrag_text(inv.tax_amount_accounting), currencyID=steuerwaehrung)
        _einordnen(root, tt, folge)

    # BT-114 Rundungsbetrag
    rund = Decimal(str(getattr(inv, "rounding_amount", 0) or 0))
    lmt = root.find("cac:LegalMonetaryTotal", _UNS)
    if rund != 0 and lmt is not None:
        _einordnen(lmt, _u("PayableRoundingAmount", text=_betrag_text(rund), currencyID=cur), _UBL_FOLGEN["LegalMonetaryTotal"])
    return root


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# CII D16B schreiben
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_RSM = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
_RAM = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
_UDT = "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"
_QDT = "urn:un:unece:uncefact:data:standard:QualifiedDataType:100"
_CNS = {"rsm": _RSM, "ram": _RAM, "udt": _UDT, "qdt": _QDT}
_CII_FOLGEN = {
    "agr": "Reference BuyerReference SellerTradeParty BuyerTradeParty SalesAgentTradeParty BuyerRequisitionerTradeParty "
           "BuyerAssignedAccountantTradeParty SellerAssignedAccountantTradeParty BuyerTaxRepresentativeTradeParty "
           "SellerTaxRepresentativeTradeParty ProductEndUserTradeParty ApplicableTradeDeliveryTerms "
           "SellerOrderReferencedDocument BuyerOrderReferencedDocument QuotationReferencedDocument "
           "OrderResponseReferencedDocument ContractReferencedDocument DemandForecastReferencedDocument "
           "SupplyInstructionReferencedDocument PromotionalDealReferencedDocument PriceListReferencedDocument "
           "AdditionalReferencedDocument RequisitionerReferencedDocument BuyerAgentTradeParty "
           "PurchaseConditionsReferencedDocument SpecifiedProcuringProject UltimateCustomerOrderReferencedDocument".split(),
    "dlv": "RelatedSupplyChainConsignment ShipToTradeParty UltimateShipToTradeParty ShipFromTradeParty "
           "ActualDespatchSupplyChainEvent ActualPickUpSupplyChainEvent ActualDeliverySupplyChainEvent "
           "ActualReceiptSupplyChainEvent AdditionalReferencedDocument DespatchAdviceReferencedDocument "
           "ReceivingAdviceReferencedDocument DeliveryNoteReferencedDocument".split(),
    "st": "DuePayableAmount CreditorReferenceTypeCode CreditorReferenceType CreditorReferenceIssuerID "
          "CreditorReferenceID PaymentReference TaxCurrencyCode InvoiceCurrencyCode PaymentCurrencyCode "
          "InvoiceIssuerReference InvoiceDateTime NextInvoiceDateTime CreditReasonCode CreditReason InvoicerTradeParty "
          "InvoiceeTradeParty PayeeTradeParty PayerTradeParty TaxApplicableTradeCurrencyExchange "
          "InvoiceApplicableTradeCurrencyExchange PaymentApplicableTradeCurrencyExchange "
          "SpecifiedTradeSettlementPaymentMeans ApplicableTradeTax BillingSpecifiedPeriod SpecifiedTradeAllowanceCharge "
          "SubtotalCalculatedTradeTax SpecifiedLogisticsServiceCharge SpecifiedTradePaymentTerms "
          "SpecifiedTradeSettlementHeaderMonetarySummation SpecifiedFinancialAdjustment InvoiceReferencedDocument "
          "ProFormaInvoiceReferencedDocument LetterOfCreditReferencedDocument FactoringAgreementReferencedDocument "
          "FactoringListReferencedDocument PayableSpecifiedTradeAccountingAccount "
          "ReceivableSpecifiedTradeAccountingAccount".split(),
    "pm": "PaymentChannelCode TypeCode GuaranteeMethodCode PaymentMethodCode Information ID "
          "ApplicableTradeSettlementFinancialCard PayerPartyDebtorFinancialAccount PayeePartyCreditorFinancialAccount "
          "PayerSpecifiedDebtorFinancialInstitution PayeeSpecifiedCreditorFinancialInstitution".split(),
    "sum": "LineTotalAmount ChargeTotalAmount AllowanceTotalAmount TaxBasisTotalAmount TaxTotalAmount RoundingAmount "
           "GrandTotalAmount InformationAmount TotalPrepaidAmount TotalDiscountAmount TotalAllowanceChargeAmount "
           "DuePayableAmount".split(),
    "pt": "ID FromEventCode SettlementPeriodMeasure Description DueDateDateTime TypeCode InstructionTypeCode "
          "DirectDebitMandateID".split(),
    "party": "ID GlobalID Name RoleCode Description SpecifiedLegalOrganization DefinedTradeContact PostalTradeAddress "
             "URIUniversalCommunication SpecifiedTaxRegistration".split(),
    "tax": "CalculatedAmount TypeCode ExemptionReason BasisAmount LineTotalBasisAmount AllowanceChargeBasisAmount "
           "CategoryCode ExemptionReasonCode TaxPointDate DueDateTypeCode RateApplicablePercent".split(),
}


def _r(tag, text=None, **attr):
    el = etree.Element(f"{{{_RAM}}}{tag}")
    if text is not None:
        el.text = " ".join(str(text).split()) if isinstance(text, str) else str(text)
    for k, v in attr.items():
        if v:
            el.set(k, v)
    return el


def _rsub(eltern, tag, text=None, **attr):
    el = _r(tag, text, **attr)
    eltern.append(el)
    return el


def _cii_adresse(eltern, addr):
    pa = _rsub(eltern, "PostalTradeAddress")
    if (addr.post_code or "").strip():
        _rsub(pa, "PostcodeCode", addr.post_code)
    haus = (getattr(addr, "house_number", "") or "").strip()
    strasse = ((addr.street or "") + (" " + haus if haus else "")).strip()
    if strasse:
        _rsub(pa, "LineOne", strasse)
    if (addr.address_line_2 or "").strip():
        _rsub(pa, "LineTwo", addr.address_line_2)
    if (addr.city or "").strip():
        _rsub(pa, "CityName", addr.city)
    _rsub(pa, "CountryID", (addr.country_code or "DE").strip().upper())


def cii_ergaenzen(root, inv):
    """Ergänzt die fertige CII-Datei um die Zusatzangaben. Ohne Zusatzangaben keine Änderung."""
    if not hat_zusatz(inv):
        return root
    tx = root.find("rsm:SupplyChainTradeTransaction", _CNS)
    if tx is None:
        return root
    agr = tx.find("ram:ApplicableHeaderTradeAgreement", _CNS)
    dlv = tx.find("ram:ApplicableHeaderTradeDelivery", _CNS)
    st = tx.find("ram:ApplicableHeaderTradeSettlement", _CNS)
    cur = inv.currency_code or "EUR"
    kats = _kategorien(inv)
    F = _CII_FOLGEN

    if agr is not None:
        if getattr(inv, "sales_order_reference", ""):                     # BT-14
            _rsub(_einordnen(agr, _r("SellerOrderReferencedDocument"), F["agr"]), "IssuerAssignedID", inv.sales_order_reference)
        if getattr(inv, "tender_reference", ""):                          # BT-17 (Dokumenttyp 50)
            ard = _einordnen(agr, _r("AdditionalReferencedDocument"), F["agr"])
            _rsub(ard, "IssuerAssignedID", inv.tender_reference)
            _rsub(ard, "TypeCode", "50")
        if getattr(inv, "object_identifier", ""):                         # BT-18 (Dokumenttyp 130)
            ard = _einordnen(agr, _r("AdditionalReferencedDocument"), F["agr"])
            _rsub(ard, "IssuerAssignedID", inv.object_identifier)
            _rsub(ard, "TypeCode", "130")
            if (getattr(inv, "object_identifier_scheme", "") or "").strip():
                _rsub(ard, "ReferenceTypeCode", inv.object_identifier_scheme.strip())
        for a in (getattr(inv, "attachments", None) or []):               # BG-24 (Dokumenttyp 916)
            ard = _einordnen(agr, _r("AdditionalReferencedDocument"), F["agr"])
            _rsub(ard, "IssuerAssignedID", a.reference or a.filename or "Anhang")
            if (a.url or "").strip():
                _rsub(ard, "URIID", a.url.strip())
            _rsub(ard, "TypeCode", "916")
            if (a.description or "").strip():
                _rsub(ard, "Name", a.description)
            daten = a.daten()
            if daten:
                bo = _rsub(ard, "AttachmentBinaryObject", mimeCode=a.mime, filename=a.filename)
                bo.text = base64.b64encode(daten).decode("ascii")

    if dlv is not None:
        d = lieferort(inv)
        if d is not None:                                                 # BG-13 / BG-15
            ship = _einordnen(dlv, _r("ShipToTradeParty"), F["dlv"])
            if (d.location_id or "").strip():
                _rsub(ship, "ID", d.location_id)
            if (d.name or "").strip():
                _rsub(ship, "Name", d.name)
            if d.address is not None:
                _cii_adresse(ship, d.address)
        if getattr(inv, "despatch_advice_reference", ""):                 # BT-16
            _rsub(_einordnen(dlv, _r("DespatchAdviceReferencedDocument"), F["dlv"]), "IssuerAssignedID", inv.despatch_advice_reference)
        if getattr(inv, "receiving_advice_reference", ""):                # BT-15
            _rsub(_einordnen(dlv, _r("ReceivingAdviceReferencedDocument"), F["dlv"]), "IssuerAssignedID", inv.receiving_advice_reference)

    if st is not None:
        lastschrift = ist_lastschrift(inv)
        steuerwaehrung = (getattr(inv, "tax_currency_code", "") or "").strip().upper()
        if lastschrift and (inv.payment.creditor_id or "").strip():       # BT-90
            _einordnen(st, _r("CreditorReferenceID", inv.payment.creditor_id.strip()), F["st"])
        if steuerwaehrung and steuerwaehrung != cur:                      # BT-6
            _einordnen(st, _r("TaxCurrencyCode", steuerwaehrung), F["st"])
        payee = getattr(inv, "payee", None)
        if payee is not None and not payee.leer():                        # BG-10
            pp = _einordnen(st, _r("PayeeTradeParty"), F["st"])
            if (payee.payee_id or "").strip():
                _rsub(pp, "ID", payee.payee_id)
            _rsub(pp, "Name", payee.name)
            if (payee.company_id or "").strip():
                _rsub(_rsub(pp, "SpecifiedLegalOrganization"), "ID", payee.company_id)
        pm = st.find("ram:SpecifiedTradeSettlementPaymentMeans", _CNS)
        pt = st.find("ram:SpecifiedTradePaymentTerms", _CNS)
        if lastschrift:
            # BG-19: IBAN des Zahlungspflichtigen (BT-91), kein Überweisungskonto (BR-DE-25-b)
            if pm is not None:
                for tag in ("PayeePartyCreditorFinancialAccount", "PayeeSpecifiedCreditorFinancialInstitution"):
                    for alt in pm.findall("ram:" + tag, _CNS):
                        pm.remove(alt)
                if iban_norm(inv.payment.debited_account):
                    _rsub(_einordnen(pm, _r("PayerPartyDebtorFinancialAccount"), F["pm"]), "IBANID", iban_norm(inv.payment.debited_account))
        elif pt is not None:
            # Mandatsreferenz ohne Lastschrift gehört nicht in die Datei (BR-DE-23-b bei Überweisung)
            for alt in pt.findall("ram:DirectDebitMandateID", _CNS):
                pt.remove(alt)
            if len(pt) == 0:
                st.remove(pt)
        summ = st.find("ram:SpecifiedTradeSettlementHeaderMonetarySummation", _CNS)
        if summ is not None:
            if steuerwaehrung and steuerwaehrung != cur and getattr(inv, "tax_amount_accounting", None) is not None:
                erste = summ.findall("ram:TaxTotalAmount", _CNS)          # BT-111 direkt hinter BT-110
                neu = _r("TaxTotalAmount", _betrag_text(inv.tax_amount_accounting), currencyID=steuerwaehrung)
                if erste:
                    erste[-1].addnext(neu)
                else:
                    _einordnen(summ, neu, F["sum"])
            rund = Decimal(str(getattr(inv, "rounding_amount", 0) or 0))
            if rund != 0:                                                 # BT-114
                _einordnen(summ, _r("RoundingAmount", _betrag_text(rund)), F["sum"])
        if getattr(inv, "buyer_accounting_reference", ""):                # BT-19
            _rsub(_einordnen(st, _r("ReceivableSpecifiedTradeAccountingAccount"), F["st"]), "ID", inv.buyer_accounting_reference)

        if "O" in kats:
            # Kategorie O: Aufschlüsselung mit Satz 0 (BR-DE-14), keine USt-ID von Verkäufer und Käufer (BR-O-02)
            for tt in st.findall("ram:ApplicableTradeTax", _CNS):
                if (tt.findtext("ram:CategoryCode", "", _CNS) or "").strip() == "O" \
                        and tt.find("ram:RateApplicablePercent", _CNS) is None:
                    _einordnen(tt, _r("RateApplicablePercent", "0"), F["tax"])
            if agr is not None:
                for pname in ("SellerTradeParty", "BuyerTradeParty"):
                    party = agr.find("ram:" + pname, _CNS)
                    if party is None:
                        continue
                    for reg in party.findall("ram:SpecifiedTaxRegistration", _CNS):
                        rid = reg.find("ram:ID", _CNS)
                        if rid is not None and (rid.get("schemeID") or "").upper() == "VA":
                            party.remove(reg)
                    if pname == "SellerTradeParty" and party.find("ram:ID", _CNS) is None \
                            and party.find("ram:SpecifiedLegalOrganization/ram:ID", _CNS) is None \
                            and (inv.seller.tax_registration_id or "").strip():
                        _einordnen(party, _r("ID", inv.seller.tax_registration_id.strip()), F["party"], hinter_gleichen=False)
    return root


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Lesen (UBL und CII)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _ut(el, pfad) -> str:
    if el is None:
        return ""
    k = el.find(pfad, _UNS)
    return (k.text or "").strip() if k is not None and k.text else ""


def _ct(el, pfad) -> str:
    if el is None:
        return ""
    k = el.find(pfad, _CNS)
    return (k.text or "").strip() if k is not None and k.text else ""


def _anhang_aus_datei(kennung, beschreibung, url, knoten):
    from models import Attachment
    a = Attachment(reference=kennung, description=beschreibung, url=url)
    if knoten is not None and (knoten.text or "").strip():
        try:
            daten = base64.b64decode("".join(knoten.text.split()), validate=False)
        except Exception:
            daten = b""
        a.mime = (knoten.get("mimeCode") or "").strip().lower()
        a.filename = dateiname_sicher(knoten.get("filename") or "", "anhang" + (ANHANG_MIME.get(a.mime) or (".bin",))[0])
        a.size = len(daten)
        a.content = daten
    return a


def ubl_lesen(root, inv) -> None:
    """Liest die Zusatzangaben aus einer UBL-Rechnung/-Gutschrift in das Fachobjekt."""
    from models import Address, DeliveryInfo, Payee
    inv.sales_order_reference = _ut(root, "cac:OrderReference/cbc:SalesOrderID")
    if inv.sales_order_reference and (inv.order_reference or "").strip().upper() == "NA":
        inv.order_reference = ""                     # „NA“ ist nur der Platzhalter der Syntax für „keine Bestellnummer“
    inv.despatch_advice_reference = _ut(root, "cac:DespatchDocumentReference/cbc:ID")
    inv.receiving_advice_reference = _ut(root, "cac:ReceiptDocumentReference/cbc:ID")
    inv.tender_reference = _ut(root, "cac:OriginatorDocumentReference/cbc:ID")
    inv.buyer_accounting_reference = _ut(root, "cbc:AccountingCost")
    inv.tax_currency_code = _ut(root, "cbc:TaxCurrencyCode")
    for adr in root.findall("cac:AdditionalDocumentReference", _UNS):
        typ = _ut(adr, "cbc:DocumentTypeCode")
        if typ == "130":
            idn = adr.find("cbc:ID", _UNS)
            inv.object_identifier = _ut(adr, "cbc:ID")
            inv.object_identifier_scheme = (idn.get("schemeID") or "") if idn is not None else ""
        elif typ == "50":
            continue                                 # Projektbezug BT-11 der Gutschrift – liest der Parser selbst
        else:
            inv.attachments.append(_anhang_aus_datei(
                _ut(adr, "cbc:ID"), _ut(adr, "cbc:DocumentDescription"),
                _ut(adr, "cac:Attachment/cac:ExternalReference/cbc:URI"),
                adr.find("cac:Attachment/cbc:EmbeddedDocumentBinaryObject", _UNS)))
    # BT-111: das TaxTotal ohne Aufschlüsselung in der Steuerwährung
    if inv.tax_currency_code:
        for tt in root.findall("cac:TaxTotal", _UNS):
            ta = tt.find("cbc:TaxAmount", _UNS)
            if ta is not None and (ta.get("currencyID") or "") == inv.tax_currency_code and (ta.text or "").strip():
                try:
                    inv.tax_amount_accounting = Decimal(ta.text.strip())
                except InvalidOperation:
                    pass
    try:
        inv.rounding_amount = Decimal(_ut(root, "cac:LegalMonetaryTotal/cbc:PayableRoundingAmount") or "0")
    except InvalidOperation:
        pass
    # BG-10
    pp = root.find("cac:PayeeParty", _UNS)
    if pp is not None:
        pid = ""
        for k in pp.findall("cac:PartyIdentification/cbc:ID", _UNS):
            if (k.get("schemeID") or "").upper() == "SEPA":
                inv.payment.creditor_id = (k.text or "").strip()
            elif not pid:
                pid = (k.text or "").strip()
        inv.payee = Payee(name=_ut(pp, "cac:PartyName/cbc:Name"), payee_id=pid,
                          company_id=_ut(pp, "cac:PartyLegalEntity/cbc:CompanyID"))
    # BG-13 / BG-15
    dl = root.find("cac:Delivery", _UNS)
    if dl is not None:
        a = dl.find("cac:DeliveryLocation/cac:Address", _UNS)
        d = DeliveryInfo(name=_ut(dl, "cac:DeliveryParty/cac:PartyName/cbc:Name"),
                         location_id=_ut(dl, "cac:DeliveryLocation/cbc:ID"))
        if a is not None:
            d.address = Address(street=_ut(a, "cbc:StreetName"), address_line_2=_ut(a, "cbc:AdditionalStreetName"),
                                city=_ut(a, "cbc:CityName"), post_code=_ut(a, "cbc:PostalZone"),
                                country_code=_ut(a, "cac:Country/cbc:IdentificationCode"))
        if not d.leer():
            inv.delivery = d
    # BG-19
    for k in root.findall("cac:AccountingSupplierParty/cac:Party/cac:PartyIdentification/cbc:ID", _UNS):
        if (k.get("schemeID") or "").upper() == "SEPA" and (k.text or "").strip():
            inv.payment.creditor_id = k.text.strip()
    mand = root.find("cac:PaymentMeans/cac:PaymentMandate", _UNS)
    if mand is not None:
        inv.payment.mandate_reference = _ut(mand, "cbc:ID")
        inv.payment.debited_account = _ut(mand, "cac:PayerFinancialAccount/cbc:ID")


def cii_lesen(root, inv) -> None:
    """Liest die Zusatzangaben aus einer CII-Datei (ZUGFeRD / Factur-X / XRechnung-CII)."""
    from models import Address, DeliveryInfo, Payee
    tx = root.find("rsm:SupplyChainTradeTransaction", _CNS)
    if tx is None:
        return
    agr = tx.find("ram:ApplicableHeaderTradeAgreement", _CNS)
    dlv = tx.find("ram:ApplicableHeaderTradeDelivery", _CNS)
    st = tx.find("ram:ApplicableHeaderTradeSettlement", _CNS)
    if agr is not None:
        inv.sales_order_reference = _ct(agr, "ram:SellerOrderReferencedDocument/ram:IssuerAssignedID")
        for ard in agr.findall("ram:AdditionalReferencedDocument", _CNS):
            typ = _ct(ard, "ram:TypeCode")
            if typ == "50":
                inv.tender_reference = _ct(ard, "ram:IssuerAssignedID")
            elif typ == "130":
                inv.object_identifier = _ct(ard, "ram:IssuerAssignedID")
                inv.object_identifier_scheme = _ct(ard, "ram:ReferenceTypeCode")
            else:
                inv.attachments.append(_anhang_aus_datei(
                    _ct(ard, "ram:IssuerAssignedID"), _ct(ard, "ram:Name"), _ct(ard, "ram:URIID"),
                    ard.find("ram:AttachmentBinaryObject", _CNS)))
    if dlv is not None:
        inv.despatch_advice_reference = _ct(dlv, "ram:DespatchAdviceReferencedDocument/ram:IssuerAssignedID")
        inv.receiving_advice_reference = _ct(dlv, "ram:ReceivingAdviceReferencedDocument/ram:IssuerAssignedID")
        ship = dlv.find("ram:ShipToTradeParty", _CNS)
        if ship is not None:
            d = DeliveryInfo(name=_ct(ship, "ram:Name"), location_id=_ct(ship, "ram:ID"))
            a = ship.find("ram:PostalTradeAddress", _CNS)
            if a is not None:
                d.address = Address(street=_ct(a, "ram:LineOne"), address_line_2=_ct(a, "ram:LineTwo"),
                                    city=_ct(a, "ram:CityName"), post_code=_ct(a, "ram:PostcodeCode"),
                                    country_code=_ct(a, "ram:CountryID"))
            if not d.leer():
                inv.delivery = d
    if st is not None:
        inv.tax_currency_code = _ct(st, "ram:TaxCurrencyCode")
        inv.buyer_accounting_reference = _ct(st, "ram:ReceivableSpecifiedTradeAccountingAccount/ram:ID")
        pp = st.find("ram:PayeeTradeParty", _CNS)
        if pp is not None:
            inv.payee = Payee(name=_ct(pp, "ram:Name"), payee_id=_ct(pp, "ram:ID") or _ct(pp, "ram:GlobalID"),
                              company_id=_ct(pp, "ram:SpecifiedLegalOrganization/ram:ID"))
        summ = st.find("ram:SpecifiedTradeSettlementHeaderMonetarySummation", _CNS)
        if summ is not None:
            try:
                inv.rounding_amount = Decimal(_ct(summ, "ram:RoundingAmount") or "0")
            except InvalidOperation:
                pass
            if inv.tax_currency_code:
                for k in summ.findall("ram:TaxTotalAmount", _CNS):
                    if (k.get("currencyID") or "") == inv.tax_currency_code and (k.text or "").strip():
                        try:
                            inv.tax_amount_accounting = Decimal(k.text.strip())
                        except InvalidOperation:
                            pass


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Speichern und Laden
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def anhang_ordner(data_dir, invoice_id: str) -> str:
    """Anhänge liegen im Archivordner der Rechnung: <data>/archiv/<rechnung_id>/anhaenge/"""
    return os.path.join(str(data_dir), "archiv", str(invoice_id), "anhaenge")


def _anhang_ablegen(a, ordner: str) -> None:
    """Schreibt den Inhalt eines Anhangs, der noch nur im Speicher liegt, in den Archivordner und gibt den
    Arbeitsspeicher frei. Gleicher Inhalt unter gleichem Namen wird nicht doppelt abgelegt."""
    daten = getattr(a, "content", None)
    if daten is None or (a.path and os.path.isfile(a.path)):
        if a.path and os.path.isfile(a.path):
            a.content = None
        return
    os.makedirs(ordner, exist_ok=True)
    name = dateiname_sicher(a.filename, "anhang")
    ziel = os.path.join(ordner, name)
    n = 1
    while os.path.exists(ziel):
        try:
            with open(ziel, "rb") as f:
                if hashlib.sha256(f.read()).digest() == hashlib.sha256(daten).digest():
                    break
        except OSError:
            pass
        stamm, endung = os.path.splitext(name)
        ziel = os.path.join(ordner, f"{stamm}-{n}{endung}")
        n += 1
    if not os.path.exists(ziel):
        tmp = ziel + f".{os.getpid()}.tmp"
        with open(tmp, "wb") as f:
            f.write(daten)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, ziel)
    a.path = ziel
    a.size = len(daten)
    a.content = None


def speichern(inv, data_dir):
    """JSON-fähiges dict der Zusatzangaben für invoices.json; None, wenn der Beleg keine trägt (Altbestand und
    Standardrechnungen bleiben schlank). Anhänge: nur Dateiname, Art, Größe, Prüfsumme – der Inhalt liegt als Datei
    im Archivordner."""
    aus = {}
    bez = {f: getattr(inv, f) for f, *_ in BEZUEGE if getattr(inv, f, "")}
    if getattr(inv, "object_identifier_scheme", ""):
        bez["object_identifier_scheme"] = inv.object_identifier_scheme
    if bez:
        aus["bezuege"] = bez
    d = getattr(inv, "delivery", None)
    if d is not None and not d.leer():
        a = d.address
        aus["lieferung"] = {"name": d.name, "location_id": d.location_id,
                            "adresse": ({"street": a.street, "house_number": a.house_number, "city": a.city,
                                         "post_code": a.post_code, "country_code": a.country_code,
                                         "address_line_2": a.address_line_2} if a is not None else None)}
    p = getattr(inv, "payee", None)
    if p is not None and not p.leer():
        aus["zahlungsempfaenger"] = {"name": p.name, "payee_id": p.payee_id, "company_id": p.company_id}
    anh = []
    for a in (getattr(inv, "attachments", None) or []):
        if getattr(a, "content", None) is not None:
            try:
                _anhang_ablegen(a, anhang_ordner(data_dir, inv._id))
            except OSError as e:
                print(f"  WARNUNG: Anhang „{a.filename}“ von {inv.invoice_number} nicht abgelegt: {e}", flush=True)
        anh.append({"reference": a.reference, "description": a.description, "url": a.url, "filename": a.filename,
                    "mime": a.mime, "size": int(a.size or 0), "datei": os.path.basename(a.path) if a.path else ""})
    if anh:
        aus["anhaenge"] = anh
    if (getattr(inv, "tax_currency_code", "") or "").strip():
        aus["tax_currency_code"] = inv.tax_currency_code
    if getattr(inv, "tax_amount_accounting", None) is not None:
        aus["tax_amount_accounting"] = str(inv.tax_amount_accounting)
    if Decimal(str(getattr(inv, "rounding_amount", 0) or 0)) != 0:
        aus["rounding_amount"] = str(inv.rounding_amount)
    return aus or None


def laden(inv, d, data_dir) -> None:
    """Gegenstück zu speichern(). d = None (Altbestand): nichts zu tun."""
    if not isinstance(d, dict) or not d:
        return
    from models import Address, Attachment, DeliveryInfo, Payee
    for f, wert in (d.get("bezuege") or {}).items():
        if hasattr(inv, f) and isinstance(wert, str):
            setattr(inv, f, wert)
    l = d.get("lieferung")
    if isinstance(l, dict):
        a = l.get("adresse")
        inv.delivery = DeliveryInfo(
            name=l.get("name", "") or "", location_id=l.get("location_id", "") or "",
            address=(Address(street=a.get("street", "") or "", house_number=a.get("house_number", "") or "",
                             city=a.get("city", "") or "", post_code=a.get("post_code", "") or "",
                             country_code=a.get("country_code", "") or "",
                             address_line_2=a.get("address_line_2", "") or "") if isinstance(a, dict) else None))
    z = d.get("zahlungsempfaenger")
    if isinstance(z, dict):
        inv.payee = Payee(name=z.get("name", "") or "", payee_id=z.get("payee_id", "") or "",
                          company_id=z.get("company_id", "") or "")
    ordner = anhang_ordner(data_dir, inv._id)
    for x in (d.get("anhaenge") or []):
        if not isinstance(x, dict):
            continue
        inv.attachments.append(Attachment(
            reference=x.get("reference", "") or "", description=x.get("description", "") or "",
            url=x.get("url", "") or "", filename=x.get("filename", "") or "", mime=x.get("mime", "") or "",
            size=int(x.get("size") or 0),
            path=(os.path.join(ordner, os.path.basename(x["datei"])) if x.get("datei") else "")))
    inv.tax_currency_code = d.get("tax_currency_code", "") or ""
    if d.get("tax_amount_accounting") not in (None, ""):
        try:
            inv.tax_amount_accounting = Decimal(str(d["tax_amount_accounting"]))
        except InvalidOperation:
            pass
    if d.get("rounding_amount") not in (None, ""):
        try:
            inv.rounding_amount = Decimal(str(d["rounding_amount"]))
        except InvalidOperation:
            pass


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Prüfung
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def pruefung_ergaenzen(inv, report, eingang: bool) -> None:
    """Regeln zu den Zusatzangaben. Beim AUSSTELLEN sind Verstöße Fehler (die amtliche Prüfung lehnt den Beleg
    sonst ab), beim Einlesen eines fremden Belegs Warnungen."""
    from validator import ValidationIssue, Severity
    iss = report.issues

    def err(rid, msg, fld=""):
        iss.append(ValidationIssue(rid, Severity.ERROR, msg, fld))

    def warn(rid, msg, fld=""):
        iss.append(ValidationIssue(rid, Severity.WARNING, msg, fld))

    def regel(rid, msg, fld=""):
        if eingang:
            warn(rid, msg + " (eingelesener Beleg: nur Hinweis)", fld)
        else:
            err(rid, msg, fld)

    kats = _kategorien(inv)
    v_land = (inv.seller.address.country_code or "").strip().upper()
    k_land = (inv.buyer.address.country_code or "").strip().upper()

    # ── Land des Käufers (BT-55) und USt-ID (BT-48) ──
    if not k_land:
        regel("BR-11", "Das Land des Käufers (BT-55) fehlt.", "BT-55")
    elif not _ISO2.match(k_land):
        regel("BR-CL-14", f"Das Land des Käufers „{k_land}“ ist kein zweistelliger Ländercode (ISO 3166).", "BT-55")
    if (inv.buyer.vat_id or "").strip() and not eingang:
        stufe, text = ustid_pruefen(inv.buyer.vat_id, k_land)
        if stufe == "praefix" and (kats & {"K", "AE"}):
            err("BT-48-FORM", text + " Für diesen Steuerfall muss die USt-ID des Kunden stimmen.", "BT-48")
        elif stufe:
            warn("BT-48-FORM", text, "BT-48")

    # ── Steuersatz 0 bei K, G; kein Satz bei O ──
    for kat, rid, name in (("K", "BR-IC-05", "K (innergemeinschaftliche Lieferung)"), ("G", "BR-G-05", "G (Ausfuhr)"),
                           ("O", "BR-O-05", "O (nicht steuerbar)")):
        if kat in kats and any(Decimal(str(x.tax_rate or 0)) != 0 for x in list(inv.lines) + list(inv.allowances_charges)
                               if (x.tax_category or "S") == kat):
            regel(rid, f"Kategorie {name} verlangt den Steuersatz 0 %.", "BT-152")

    # ── Innergemeinschaftliche Lieferung (BR-IC-*) ──
    if "K" in kats:
        if eingang:
            if not (inv.seller.vat_id and inv.buyer.vat_id):
                warn("BR-IC-02", "Innergemeinschaftliche Lieferung: USt-ID von Verkäufer (BT-31) und Käufer (BT-48) gehören in den Beleg.", "BT-31/BT-48")
        else:
            if not (inv.seller.vat_id or "").strip():
                err("BR-IC-02", "Innergemeinschaftliche Lieferung: Ihre USt-ID (BT-31) ist erforderlich (Einstellungen → Stammdaten).", "BT-31")
            if not (inv.buyer.vat_id or "").strip():
                err("BR-IC-02", "Innergemeinschaftliche Lieferung: Die USt-ID des Kunden (BT-48) ist erforderlich.", "BT-48")
            if k_land and v_land and k_land == v_land:
                err("IG-LAND", f"Innergemeinschaftliche Lieferung: Der Kunde sitzt im selben Land wie der Verkäufer ({land_name(k_land)}). "
                               f"Der Kunde muss in einem anderen EU-Mitgliedstaat ansässig sein.", "BT-55")
            elif k_land and not ist_eu(k_land):
                err("IG-LAND", f"Innergemeinschaftliche Lieferung: {land_name(k_land)} ist kein EU-Mitgliedstaat. "
                               f"Für Lieferungen in ein Drittland wählen Sie den Steuerfall „Ausfuhrlieferung“.", "BT-55")
        l_land = lieferland(inv)
        if not l_land:
            regel("BR-IC-12", "Innergemeinschaftliche Lieferung: Das Land des Lieferorts (BT-80) fehlt.", "BT-80")
        elif not eingang:
            if l_land == v_land:
                err("IG-LIEFERLAND", f"Innergemeinschaftliche Lieferung: Der Lieferort liegt im Land des Verkäufers ({land_name(l_land)}). "
                                     f"Die Ware muss in einen anderen EU-Mitgliedstaat gelangen.", "BT-80")
            elif not ist_eu(l_land):
                err("IG-LIEFERLAND", f"Innergemeinschaftliche Lieferung: Der Lieferort liegt in {land_name(l_land)}, also außerhalb der EU.", "BT-80")
        if not (inv.tax_point_date or inv.period_start or inv.period_end):
            regel("BR-IC-11", "Innergemeinschaftliche Lieferung: Das Lieferdatum (BT-72) oder der Rechnungszeitraum (BG-14) fehlt.", "BT-72")

    # ── Ausfuhr (BR-G-*) ──
    if "G" in kats and not eingang:
        if not (inv.seller.vat_id or "").strip():
            err("BR-G-02", "Ausfuhrlieferung: Ihre USt-ID (BT-31) ist erforderlich (Einstellungen → Stammdaten).", "BT-31")
        l_land = lieferland(inv) or k_land
        if l_land and ist_eu(l_land):
            err("AUSFUHR-LAND", f"Ausfuhrlieferung: Der Lieferort liegt in {land_name(l_land)}, also in der EU. Eine Ausfuhr geht in ein "
                                f"Drittland; für Lieferungen in einen anderen EU-Mitgliedstaat gilt die innergemeinschaftliche Lieferung.", "BT-80")

    # ── Nicht steuerbar (BR-O-*) ──
    if "O" in kats:
        if len(kats) > 1:
            regel("BR-O-11", "Ein Beleg mit nicht steuerbaren Positionen (Kategorie O) darf keine anderen Steuerkategorien enthalten.", "BT-151")
        if not eingang:
            if not (inv.seller.tax_registration_id or "").strip() and not (getattr(inv.seller, "company_id", "") or getattr(inv.seller, "seller_id", "")):
                err("BR-O-02", "Nicht steuerbarer Umsatz: Die E-Rechnung darf hier keine USt-ID tragen (BR-O-02). Der Verkäufer wird "
                               "dann über die Steuernummer bestimmt – bitte die Steuernummer in den Stammdaten eintragen.", "BT-32")
            if k_land and v_land and k_land == v_land:
                err("NSTB-LAND", f"Nicht steuerbar (Leistungsort im Ausland): Der Kunde sitzt im Land des Verkäufers ({land_name(k_land)}).", "BT-55")
            elif k_land and ist_eu(k_land):
                warn("NSTB-LAND", f"Der Kunde sitzt in {land_name(k_land)}, also in der EU. Für Leistungen an EU-Unternehmer ist in der Regel "
                                  f"der Steuerfall „Steuerschuld des Kunden“ (Reverse Charge) richtig.", "BT-55")

    # ── Reverse Charge mit ausländischem Käufer: Hinweis, wenn der Käufer im Drittland sitzt ──
    if "AE" in kats and not eingang and k_land and v_land and k_land != v_land and not ist_eu(k_land):
        warn("AE-LAND", f"Steuerschuld des Kunden: Der Kunde sitzt in {land_name(k_land)}, also außerhalb der EU. Für Leistungen an "
                        f"Unternehmer im Drittland ist in der Regel „Nicht steuerbar“ richtig.", "BT-55")

    # ── Lieferadresse ──
    d = getattr(inv, "delivery", None)
    if d is not None and not d.leer() and d.address is not None:
        lc = (d.address.country_code or "").strip().upper()
        if not lc:
            regel("BR-57", "Lieferadresse: Das Land (BT-80) fehlt.", "BT-80")
        elif not _ISO2.match(lc):
            regel("BR-CL-14", f"Lieferadresse: „{lc}“ ist kein zweistelliger Ländercode (ISO 3166).", "BT-80")

    # ── Lastschrift (BG-19) ──
    if ist_lastschrift(inv):
        p = inv.payment
        if not (p.creditor_id or "").strip():
            regel("BR-DE-30", "Lastschrift: Die Gläubiger-Identifikationsnummer (BT-90) fehlt (Einstellungen → Stammdaten).", "BT-90")
        elif not eingang and not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{3}[A-Z0-9]{1,28}", iban_norm(p.creditor_id)):
            warn("BT-90-FORM", f"Die Gläubiger-Identifikationsnummer „{p.creditor_id}“ hat nicht den üblichen Aufbau (z. B. DE98ZZZ09999999999).", "BT-90")
        if not iban_norm(p.debited_account):
            regel("BR-DE-31", "Lastschrift: Die IBAN des Zahlungspflichtigen (BT-91) fehlt.", "BT-91")
        elif not iban_formal_ok(p.debited_account):
            warn("BR-DE-20", f"Lastschrift: Die IBAN des Zahlungspflichtigen „{p.debited_account}“ ist formal nicht richtig.", "BT-91")
        if eingang and not (p.mandate_reference or "").strip():
            pass          # die Mandatsreferenz meldet die Grundprüfung bereits

    # ── Zahlungsempfänger (BG-10) ──
    pe = getattr(inv, "payee", None)
    if pe is not None and not pe.leer():
        if not (pe.name or "").strip():
            regel("BR-17", "Abweichender Zahlungsempfänger: Der Name (BT-59) fehlt.", "BT-59")
        elif " ".join(pe.name.split()).lower() == " ".join((inv.seller.name or "").split()).lower() and not eingang:
            warn("BG-10", "Der Zahlungsempfänger heißt wie der Verkäufer. Die Angabe ist nur nötig, wenn ein anderer das Geld erhält.", "BT-59")

    # ── Anhänge (BG-24) ──
    for rid, text in anhaenge_pruefen(getattr(inv, "attachments", None) or []):
        regel(rid, text, "BG-24")
    if not eingang:
        for a in (getattr(inv, "attachments", None) or []):
            if (a.filename or a.path) and a.daten() is None and not (a.url or "").strip():
                err("ANHANG-FEHLT", f"Die Datei des Anhangs „{a.filename}“ liegt nicht mehr im Archivordner.", "BT-125")

    # ── Währung (BT-5, BT-6, BT-111) ──
    cur = (inv.currency_code or "").strip().upper()
    steuerw = (getattr(inv, "tax_currency_code", "") or "").strip().upper()
    if not eingang and cur and cur != "EUR":
        err("WAEHRUNG-EUR", f"Rechnungen werden nur in Euro ausgestellt (angegeben: {cur}). PDF, Zahlungsabgleich und "
                            f"Buchhaltung des Programms rechnen in Euro.", "BT-5")
    if steuerw:
        if steuerw == cur:
            regel("BT-6", "Die Steuerwährung (BT-6) ist nur anzugeben, wenn sie von der Rechnungswährung abweicht.", "BT-6")
        elif getattr(inv, "tax_amount_accounting", None) is None:
            regel("BR-53", f"Mit Steuerwährung {steuerw} (BT-6) muss der Steuerbetrag in dieser Währung (BT-111) angegeben sein.", "BT-111")
    elif getattr(inv, "tax_amount_accounting", None) is not None:
        regel("BR-53", "Ein Steuerbetrag in Buchungswährung (BT-111) ohne Angabe der Steuerwährung (BT-6).", "BT-6")

    # ── Rundungsbetrag (BT-114) ──
    rund = Decimal(str(getattr(inv, "rounding_amount", 0) or 0))
    if rund != 0:
        if rund != rund.quantize(Decimal("0.01")):
            regel("BT-114", f"Der Rundungsbetrag ({rund}) darf höchstens zwei Nachkommastellen haben.", "BT-114")
        if abs(rund) >= Decimal("1") and not eingang:
            err("BT-114", f"Der Rundungsbetrag ({rund}) ist zu groß – er gleicht nur Cent-Beträge aus (unter 1,00).", "BT-114")
        # Die Grundprüfung kennt die Rundung nicht: ihre Meldungen dazu entfallen, wenn die Rundung sie erklärt
        brutto = inv.tax_inclusive_amount()
        vorab = Decimal(str(getattr(inv, "prepaid_amount", 0) or 0))
        if inv.amount_due() == brutto - vorab + rund:
            iss[:] = [i for i in iss if i.rule_id not in ("BR-CO-16", "SUMME-BT-114")]


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Anfrage (Maske, Schnittstelle, CSV) → Fachobjekt
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _aus(data, *schluessel):
    for k in schluessel:
        if isinstance(data, dict) and data.get(k) not in (None, ""):
            return data.get(k)
    return ""


def aus_anfrage(inv, data, stammdaten=None) -> None:
    """Übernimmt die Zusatzangaben einer Anfrage (POST /api/generate, CSV-Import) in den Beleg.
    Unlesbares (unbekanntes Land, kaputter Anhang) wird nicht geraten: ValueError mit Klartext → Antwort 400."""
    from models import Address, Attachment, DeliveryInfo, Payee
    if not isinstance(data, dict):
        return
    ms = stammdaten if isinstance(stammdaten, dict) else {}

    # Land des Käufers (BT-55)
    if _aus(data, "buyer_country", "buyer_country_code"):
        inv.buyer.address.country_code = land_normalisieren(_aus(data, "buyer_country", "buyer_country_code"))
    if _aus(data, "seller_country"):
        inv.seller.address.country_code = land_normalisieren(data.get("seller_country"))

    # weitere Bezüge
    for feld, _bt, _name, schluessel in BEZUEGE:
        if schluessel in data:
            setattr(inv, feld, _s(data.get(schluessel), 100))
    if "object_identifier_scheme" in data:
        inv.object_identifier_scheme = _s(data.get("object_identifier_scheme"), 10)

    # Lieferadresse (BG-13/BG-15): verschachtelt {"delivery": {...}} oder flach delivery_name, delivery_street …
    l = data.get("delivery") if isinstance(data.get("delivery"), dict) else {
        k[len("delivery_"):]: v for k, v in data.items() if k.startswith("delivery_") and k != "delivery_date"}
    if l and any(str(v or "").strip() for v in l.values()):
        name, kennung = _s(l.get("name"), 150), _s(l.get("location_id"), 60)
        strasse, haus = _s(l.get("street"), 150), _s(l.get("house_number"), 20)
        plz, ort = _s(l.get("post_code") or l.get("postcode") or l.get("plz"), 20), _s(l.get("city"), 100)
        zeile2 = _s(l.get("address_line_2"), 150)
        land_roh = l.get("country") or l.get("country_code") or ""
        hat_adresse = bool(strasse or plz or ort or zeile2 or str(land_roh).strip())
        adr = None
        if hat_adresse:
            adr = Address(street=strasse, house_number=haus, city=ort, post_code=plz, address_line_2=zeile2,
                          country_code=land_normalisieren(land_roh, vorgabe=inv.buyer.address.country_code or "DE"))
        inv.delivery = DeliveryInfo(name=name, location_id=kennung, address=adr)

    # Lastschrift (BG-19)
    if ist_lastschrift(inv):
        inv.payment.mandate_reference = _s(_aus(data, "mandate_reference", "mandatsreferenz"), 60)
        inv.payment.creditor_id = iban_norm(_aus(data, "creditor_id", "glaeubiger_id") or ms.get("glaeubiger_id", ""))[:40]
        inv.payment.debited_account = iban_norm(_aus(data, "debited_account", "buyer_iban"))[:40]
        if str(getattr(inv, "invoice_type_code", "380")) != "381":
            wann = f" zum {inv.payment.due_date.strftime('%d.%m.%Y')}" if inv.payment.due_date else ""
            inv.payment.payment_terms = (f"Der Rechnungsbetrag wird{wann} per SEPA-Lastschrift eingezogen "
                                         f"(Mandatsreferenz {inv.payment.mandate_reference or '–'}).")
            inv.payment.skonto_percent, inv.payment.skonto_days = 0.0, 0
    elif str(getattr(inv.payment, "means_code", "58") or "58") not in ("58", "30", "59"):
        raise ValueError(f"Zahlungsart „{inv.payment.means_code}“ wird nicht unterstützt. Möglich sind 58 (Überweisung) "
                         f"und 59 (SEPA-Lastschrift).")

    # Zahlungsempfänger (BG-10): aus der Anfrage; fehlt das Feld ganz, gilt die Vorgabe der Stammdaten
    if "payee_name" in data or isinstance(data.get("payee"), dict):
        z = data.get("payee") if isinstance(data.get("payee"), dict) else {
            "name": data.get("payee_name"), "payee_id": data.get("payee_id"), "company_id": data.get("payee_company_id")}
    else:
        z = {"name": ms.get("zahlungsempfaenger_name"), "payee_id": ms.get("zahlungsempfaenger_kennung"),
             "company_id": ms.get("zahlungsempfaenger_register")}
    if any(str(v or "").strip() for v in z.values()):
        inv.payee = Payee(name=_s(z.get("name"), 150), payee_id=_s(z.get("payee_id") or z.get("id"), 60),
                          company_id=_s(z.get("company_id"), 60))

    # Anhänge (BG-24): eingebettet (data_base64) oder als Verweis (url)
    for i, x in enumerate(data.get("attachments") or data.get("anhaenge") or [], 1):
        if not isinstance(x, dict):
            continue
        roh = x.get("data_base64") or x.get("daten_base64") or ""
        url = str(x.get("url") or "").strip()
        if roh:
            try:
                daten = base64.b64decode("".join(str(roh).split()), validate=True)
            except Exception:
                raise ValueError(f"Anhang {i}: Die Datei ist nicht lesbar (base64).")
            a = anhang_bauen(x.get("filename") or x.get("name") or f"anhang-{i}", daten, x.get("mime") or "",
                             x.get("description") or x.get("beschreibung") or "", x.get("reference") or x.get("kennung") or "")
            a.url = url
        elif url:
            a = Attachment(reference=_s(x.get("reference") or x.get("kennung"), 80) or f"Verweis-{i}",
                           description=_s(x.get("description") or x.get("beschreibung"), 200), url=url)
        else:
            continue
        inv.attachments.append(a)
    for rid, text in anhaenge_pruefen(inv.attachments):
        if rid in ("ANHANG-GROESSE", "ANHANG-ANZAHL"):
            raise ValueError(text)

    # Steuerwährung (BT-6/BT-111) und Rundung (BT-114) – nur über Schnittstelle/CSV
    if _aus(data, "tax_currency", "tax_currency_code"):
        inv.tax_currency_code = str(_aus(data, "tax_currency", "tax_currency_code")).strip().upper()[:3]
    if data.get("tax_amount_accounting") not in (None, ""):
        inv.tax_amount_accounting = _dez(data.get("tax_amount_accounting"))
    if data.get("rounding_amount") not in (None, ""):
        inv.rounding_amount = _dez(data.get("rounding_amount")) or Decimal("0.00")


def uebernehmen(ziel, quelle, voll: bool = False) -> None:
    """Storno und Gutschrift: Lieferort, Zahlungsempfänger und Bezüge der Ursprungsrechnung gelten weiter – ohne
    Lieferland wäre das Storno einer innergemeinschaftlichen Lieferung amtlich ungültig. Anhänge wandern nicht mit.
    Eine Gutschrift wird nicht eingezogen: aus der Lastschrift wird die Überweisung."""
    import copy
    for feld, *_ in BEZUEGE:
        setattr(ziel, feld, getattr(quelle, feld, "") or "")
    ziel.object_identifier_scheme = getattr(quelle, "object_identifier_scheme", "") or ""
    ziel.delivery = copy.deepcopy(getattr(quelle, "delivery", None))
    ziel.payee = copy.deepcopy(getattr(quelle, "payee", None))
    if voll:
        ziel.rounding_amount = Decimal(str(getattr(quelle, "rounding_amount", 0) or 0))
    try:
        ziel.buyer.address.country_code = quelle.buyer.address.country_code
    except Exception:
        pass
    if str(getattr(ziel.payment, "means_code", "")) == "59":
        ziel.payment.means_code = "58"
        ziel.payment.mandate_reference = ""
        ziel.payment.debited_account = ""
        ziel.payment.creditor_id = ""


# ── CSV-Import ──
CSV_SPALTEN = {
    "kunde_land": ("kunde_land", "land", "kunde_laendercode", "country"),
    "zahlungsart": ("zahlungsart", "zahlart", "payment_code"),
    "mandatsreferenz": ("mandatsreferenz", "mandat", "mandate_reference"),
    "kunde_iban": ("kunde_iban", "iban_kunde", "debited_account"),
    "lieferadresse_name": ("lieferadresse_name", "lieferung_name", "empfaenger_name"),
    "lieferadresse_strasse": ("lieferadresse_strasse", "lieferadresse_straße", "lieferung_strasse"),
    "lieferadresse_plz": ("lieferadresse_plz", "lieferung_plz"),
    "lieferadresse_ort": ("lieferadresse_ort", "lieferung_ort"),
    "lieferadresse_land": ("lieferadresse_land", "lieferung_land", "lieferland"),
    "zahlungsempfaenger_name": ("zahlungsempfaenger_name", "zahlungsempfänger_name", "zahlungsempfaenger", "zahlungsempfänger"),
    "zahlungsempfaenger_kennung": ("zahlungsempfaenger_kennung", "zahlungsempfänger_kennung"),
    "zahlungsempfaenger_register": ("zahlungsempfaenger_register", "zahlungsempfänger_register"),
    "auftragsnummer": ("auftragsnummer", "auftrag", "auftragsnr", "sales_order_reference"),
    "wareneingang": ("wareneingang", "wareneingangsmeldung", "receiving_advice_reference"),
    "versandanzeige": ("versandanzeige", "lieferschein", "lieferscheinnummer", "despatch_advice_reference"),
    "ausschreibung": ("ausschreibung", "los", "vergabenummer", "tender_reference"),
    "objektkennung": ("objektkennung", "objekt", "zaehlernummer", "zählernummer", "object_identifier"),
    "kostenstelle": ("kostenstelle", "buchungsreferenz", "buyer_accounting_reference"),
    "rundung": ("rundung", "rundungsbetrag", "rounding_amount"),
    "anhang_verweis": ("anhang_verweis", "anhang_url", "anlage_url"),
}
_CSV_ZAHLUNGSART = {"": "", "58": "58", "ueberweisung": "58", "überweisung": "58", "30": "58",
                    "59": "59", "lastschrift": "59", "sepa-lastschrift": "59", "sepa_lastschrift": "59", "sepa": "59"}


def csv_uebernehmen(body: dict, wert, probleme: list) -> None:
    """CSV-Zeile → Felder der Anfrage. `wert(spalte)` liefert den Text der ersten Zeile der Rechnung."""
    if wert("kunde_land"):
        try:
            body["buyer_country"] = land_normalisieren(wert("kunde_land"))
        except ValueError as e:
            probleme.append(str(e))
    za = wert("zahlungsart").strip().lower()
    if za not in _CSV_ZAHLUNGSART:
        probleme.append(f"Zahlungsart „{wert('zahlungsart')}“ unbekannt (58 Überweisung oder 59 Lastschrift)")
    elif _CSV_ZAHLUNGSART[za]:
        body["payment_code"] = _CSV_ZAHLUNGSART[za]
    for spalte, feld in (("mandatsreferenz", "mandate_reference"), ("kunde_iban", "debited_account"),
                         ("lieferadresse_name", "delivery_name"), ("lieferadresse_strasse", "delivery_street"),
                         ("lieferadresse_plz", "delivery_post_code"), ("lieferadresse_ort", "delivery_city"),
                         ("zahlungsempfaenger_kennung", "payee_id"), ("zahlungsempfaenger_register", "payee_company_id"),
                         ("auftragsnummer", "sales_order_reference"), ("wareneingang", "receiving_advice_reference"),
                         ("versandanzeige", "despatch_advice_reference"), ("ausschreibung", "tender_reference"),
                         ("objektkennung", "object_identifier"), ("kostenstelle", "buyer_accounting_reference")):
        if wert(spalte):
            body[feld] = wert(spalte)
    if wert("zahlungsempfaenger_name"):
        body["payee_name"] = wert("zahlungsempfaenger_name")
    if wert("lieferadresse_land"):
        try:
            body["delivery_country"] = land_normalisieren(wert("lieferadresse_land"))
        except ValueError as e:
            probleme.append("Lieferadresse: " + str(e))
    if wert("rundung"):
        try:
            body["rounding_amount"] = str(_dez(wert("rundung")))
        except ValueError:
            probleme.append(f"Rundung „{wert('rundung')}“ ist kein Betrag")
    if wert("anhang_verweis"):
        body["attachments"] = [{"url": wert("anhang_verweis"), "reference": "Anlage"}]


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# PDF
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _fmt_eur(wert) -> str:
    t = f"{Decimal(str(wert)).quantize(Decimal('0.01')):,.2f}"
    return t.replace(",", "X").replace(".", ",").replace("X", ".")


def adresse_zeilen(addr, mit_land_ab: str = "DE") -> list:
    """Anschrift als Zeilen; das Land in Großbuchstaben, wenn es vom Inland abweicht (DIN 5008)."""
    z = []
    if addr is None:
        return z
    s = addr.street_full() if hasattr(addr, "street_full") else (addr.street or "")
    if s:
        z.append(s)
    if (addr.address_line_2 or "").strip():
        z.append(addr.address_line_2.strip())
    po = f"{addr.post_code or ''} {addr.city or ''}".strip()
    if po:
        z.append(po)
    c = (addr.country_code or "").strip().upper()
    if c and c != (mit_land_ab or "").upper():
        z.append(land_name(c).upper())
    return z


def pdf_angaben(inv, buyer_data: dict, steuer_pdf: dict, schlusstext: str) -> str:
    """Trägt die Zusatzangaben in die Daten des PDF ein: Land in der Anschrift, weitere Bezüge in der Kopfzeile;
    Lieferadresse, Zahlungsempfänger und Anhänge als Absatz vor dem Schlusstext. Liefert den neuen Schlusstext.
    Ohne Zusatzangaben bleibt alles, wie es war."""
    try:
        inland = (inv.seller.address.country_code or "DE").strip().upper()
        k_land = (inv.buyer.address.country_code or "").strip().upper()
        if isinstance(buyer_data, dict) and k_land and k_land != inland:
            buyer_data["country_name"] = land_name(k_land)
        if isinstance(steuer_pdf, dict):
            bez = list(steuer_pdf.get("bezuege") or [])
            for feld, _bt, name, _k in BEZUEGE:
                if getattr(inv, feld, ""):
                    bez.append((name, getattr(inv, feld)))
            if bez:
                steuer_pdf["bezuege"] = bez
        absaetze = []
        d = getattr(inv, "delivery", None)
        if d is not None and not d.leer():
            teile = ([d.name] if (d.name or "").strip() else []) + adresse_zeilen(d.address, inland)
            if (d.location_id or "").strip():
                teile.append(f"Kennung des Lieferorts: {d.location_id}")
            if teile:
                absaetze.append("Lieferadresse: " + ", ".join(teile))
        p = getattr(inv, "payee", None)
        if p is not None and not p.leer() and (p.name or "").strip():
            zusatz = ", ".join(x for x in ((f"Kennung {p.payee_id}" if p.payee_id else ""),
                                           (f"Register {p.company_id}" if p.company_id else "")) if x)
            absaetze.append(f"Zahlungsempfänger: Die Forderung aus dieser Rechnung ist abgetreten. Zahlungen mit schuldbefreiender "
                            f"Wirkung bitte ausschließlich an {p.name}" + (f" ({zusatz})" if zusatz else "") + ".")
        anh = [a for a in (getattr(inv, "attachments", None) or [])]
        if anh:
            namen = [(a.description or a.filename or a.reference or a.url) for a in anh]
            absaetze.append("Anlagen zur elektronischen Rechnung: " + "; ".join(n for n in namen if n) + ".")
        rund = Decimal(str(getattr(inv, "rounding_amount", 0) or 0))
        if rund != 0:
            absaetze.append(f"Rundung: {_fmt_eur(rund)} EUR. Zu zahlen sind {_fmt_eur(inv.amount_due())} EUR.")
        if not absaetze:
            return schlusstext
        return "\n\n".join(absaetze) + ("\n\n" + schlusstext if schlusstext else "")
    except Exception as e:      # das PDF darf an den Zusatzangaben nie scheitern
        print(f"[rechnungszusatz] PDF-Angaben für {getattr(inv, 'invoice_number', '?')}: {e}", flush=True)
        return schlusstext


def pdf_zahlhinweis(inv, bisher):
    """Lastschrift: statt Überweisungskonto, „Zahlbar bis“ und GiroCode steht der Einzugshinweis mit Mandat und
    Gläubiger-ID. Für alle anderen Zahlungsarten bleibt der bisherige Wert (in der Regel None)."""
    try:
        if not ist_lastschrift(inv) or str(getattr(inv, "invoice_type_code", "380")) == "381":
            return bisher
        p = inv.payment
        wann = f" zum {p.due_date.strftime('%d.%m.%Y')}" if p.due_date else ""
        konto = f" {iban_gekuerzt(p.debited_account)}" if iban_norm(p.debited_account) else ""
        return (f"Der Betrag von {_fmt_eur(inv.amount_due())} EUR wird{wann} per SEPA-Lastschrift von Ihrem Konto{konto} "
                f"eingezogen. Mandatsreferenz: {p.mandate_reference or '–'} · Gläubiger-Identifikationsnummer: "
                f"{p.creditor_id or '–'}. Bitte überweisen Sie den Betrag nicht.")
    except Exception:
        return bisher


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Rechnungsansicht
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def ansicht(inv) -> dict:
    """Alles, was die Rechnungsansicht über die Standardangaben hinaus zeigt (auch bei eingelesenen Fremdbelegen)."""
    import models as _m
    d = getattr(inv, "delivery", None)
    p = getattr(inv, "payee", None)
    fall = getattr(inv, "steuerfall", "regel") or "regel"
    aus = {
        "steuerfall": fall,
        "steuerfall_name": (_m.STEUERFAELLE.get(fall) or {}).get("name", fall),
        "steuerhinweis": inv.steuerhinweis() if hasattr(inv, "steuerhinweis") else "",
        "kaeufer_land": (inv.buyer.address.country_code or "").upper(),
        "kaeufer_land_name": land_name(inv.buyer.address.country_code),
        "verkaeufer_land": (inv.seller.address.country_code or "").upper(),
        "verkaeufer_land_name": land_name(inv.seller.address.country_code),
        "bezuege": [{"bt": bt, "name": name, "wert": getattr(inv, feld)} for feld, bt, name, _k in BEZUEGE if getattr(inv, feld, "")],
        "lieferung": None, "zahlungsempfaenger": None, "lastschrift": None, "anhaenge": [],
        "steuerwaehrung": getattr(inv, "tax_currency_code", "") or "",
        "steuerbetrag_buchungswaehrung": (float(inv.tax_amount_accounting) if getattr(inv, "tax_amount_accounting", None) is not None else None),
        "rundung": float(Decimal(str(getattr(inv, "rounding_amount", 0) or 0))),
        "zahlungsart": str(inv.payment.means_code or ""),
        "anhang_grenze_mb": ANHANG_MAX_GESAMT // 1048576,
    }
    if d is not None and not d.leer():
        a = d.address
        aus["lieferung"] = {"name": d.name, "kennung": d.location_id,
                            "zeilen": adresse_zeilen(a, ""), "land": (a.country_code or "").upper() if a is not None else "",
                            "land_name": land_name(a.country_code) if a is not None else ""}
    if p is not None and not p.leer():
        aus["zahlungsempfaenger"] = {"name": p.name, "kennung": p.payee_id, "register": p.company_id}
    pay = inv.payment
    if ist_lastschrift(inv) or pay.mandate_reference or pay.creditor_id or pay.debited_account:
        aus["lastschrift"] = {"mandat": pay.mandate_reference, "glaeubiger_id": pay.creditor_id, "iban": pay.debited_account}
    for i, a in enumerate(getattr(inv, "attachments", None) or []):
        aus["anhaenge"].append({"nr": i, "kennung": a.reference, "beschreibung": a.description, "url": a.url,
                                "datei": a.filename, "art": a.mime, "groesse": int(a.size or 0),
                                "ladbar": bool(a.filename and a.daten() is not None),
                                "zulaessig": (a.mime in ANHANG_MIME) if a.filename else True})
    aus["vorhanden"] = bool(aus["bezuege"] or aus["lieferung"] or aus["zahlungsempfaenger"] or aus["lastschrift"]
                            or aus["anhaenge"] or aus["steuerwaehrung"] or aus["rundung"]
                            or (aus["kaeufer_land"] and aus["kaeufer_land"] != aus["verkaeufer_land"]) or fall != "regel")
    return aus


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Einhängen in die Anwendung (Routen). Aufruf am Ende von webapp.py: einrichten(app, globals())
# ══════════════════════════════════════════════════════════════════════════════════════════════════
KUNDENFELDER = ("country", "iban", "mandate_reference")          # Zusatzfelder am Kunden (buyers.json)
STAMMFELDER = ("glaeubiger_id", "zahlungsempfaenger_name", "zahlungsempfaenger_kennung", "zahlungsempfaenger_register")


def kunde_felder(kunde: dict, data: dict) -> bool:
    """Land, IBAN und Mandatsreferenz am Kundendatensatz setzen. True, wenn sich etwas geändert hat."""
    geaendert = False
    if "country" in data:
        neu = land_normalisieren(data.get("country"), vorgabe="")
        if neu == "DE":
            neu = ""             # Inland ist die Vorgabe und wird nicht gespeichert
        if (kunde.get("country") or "") != neu:
            kunde["country"], geaendert = neu, True
    if "iban" in data:
        neu = iban_norm(data.get("iban"))[:40]
        if neu and not iban_formal_ok(neu):
            raise ValueError(f"Die IBAN „{data.get('iban')}“ ist formal nicht richtig.")
        if (kunde.get("iban") or "") != neu:
            kunde["iban"], geaendert = neu, True
    if "mandate_reference" in data:
        neu = _s(data.get("mandate_reference"), 60)
        if (kunde.get("mandate_reference") or "") != neu:
            kunde["mandate_reference"], geaendert = neu, True
    return geaendert


def einrichten(app, g: dict) -> None:
    """Hängt die Routen der Zusatzangaben ein. g = globals() von webapp.py (Rechnungen, Kunden, Stammdaten)."""
    from flask import request, Response

    _json = g["_json"]
    invoices = g["invoices"]

    def _data_dir():
        st = g.get("store")
        return str(getattr(st, "data_dir", "")) if st is not None else ""

    def _gesperrt(inv) -> str:
        """Anhänge lassen sich nur an der eigenen, noch nicht versandten Rechnung ändern."""
        if (getattr(inv, "_direction", "") or "AUSGANG") == "EINGANG":
            return "eingelesene Rechnung"
        if (inv.status or "") != "NEU":
            return f"Status {inv.status}"
        if any(e.event_type in ("EMAIL_VERSENDET", "EXPORTIERT") for e in inv.audit_trail):
            return "bereits versendet"
        return ""

    def _archiv_auffrischen(inv) -> None:
        """Nach einer Änderung der Anhänge an einer noch nicht versandten Rechnung: die archivierte XML (und das
        PDF) neu schreiben, Prüfsumme und Prüfergebnis im Archivverzeichnis nachziehen, die amtliche Prüfung neu
        anstoßen. Ohne das trüge das Archiv den Stand vor der Änderung."""
        try:
            arch = g.get("archive")
            xml = g["generate_and_serialize"](inv)
            bericht = g["validate_invoice"](inv)
            eintraege = [r for r in getattr(arch, "_index", []) if r.get("invoice_id") == inv._id] if arch is not None else []
            if eintraege:
                ordner = os.path.join(str(arch.root), inv._id)
                for r in eintraege:
                    with open(os.path.join(ordner, r["xml_filename"]), "wb") as f:
                        f.write(xml)
                    r["sha256_hash"] = hashlib.sha256(xml).hexdigest()
                    r["validation_status"] = "GÜLTIG" if bericht.is_valid else "UNGÜLTIG"
                    r["validation_report"] = bericht.to_dict()
                try:
                    pdf = g["_build_invoice_pdf"](inv)[1]
                    if pdf:
                        with open(os.path.join(ordner, "original.pdf"), "wb") as f:
                            f.write(pdf)
                except Exception as e:
                    print(f"[rechnungszusatz] PDF zu {inv.invoice_number} nicht erneuert: {e}", flush=True)
                arch._save_index()
            if getattr(inv, "_kosit", None):
                inv._kosit = None
            if "_kosit_im_hintergrund" in g:
                g["_kosit_im_hintergrund"](inv, xml)
        except Exception as e:
            print(f"[rechnungszusatz] Archiv zu {getattr(inv, 'invoice_number', '?')} nicht aufgefrischt: {e}", flush=True)

    @app.route("/api/zusatz/info")
    def api_zusatz_info():
        ms = {}
        try:
            ms = g["_load_mandant_settings"]() or {}
        except Exception:
            pass
        import models as _m
        return _json({
            "version": VERSION,
            "laender": [{"code": c, "name": n, "eu": c in EU_LAENDER} for c, n in LAENDER.items()],
            "steuerfaelle": {k: v["name"] for k, v in _m.STEUERFAELLE.items()},
            "anhang": {"arten": sorted({e for es in ANHANG_MIME.values() for e in es}),
                       "max_gesamt": ANHANG_MAX_GESAMT, "max_anzahl": ANHANG_MAX_ANZAHL},
            "stammdaten": {k: ms.get(k, "") or "" for k in STAMMFELDER},
            "bezuege": [{"feld": k, "bt": bt, "name": name} for _f, bt, name, k in BEZUEGE],
        })

    @app.route("/api/mandant/zusatz", methods=["POST"])
    def api_mandant_zusatz():
        data = request.get_json(silent=True) or {}
        ms = g["_load_mandant_settings"]() or {}
        for k in STAMMFELDER:
            if k in data:
                ms[k] = iban_norm(data.get(k))[:40] if k == "glaeubiger_id" else _s(data.get(k), 150)
        g["_save_mandant_settings"](ms)
        return _json({"saved": True, "stammdaten": {k: ms.get(k, "") or "" for k in STAMMFELDER}})

    @app.route("/api/buyers/<buyer_id>/zusatz", methods=["POST"])
    def api_buyer_zusatz(buyer_id):
        data = request.get_json(silent=True) or {}
        kunden = g["_load_buyers"]()
        k = next((b for b in kunden if b.get("id") == buyer_id), None)
        if k is None:
            return _json({"error": "Kunde nicht gefunden"}, 404)
        try:
            if kunde_felder(k, data):
                g["_save_buyers"](kunden)
        except ValueError as e:
            return _json({"error": str(e)}, 400)
        return _json({"saved": True, "buyer": k})

    @app.route("/api/invoices/<inv_id>/zusatz")
    def api_invoice_zusatz(inv_id):
        inv = invoices.get(inv_id)
        if not inv:
            return _json({"error": "Rechnung nicht gefunden"}, 404)
        aus = ansicht(inv)
        aus["anhang_aenderbar"] = not _gesperrt(inv)
        return _json(aus)

    @app.route("/api/invoices/<inv_id>/anhaenge/<int:nr>")
    def api_invoice_anhang_laden(inv_id, nr):
        inv = invoices.get(inv_id)
        liste = list(getattr(inv, "attachments", None) or []) if inv else []
        if not inv or nr < 0 or nr >= len(liste):
            return _json({"error": "Anhang nicht gefunden"}, 404)
        a = liste[nr]
        daten = a.daten()
        if daten is None:
            return _json({"error": "Zu diesem Anhang liegt keine Datei vor."}, 404)
        # Fremde Dateien nie im Browser ausführen: immer als Download, unbekannte Arten als Binärdatei
        art = a.mime if a.mime in ANHANG_MIME else "application/octet-stream"
        name = dateiname_sicher(a.filename, "anhang")
        return Response(daten, mimetype=art, headers={
            "Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store"})

    @app.route("/api/invoices/<inv_id>/anhaenge", methods=["POST"])
    def api_invoice_anhang_neu(inv_id):
        """Hängt eine Unterlage an eine eigene, noch nicht versandte Rechnung (multipart: datei, beschreibung;
        oder JSON: {url, beschreibung, kennung} für einen Verweis)."""
        inv = invoices.get(inv_id)
        if not inv:
            return _json({"error": "Rechnung nicht gefunden"}, 404)
        grund = _gesperrt(inv)
        if grund:
            return _json({"error": f"Anhänge lassen sich nur vor der Freigabe ändern ({grund})."}, 409)
        from models import Attachment
        try:
            f = request.files.get("datei") or request.files.get("file")
            if f is not None:
                a = anhang_bauen(f.filename or "anhang", f.read(ANHANG_MAX_GESAMT + 1), f.mimetype or "",
                                 request.form.get("beschreibung", ""), request.form.get("kennung", ""))
            else:
                data = request.get_json(silent=True) or {}
                url = str(data.get("url") or "").strip()
                if not url:
                    return _json({"error": "Bitte eine Datei oder einen Verweis (Internetadresse) angeben."}, 400)
                a = Attachment(reference=_s(data.get("kennung"), 80) or f"Verweis-{len(inv.attachments) + 1}",
                               description=_s(data.get("beschreibung"), 200), url=url)
            probe = list(inv.attachments) + [a]
            meldungen = anhaenge_pruefen(probe)
            if meldungen:
                return _json({"error": meldungen[0][1]}, 400)
        except ValueError as e:
            return _json({"error": str(e)}, 400)
        inv.attachments.append(a)
        if getattr(a, "content", None) is not None:
            _anhang_ablegen(a, anhang_ordner(_data_dir(), inv._id))
        inv.add_audit("ANHANG_HINZUGEFUEGT", user=g["_wer"]({}, "web-user") if "_wer" in g else "web-user",
                      comment=a.filename or a.url)
        _archiv_auffrischen(inv)
        g["auto_save"]()
        return _json({"success": True, "zusatz": dict(ansicht(inv), anhang_aenderbar=True)})

    @app.route("/api/invoices/<inv_id>/anhaenge/<int:nr>", methods=["DELETE"])
    def api_invoice_anhang_weg(inv_id, nr):
        inv = invoices.get(inv_id)
        if not inv or nr < 0 or nr >= len(getattr(inv, "attachments", None) or []):
            return _json({"error": "Anhang nicht gefunden"}, 404)
        grund = _gesperrt(inv)
        if grund:
            return _json({"error": f"Anhänge lassen sich nur vor der Freigabe ändern ({grund})."}, 409)
        a = inv.attachments.pop(nr)
        try:
            if a.path and os.path.isfile(a.path) and os.path.dirname(a.path) == anhang_ordner(_data_dir(), inv._id):
                os.remove(a.path)
        except OSError:
            pass
        inv.add_audit("ANHANG_ENTFERNT", user=g["_wer"]({}, "web-user") if "_wer" in g else "web-user",
                      comment=a.filename or a.url)
        _archiv_auffrischen(inv)
        g["auto_save"]()
        return _json({"success": True, "zusatz": dict(ansicht(inv), anhang_aenderbar=True)})


def kunde_in_beleg(inv, kunde) -> None:
    """Vorgangs- und Abschlagsweg bauen den Käufer aus dem Kundenstamm: das dort hinterlegte Land übernehmen."""
    try:
        land = str((kunde or {}).get("country") or "").strip()
        if land:
            inv.buyer.address.country_code = land_normalisieren(land)
    except Exception:
        pass
