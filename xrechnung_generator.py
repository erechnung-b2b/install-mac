"""
E-Rechnungssystem – XRechnung XML-Generator (UBL 2.1)
P-03: Formaterzeugung – Trennung Fachobjekt → Mapping → Serialisierung
"""
from __future__ import annotations
from decimal import Decimal
from datetime import date
from typing import Optional
from lxml import etree
from models import Invoice

NS = {
    "ubl": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}
NSMAP = {None: NS["ubl"], "cbc": NS["cbc"], "cac": NS["cac"]}

def _cbc(t): return f"{{{NS['cbc']}}}{t}"
def _cac(t): return f"{{{NS['cac']}}}{t}"
def _ubl(t): return f"{{{NS['ubl']}}}{t}"

def _amt(parent, tag, val, cur="EUR"):
    el = etree.SubElement(parent, _cbc(tag))
    el.text = str(val.quantize(Decimal("0.01")))
    el.set("currencyID", cur)
    return el

def _dt(parent, tag, d):
    el = etree.SubElement(parent, _cbc(tag)); el.text = d.isoformat(); return el

def _txt(parent, tag, text, ns="cbc"):
    if not text: return None
    el = etree.SubElement(parent, (_cbc if ns == "cbc" else _cac)(tag))
    el.text = text; return el

def _build_address(parent, addr):
    postal = etree.SubElement(parent, _cac("PostalAddress"))
    if addr.street: _txt(postal, "StreetName", addr.street)
    if addr.address_line_2: _txt(postal, "AdditionalStreetName", addr.address_line_2)
    _txt(postal, "CityName", addr.city)
    _txt(postal, "PostalZone", addr.post_code)
    country = etree.SubElement(postal, _cac("Country"))
    _txt(country, "IdentificationCode", addr.country_code)

def _build_seller(root, inv):
    asp = etree.SubElement(root, _cac("AccountingSupplierParty"))
    party = etree.SubElement(asp, _cac("Party"))
    ep = etree.SubElement(party, _cbc("EndpointID"))
    ep.text = inv.seller.electronic_address
    ep.set("schemeID", inv.seller.electronic_address_scheme)
    pn = etree.SubElement(party, _cac("PartyName"))
    _txt(pn, "Name", inv.seller.name)
    _build_address(party, inv.seller.address)
    if inv.seller.vat_id:
        ts_w = etree.SubElement(party, _cac("PartyTaxScheme"))
        _txt(ts_w, "CompanyID", inv.seller.vat_id)
        ts = etree.SubElement(ts_w, _cac("TaxScheme")); _txt(ts, "ID", "VAT")
    if inv.seller.tax_registration_id:
        ts_w2 = etree.SubElement(party, _cac("PartyTaxScheme"))
        _txt(ts_w2, "CompanyID", inv.seller.tax_registration_id)
        ts2 = etree.SubElement(ts_w2, _cac("TaxScheme")); _txt(ts2, "ID", "FC")
    legal = etree.SubElement(party, _cac("PartyLegalEntity"))
    _txt(legal, "RegistrationName", inv.seller.name)
    if inv.seller.company_id: _txt(legal, "CompanyID", inv.seller.company_id)
    contact = etree.SubElement(party, _cac("Contact"))
    _txt(contact, "Name", inv.seller.contact.name)
    _txt(contact, "Telephone", inv.seller.contact.telephone)
    _txt(contact, "ElectronicMail", inv.seller.contact.email)

def _build_buyer(root, inv):
    acp = etree.SubElement(root, _cac("AccountingCustomerParty"))
    party = etree.SubElement(acp, _cac("Party"))
    ep = etree.SubElement(party, _cbc("EndpointID"))
    ep.text = inv.buyer.electronic_address
    ep.set("schemeID", inv.buyer.electronic_address_scheme)
    pn = etree.SubElement(party, _cac("PartyName"))
    _txt(pn, "Name", inv.buyer.name)
    _build_address(party, inv.buyer.address)
    if inv.buyer.vat_id:
        ts_w = etree.SubElement(party, _cac("PartyTaxScheme"))
        _txt(ts_w, "CompanyID", inv.buyer.vat_id)
        ts = etree.SubElement(ts_w, _cac("TaxScheme")); _txt(ts, "ID", "VAT")
    legal = etree.SubElement(party, _cac("PartyLegalEntity"))
    _txt(legal, "RegistrationName", inv.buyer.name)

def _build_payment(root, inv):
    pm = etree.SubElement(root, _cac("PaymentMeans"))
    _txt(pm, "PaymentMeansCode", inv.payment.means_code)
    if inv.payment.iban:
        pf = etree.SubElement(pm, _cac("PayeeFinancialAccount"))
        _txt(pf, "ID", inv.payment.iban)
        if inv.payment.bic:
            br = etree.SubElement(pf, _cac("FinancialInstitutionBranch"))
            _txt(br, "ID", inv.payment.bic)
    if inv.payment.payment_terms:
        pt = etree.SubElement(root, _cac("PaymentTerms"))
        _txt(pt, "Note", inv.payment.payment_terms)

def _build_allowance_charge(parent, ac, cur):
    el = etree.SubElement(parent, _cac("AllowanceCharge"))
    _txt(el, "ChargeIndicator", "true" if ac.is_charge else "false")
    if ac.reason_code: _txt(el, "AllowanceChargeReasonCode", ac.reason_code)
    if ac.reason: _txt(el, "AllowanceChargeReason", ac.reason)
    if ac.percentage: _txt(el, "MultiplierFactorNumeric", str(ac.percentage))
    _amt(el, "Amount", ac.amount, cur)
    if ac.base_amount: _amt(el, "BaseAmount", ac.base_amount, cur)
    tc = etree.SubElement(el, _cac("TaxCategory"))
    _txt(tc, "ID", ac.tax_category); _txt(tc, "Percent", str(ac.tax_rate))
    ts = etree.SubElement(tc, _cac("TaxScheme")); _txt(ts, "ID", "VAT")

def _build_tax_total(root, inv):
    subtotals = inv.compute_tax_subtotals()
    tt = etree.SubElement(root, _cac("TaxTotal"))
    _amt(tt, "TaxAmount", inv.tax_amount(), inv.currency_code)
    for st in subtotals:
        sub = etree.SubElement(tt, _cac("TaxSubtotal"))
        _amt(sub, "TaxableAmount", st.taxable_amount, inv.currency_code)
        _amt(sub, "TaxAmount", st.tax_amount, inv.currency_code)
        tc = etree.SubElement(sub, _cac("TaxCategory"))
        _txt(tc, "ID", st.category_code); _txt(tc, "Percent", str(st.rate))
        if st.exemption_reason: _txt(tc, "TaxExemptionReason", st.exemption_reason)
        ts = etree.SubElement(tc, _cac("TaxScheme")); _txt(ts, "ID", "VAT")

def _build_monetary_total(root, inv):
    lmt = etree.SubElement(root, _cac("LegalMonetaryTotal"))
    cur = inv.currency_code
    _amt(lmt, "LineExtensionAmount", inv.sum_line_net(), cur)
    _amt(lmt, "TaxExclusiveAmount", inv.tax_exclusive_amount(), cur)
    _amt(lmt, "TaxInclusiveAmount", inv.tax_inclusive_amount(), cur)
    if inv.sum_allowances(): _amt(lmt, "AllowanceTotalAmount", inv.sum_allowances(), cur)
    if inv.sum_charges(): _amt(lmt, "ChargeTotalAmount", inv.sum_charges(), cur)
    _amt(lmt, "PrepaidAmount", Decimal("0.00"), cur)
    _amt(lmt, "PayableAmount", inv.amount_due(), cur)

def _build_invoice_line(root, line, cur):
    il = etree.SubElement(root, _cac("InvoiceLine"))
    _txt(il, "ID", line.line_id)
    if line.note: _txt(il, "Note", line.note)
    qty = etree.SubElement(il, _cbc("InvoicedQuantity"))
    qty.text = str(line.quantity); qty.set("unitCode", line.unit_code)
    _amt(il, "LineExtensionAmount", line.line_net_amount, cur)
    if line.order_reference:
        oref = etree.SubElement(il, _cac("OrderLineReference"))
        _txt(oref, "LineID", line.order_reference)
    for ac in line.allowances_charges:
        _build_allowance_charge(il, ac, cur)
    item = etree.SubElement(il, _cac("Item"))
    if line.item_description: _txt(item, "Description", line.item_description)
    _txt(item, "Name", line.item_name)
    if line.item_id:
        sid = etree.SubElement(item, _cac("SellersItemIdentification"))
        _txt(sid, "ID", line.item_id)
    ctc = etree.SubElement(item, _cac("ClassifiedTaxCategory"))
    _txt(ctc, "ID", line.tax_category); _txt(ctc, "Percent", str(line.tax_rate))
    ts = etree.SubElement(ctc, _cac("TaxScheme")); _txt(ts, "ID", "VAT")
    price = etree.SubElement(il, _cac("Price"))
    _amt(price, "PriceAmount", line.unit_price, cur)
    if line.price_base_quantity != Decimal("1"):
        bq = etree.SubElement(price, _cbc("BaseQuantity"))
        bq.text = str(line.price_base_quantity); bq.set("unitCode", line.unit_code)

def generate_xrechnung(inv: Invoice) -> etree._Element:
    root = etree.Element(_ubl("Invoice"), nsmap=NSMAP)
    _txt(root, "CustomizationID", "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0")
    _txt(root, "ProfileID", "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0")
    _txt(root, "ID", inv.invoice_number)
    _dt(root, "IssueDate", inv.invoice_date)
    if inv.payment.due_date: _dt(root, "DueDate", inv.payment.due_date)
    _txt(root, "InvoiceTypeCode", inv.invoice_type_code)
    if inv.note: _txt(root, "Note", inv.note)
    _txt(root, "DocumentCurrencyCode", inv.currency_code)
    _txt(root, "BuyerReference", inv.buyer_reference or inv.buyer.buyer_reference)
    # BT-72 Leistungsdatum: als InvoicePeriod/EndDate in XRechnung abgebildet
    _period_start = inv.period_start
    _period_end = inv.period_end or inv.tax_point_date
    if _period_start or _period_end:
        period = etree.SubElement(root, _cac("InvoicePeriod"))
        if _period_start: _dt(period, "StartDate", _period_start)
        if _period_end: _dt(period, "EndDate", _period_end)
    if inv.order_reference:
        oref = etree.SubElement(root, _cac("OrderReference")); _txt(oref, "ID", inv.order_reference)
    if inv.preceding_invoice:
        br = etree.SubElement(root, _cac("BillingReference"))
        ir = etree.SubElement(br, _cac("InvoiceDocumentReference")); _txt(ir, "ID", inv.preceding_invoice)
    if inv.contract_reference:
        cr = etree.SubElement(root, _cac("ContractDocumentReference")); _txt(cr, "ID", inv.contract_reference)
    if inv.project_reference:
        pr = etree.SubElement(root, _cac("ProjectReference")); _txt(pr, "ID", inv.project_reference)
    _build_seller(root, inv)
    _build_buyer(root, inv)
    # BT-72: Actual Delivery Date
    _delivery_date = inv.tax_point_date or inv.period_end
    if _delivery_date:
        delivery = etree.SubElement(root, _cac("Delivery"))
        _dt(delivery, "ActualDeliveryDate", _delivery_date)
    _build_payment(root, inv)
    for ac in inv.allowances_charges: _build_allowance_charge(root, ac, inv.currency_code)
    _build_tax_total(root, inv)
    _build_monetary_total(root, inv)
    for line in inv.lines: _build_invoice_line(root, line, inv.currency_code)
    return root

def serialize_xml(root: etree._Element, pretty: bool = True) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=pretty)

def generate_and_serialize(inv: Invoice) -> bytes:
    return serialize_xml(generate_xrechnung(inv))
