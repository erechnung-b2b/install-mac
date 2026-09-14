"""
E-Rechnungssystem – CII-Generator (UN/CEFACT Cross Industry Invoice D16B)

Erzeugt aus dem Fachobjekt Invoice dieselben Rechnungsdaten wie
xrechnung_generator.py (UBL), aber in der CII-Syntax. CII ist die Syntax,
die ZUGFeRD / Factur-X im PDF erwartet (Anhang factur-x.xml).

Profil: XRECHNUNG (Guideline urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0).
Das ist zugleich das ZUGFeRD-Profil "XRECHNUNG" und laesst sich mit dem
KoSIT-Validator (Szenario XRechnung CII) pruefen.

Die Reihenfolge der Elemente folgt dem CII-Schema D16B — sie ist dort
verbindlich (xsd:sequence), eine Vertauschung macht die Datei ungueltig.
"""
from __future__ import annotations

from decimal import Decimal

from lxml import etree

from models import Invoice

NS_RSM = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
NS_RAM = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
NS_UDT = "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"
NS_QDT = "urn:un:unece:uncefact:data:standard:QualifiedDataType:100"
NSMAP = {"rsm": NS_RSM, "ram": NS_RAM, "udt": NS_UDT, "qdt": NS_QDT}

GUIDELINE_XRECHNUNG = "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"
# Profilname fuer die Factur-X/ZUGFeRD-XMP-Metadaten
ZUGFERD_PROFIL = "XRECHNUNG"


def _rsm(t): return f"{{{NS_RSM}}}{t}"
def _ram(t): return f"{{{NS_RAM}}}{t}"
def _udt(t): return f"{{{NS_UDT}}}{t}"


def _einzeilig(text) -> str:
    if isinstance(text, str) and ("\n" in text or "\r" in text):
        return " ".join(text.split())
    return text


def _el(parent, tag, text=None, **attrs):
    el = etree.SubElement(parent, _ram(tag))
    if text is not None:
        el.text = str(_einzeilig(text))
    for k, v in attrs.items():
        el.set(k, v)
    return el


def _txt(parent, tag, text):
    """Nur anlegen, wenn Inhalt vorhanden."""
    if text is None or text == "":
        return None
    return _el(parent, tag, text)


def _betrag(val) -> str:
    return str(Decimal(str(val)).quantize(Decimal("0.01")))


def _zahl(val) -> str:
    d = Decimal(str(val)).normalize()
    s = format(d, "f")
    return s if s not in ("-0", "") else "0"


def _datum(parent, tag, d):
    el = _el(parent, tag)
    ds = etree.SubElement(el, _udt("DateTimeString"))
    ds.set("format", "102")
    ds.text = d.strftime("%Y%m%d")
    return el


def _adresse(parent, addr):
    pa = _el(parent, "PostalTradeAddress")
    _txt(pa, "PostcodeCode", addr.post_code)
    house = (getattr(addr, "house_number", "") or "").strip()
    street = (addr.street + (" " + house if house else "")).strip() if addr.street else house
    _txt(pa, "LineOne", street)
    _txt(pa, "LineTwo", addr.address_line_2)
    _txt(pa, "CityName", addr.city)
    _el(pa, "CountryID", addr.country_code or "DE")


def _kontakt(parent, contact):
    if not contact or not (contact.name or contact.telephone or contact.email):
        return
    c = _el(parent, "DefinedTradeContact")
    _txt(c, "PersonName", contact.name)
    if contact.telephone:
        _el(_el(c, "TelephoneUniversalCommunication"), "CompleteNumber", contact.telephone)
    if contact.email:
        _el(_el(c, "EmailURIUniversalCommunication"), "URIID", contact.email)


def _elektronische_adresse(parent, adresse, schema):
    if adresse:
        _el(_el(parent, "URIUniversalCommunication"), "URIID", adresse, schemeID=schema or "EM")


def _steuernummern(parent, vat_id, tax_reg=""):
    if vat_id:
        _el(_el(parent, "SpecifiedTaxRegistration"), "ID", vat_id, schemeID="VA")
    if tax_reg:
        _el(_el(parent, "SpecifiedTaxRegistration"), "ID", tax_reg, schemeID="FC")


def _verkaeufer(parent, inv):
    s = inv.seller
    p = _el(parent, "SellerTradeParty")
    _el(p, "Name", s.name)
    if s.company_id:
        lo = _el(p, "SpecifiedLegalOrganization")
        _el(lo, "ID", s.company_id)
    _kontakt(p, s.contact)
    _adresse(p, s.address)
    _elektronische_adresse(p, s.electronic_address, s.electronic_address_scheme)
    _steuernummern(p, s.vat_id, s.tax_registration_id)


def _kaeufer(parent, inv):
    b = inv.buyer
    p = _el(parent, "BuyerTradeParty")
    _el(p, "Name", b.name)
    if b.company_id:
        lo = _el(p, "SpecifiedLegalOrganization")
        _el(lo, "ID", b.company_id)
    _kontakt(p, b.contact)
    _adresse(p, b.address)
    _elektronische_adresse(p, b.electronic_address, b.electronic_address_scheme)
    _steuernummern(p, b.vat_id)


def _steuer_kategorie(parent, category, rate):
    t = _el(parent, "CategoryTradeTax")
    _el(t, "TypeCode", "VAT")
    _el(t, "CategoryCode", category)
    if category != "O":
        _el(t, "RateApplicablePercent", _zahl(rate))


def _nachlass_zuschlag(parent, ac, cur, kopf=True):
    a = _el(parent, "SpecifiedTradeAllowanceCharge")
    ind = _el(a, "ChargeIndicator")
    etree.SubElement(ind, _udt("Indicator")).text = "true" if ac.is_charge else "false"
    if ac.percentage:
        _el(a, "CalculationPercent", _zahl(ac.percentage))
    if ac.base_amount:
        _el(a, "BasisAmount", _betrag(ac.base_amount))
    _el(a, "ActualAmount", _betrag(ac.amount))
    _txt(a, "ReasonCode", ac.reason_code)
    _txt(a, "Reason", ac.reason)
    if kopf:
        _steuer_kategorie(a, ac.tax_category, ac.tax_rate)


def _position(parent, line, cur):
    li = _el(parent, "IncludedSupplyChainTradeLineItem")
    doc = _el(li, "AssociatedDocumentLineDocument")
    _el(doc, "LineID", line.line_id)
    if line.note:
        _el(_el(doc, "IncludedNote"), "Content", line.note)

    prod = _el(li, "SpecifiedTradeProduct")
    _txt(prod, "SellerAssignedID", line.item_id)
    _el(prod, "Name", line.item_name)
    _txt(prod, "Description", line.item_description)

    agr = _el(li, "SpecifiedLineTradeAgreement")
    if line.order_reference:
        _el(_el(agr, "BuyerOrderReferencedDocument"), "LineID", line.order_reference)
    price = _el(agr, "NetPriceProductTradePrice")
    _el(price, "ChargeAmount", _zahl(line.unit_price))
    if line.price_base_quantity != Decimal("1"):
        _el(price, "BasisQuantity", _zahl(line.price_base_quantity), unitCode=line.unit_code)

    dlv = _el(li, "SpecifiedLineTradeDelivery")
    _el(dlv, "BilledQuantity", _zahl(line.quantity), unitCode=line.unit_code)

    st = _el(li, "SpecifiedLineTradeSettlement")
    tax = _el(st, "ApplicableTradeTax")
    _el(tax, "TypeCode", "VAT")
    _el(tax, "CategoryCode", line.tax_category)
    if line.tax_category != "O":
        _el(tax, "RateApplicablePercent", _zahl(line.tax_rate))
    if line.period_start or line.period_end:
        per = _el(st, "BillingSpecifiedPeriod")
        if line.period_start:
            _datum(per, "StartDateTime", line.period_start)
        if line.period_end:
            _datum(per, "EndDateTime", line.period_end)
    for ac in line.allowances_charges:
        _nachlass_zuschlag(st, ac, cur, kopf=False)
    summ = _el(st, "SpecifiedTradeSettlementLineMonetarySummation")
    _el(summ, "LineTotalAmount", _betrag(line.line_net_amount))


def generate_cii(inv: Invoice) -> etree._Element:
    cur = inv.currency_code or "EUR"
    root = etree.Element(_rsm("CrossIndustryInvoice"), nsmap=NSMAP)

    ctx = etree.SubElement(root, _rsm("ExchangedDocumentContext"))
    _el(_el(ctx, "BusinessProcessSpecifiedDocumentContextParameter"), "ID",
        "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0")
    _el(_el(ctx, "GuidelineSpecifiedDocumentContextParameter"), "ID", GUIDELINE_XRECHNUNG)

    doc = etree.SubElement(root, _rsm("ExchangedDocument"))
    _el(doc, "ID", inv.invoice_number)
    _el(doc, "TypeCode", str(inv.invoice_type_code or "380"))
    _datum(doc, "IssueDateTime", inv.invoice_date)
    if inv.note:
        _el(_el(doc, "IncludedNote"), "Content", inv.note)

    tx = etree.SubElement(root, _rsm("SupplyChainTradeTransaction"))
    for line in inv.lines:
        _position(tx, line, cur)

    # ── Vereinbarung ──
    agr = _el(tx, "ApplicableHeaderTradeAgreement")
    _txt(agr, "BuyerReference", inv.buyer_reference or inv.buyer.buyer_reference)
    _verkaeufer(agr, inv)
    _kaeufer(agr, inv)
    if inv.order_reference:
        _el(_el(agr, "BuyerOrderReferencedDocument"), "IssuerAssignedID", inv.order_reference)
    if inv.contract_reference:
        _el(_el(agr, "ContractReferencedDocument"), "IssuerAssignedID", inv.contract_reference)
    if inv.project_reference:
        proj = _el(agr, "SpecifiedProcuringProject")
        _el(proj, "ID", inv.project_reference)
        _el(proj, "Name", "Projektreferenz")   # in CII Pflicht, fachlich ohne Bedeutung

    # ── Lieferung ──
    dlv = _el(tx, "ApplicableHeaderTradeDelivery")
    liefer = inv.tax_point_date or inv.period_end
    if liefer:
        ev = _el(dlv, "ActualDeliverySupplyChainEvent")
        _datum(ev, "OccurrenceDateTime", liefer)

    # ── Abrechnung ──
    st = _el(tx, "ApplicableHeaderTradeSettlement")
    _el(st, "InvoiceCurrencyCode", cur)
    pm = _el(st, "SpecifiedTradeSettlementPaymentMeans")
    _el(pm, "TypeCode", inv.payment.means_code or "58")
    if inv.payment.iban:
        acc = _el(pm, "PayeePartyCreditorFinancialAccount")
        _el(acc, "IBANID", inv.payment.iban.replace(" ", ""))
        if inv.payment.bic:
            _el(_el(pm, "PayeeSpecifiedCreditorFinancialInstitution"), "BICID", inv.payment.bic)

    for ts in inv.compute_tax_subtotals():
        t = _el(st, "ApplicableTradeTax")
        _el(t, "CalculatedAmount", _betrag(ts.tax_amount))
        _el(t, "TypeCode", "VAT")
        _txt(t, "ExemptionReason", ts.exemption_reason)
        _el(t, "BasisAmount", _betrag(ts.taxable_amount))
        _el(t, "CategoryCode", ts.category_code)
        if ts.category_code != "O":
            _el(t, "RateApplicablePercent", _zahl(ts.rate))

    period_start = inv.period_start
    period_end = inv.period_end or inv.tax_point_date
    if period_start or period_end:
        per = _el(st, "BillingSpecifiedPeriod")
        if period_start:
            _datum(per, "StartDateTime", period_start)
        if period_end:
            _datum(per, "EndDateTime", period_end)

    for ac in inv.allowances_charges:
        _nachlass_zuschlag(st, ac, cur, kopf=True)

    if inv.payment.payment_terms or inv.payment.due_date or inv.payment.mandate_reference:
        pt = _el(st, "SpecifiedTradePaymentTerms")
        _txt(pt, "Description", inv.payment.payment_terms)
        if inv.payment.due_date:
            _datum(pt, "DueDateDateTime", inv.payment.due_date)
        _txt(pt, "DirectDebitMandateID", inv.payment.mandate_reference)

    summ = _el(st, "SpecifiedTradeSettlementHeaderMonetarySummation")
    _el(summ, "LineTotalAmount", _betrag(inv.sum_line_net()))
    if inv.sum_charges():
        _el(summ, "ChargeTotalAmount", _betrag(inv.sum_charges()))
    if inv.sum_allowances():
        _el(summ, "AllowanceTotalAmount", _betrag(inv.sum_allowances()))
    _el(summ, "TaxBasisTotalAmount", _betrag(inv.tax_exclusive_amount()))
    _el(summ, "TaxTotalAmount", _betrag(inv.tax_amount()), currencyID=cur)
    _el(summ, "GrandTotalAmount", _betrag(inv.tax_inclusive_amount()))
    vorab = Decimal(str(getattr(inv, "prepaid_amount", 0) or 0))
    if vorab:
        _el(summ, "TotalPrepaidAmount", _betrag(vorab))
    _el(summ, "DuePayableAmount", _betrag(inv.amount_due()))

    if inv.preceding_invoice:
        _el(_el(st, "InvoiceReferencedDocument"), "IssuerAssignedID", inv.preceding_invoice)

    return root


def generate_cii_bytes(inv: Invoice, pretty: bool = True) -> bytes:
    return etree.tostring(generate_cii(inv), xml_declaration=True, encoding="UTF-8",
                          pretty_print=pretty)
