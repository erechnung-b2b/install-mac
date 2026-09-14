"""
PDF/A-3b + ZUGFeRD/Factur-X – macht aus einem reportlab-PDF eine normgerechte
hybride E-Rechnung.

Was PDF/A-3b und ZUGFeRD 2.x verlangen und hier umgesetzt ist:
  * alle Schriften eingebettet          -> schriften_eingebettet() (Liberation Sans,
                                           metrisch identisch zu Helvetica)
  * OutputIntent mit ICC-Farbprofil      -> sRGB
  * XMP-Metadaten mit pdfaid (Teil 3, B), synchron zum Info-Dictionary
  * Factur-X-XMP-Erweiterung (fx:DocumentType/FileName/Version/ConformanceLevel)
    samt pdfaExtension-Schemabeschreibung
  * XML als Associated File (/AF) mit AFRelationship /Alternative, MIME text/xml
  * Datei-ID im Trailer, PDF-Version 1.7

Geprueft mit dem Mustang-Validator (enthaelt veraPDF) und KoSIT.
"""
from __future__ import annotations

import io
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import pikepdf

_BASIS = Path(__file__).resolve().parent
_FONT_DIR = _BASIS / "resources" / "fonts"
_ICC_DATEI = _BASIS / "resources" / "sRGB.icc"

# reportlab-Standardnamen -> eingebettete TrueType-Datei (metrisch gleich)
_SCHRIFTEN = {
    "Helvetica": "LiberationSans-Regular.ttf",
    "Helvetica-Bold": "LiberationSans-Bold.ttf",
    "Helvetica-Oblique": "LiberationSans-Italic.ttf",
    "Helvetica-BoldOblique": "LiberationSans-BoldItalic.ttf",
}

FACTURX_NS = "urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#"


@contextmanager
def schriften_eingebettet():
    """Ersetzt waehrend des PDF-Aufbaus die nicht einbettbaren Standardschriften
    durch eingebettete TrueType-Schriften (PDF/A verlangt eingebettete Schriften).
    Danach wird der vorherige Zustand wiederhergestellt, damit andere Dokumente
    (Angebote, Mahnungen) unveraendert bleiben."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib import fonts as rl_fonts

    vorher_fonts = {n: pdfmetrics._fonts.get(n) for n in _SCHRIFTEN}
    vorher_family = dict(rl_fonts._tt2ps_map)
    vorher_ps = dict(rl_fonts._ps2tt_map)
    try:
        for name, datei in _SCHRIFTEN.items():
            pfad = _FONT_DIR / datei
            if not pfad.exists():
                raise FileNotFoundError(f"Schrift fehlt: {pfad}")
            # registerFont ersetzt einen schon geladenen Eintrag NICHT (reportlab
            # prueft "fontName not in _fonts") — ohne pop bliebe Helvetica als
            # nicht eingebettete Type1-Schrift, sobald vorher ein anderes PDF lief.
            pdfmetrics._fonts.pop(name, None)
            pdfmetrics.registerFont(TTFont(name, str(pfad)))
        pdfmetrics.registerFontFamily("Helvetica", normal="Helvetica", bold="Helvetica-Bold",
                                      italic="Helvetica-Oblique", boldItalic="Helvetica-BoldOblique")
        yield
    finally:
        for n, f in vorher_fonts.items():
            if f is None:
                pdfmetrics._fonts.pop(n, None)
            else:
                pdfmetrics._fonts[n] = f
        rl_fonts._tt2ps_map.clear(); rl_fonts._tt2ps_map.update(vorher_family)
        rl_fonts._ps2tt_map.clear(); rl_fonts._ps2tt_map.update(vorher_ps)


def _icc_profil() -> bytes:
    if _ICC_DATEI.exists():
        return _ICC_DATEI.read_bytes()
    from PIL import ImageCms   # Rueckfall: sRGB aus Little CMS erzeugen
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _pdf_datum(dt: datetime) -> str:
    off = dt.strftime("%z") or "+0000"
    return dt.strftime("D:%Y%m%d%H%M%S") + f"{off[:3]}'{off[3:]}'"


def _xmp(titel: str, autor: str, producer: str, zeit: datetime, dateiname: str, profil: str) -> bytes:
    iso = zeit.isoformat(timespec="seconds")
    t, a, p = escape(titel), escape(autor), escape(producer)
    return f"""<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/">
   <pdfaid:part>3</pdfaid:part>
   <pdfaid:conformance>B</pdfaid:conformance>
  </rdf:Description>
  <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">
   <dc:format>application/pdf</dc:format>
   <dc:title><rdf:Alt><rdf:li xml:lang="x-default">{t}</rdf:li></rdf:Alt></dc:title>
   <dc:creator><rdf:Seq><rdf:li>{a}</rdf:li></rdf:Seq></dc:creator>
  </rdf:Description>
  <rdf:Description rdf:about="" xmlns:pdf="http://ns.adobe.com/pdf/1.3/">
   <pdf:Producer>{p}</pdf:Producer>
  </rdf:Description>
  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/">
   <xmp:CreatorTool>{p}</xmp:CreatorTool>
   <xmp:CreateDate>{iso}</xmp:CreateDate>
   <xmp:ModifyDate>{iso}</xmp:ModifyDate>
  </rdf:Description>
  <rdf:Description rdf:about="" xmlns:fx="{FACTURX_NS}">
   <fx:DocumentType>INVOICE</fx:DocumentType>
   <fx:DocumentFileName>{escape(dateiname)}</fx:DocumentFileName>
   <fx:Version>1.0</fx:Version>
   <fx:ConformanceLevel>{escape(profil)}</fx:ConformanceLevel>
  </rdf:Description>
  <rdf:Description rdf:about=""
    xmlns:pdfaExtension="http://www.aiim.org/pdfa/ns/extension/"
    xmlns:pdfaSchema="http://www.aiim.org/pdfa/ns/schema#"
    xmlns:pdfaProperty="http://www.aiim.org/pdfa/ns/property#">
   <pdfaExtension:schemas>
    <rdf:Bag>
     <rdf:li rdf:parseType="Resource">
      <pdfaSchema:schema>Factur-X PDFA Extension Schema</pdfaSchema:schema>
      <pdfaSchema:namespaceURI>{FACTURX_NS}</pdfaSchema:namespaceURI>
      <pdfaSchema:prefix>fx</pdfaSchema:prefix>
      <pdfaSchema:property>
       <rdf:Seq>
        <rdf:li rdf:parseType="Resource">
         <pdfaProperty:name>DocumentFileName</pdfaProperty:name>
         <pdfaProperty:valueType>Text</pdfaProperty:valueType>
         <pdfaProperty:category>external</pdfaProperty:category>
         <pdfaProperty:description>name of the embedded XML invoice file</pdfaProperty:description>
        </rdf:li>
        <rdf:li rdf:parseType="Resource">
         <pdfaProperty:name>DocumentType</pdfaProperty:name>
         <pdfaProperty:valueType>Text</pdfaProperty:valueType>
         <pdfaProperty:category>external</pdfaProperty:category>
         <pdfaProperty:description>INVOICE</pdfaProperty:description>
        </rdf:li>
        <rdf:li rdf:parseType="Resource">
         <pdfaProperty:name>Version</pdfaProperty:name>
         <pdfaProperty:valueType>Text</pdfaProperty:valueType>
         <pdfaProperty:category>external</pdfaProperty:category>
         <pdfaProperty:description>The actual version of the Factur-X XML schema</pdfaProperty:description>
        </rdf:li>
        <rdf:li rdf:parseType="Resource">
         <pdfaProperty:name>ConformanceLevel</pdfaProperty:name>
         <pdfaProperty:valueType>Text</pdfaProperty:valueType>
         <pdfaProperty:category>external</pdfaProperty:category>
         <pdfaProperty:description>The conformance level of the embedded Factur-X data</pdfaProperty:description>
        </rdf:li>
       </rdf:Seq>
      </pdfaSchema:property>
     </rdf:li>
    </rdf:Bag>
   </pdfaExtension:schemas>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>""".encode("utf-8")


def zu_pdfa3_zugferd(pdf_bytes: bytes, xml_bytes: bytes, *, dateiname: str = "factur-x.xml",
                    profil: str = "XRECHNUNG", titel: str = "Rechnung",
                    autor: str = "", producer: str = "E-Rechnungssystem") -> bytes:
    zeit = datetime.now(timezone.utc).astimezone().replace(microsecond=0)
    pdf_zeit = _pdf_datum(zeit)

    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
        # 1) Info-Dictionary (muss mit XMP uebereinstimmen)
        info = pikepdf.Dictionary(
            Title=pikepdf.String(titel), Author=pikepdf.String(autor or producer),
            Creator=pikepdf.String(producer), Producer=pikepdf.String(producer),
            CreationDate=pikepdf.String(pdf_zeit), ModDate=pikepdf.String(pdf_zeit))
        pdf.trailer.Info = pdf.make_indirect(info)

        # 2) OutputIntent sRGB
        icc = pikepdf.Stream(pdf, _icc_profil())
        icc.N = 3
        intent = pikepdf.Dictionary(
            Type=pikepdf.Name.OutputIntent, S=pikepdf.Name("/GTS_PDFA1"),
            OutputConditionIdentifier=pikepdf.String("sRGB IEC61966-2.1"),
            Info=pikepdf.String("sRGB IEC61966-2.1"),
            DestOutputProfile=pdf.make_indirect(icc))
        pdf.Root.OutputIntents = pikepdf.Array([pdf.make_indirect(intent)])

        # 3) XMP-Metadaten (unkomprimiert)
        meta = pikepdf.Stream(pdf, _xmp(titel, autor or producer, producer, zeit, dateiname, profil))
        meta.Type = pikepdf.Name.Metadata
        meta.Subtype = pikepdf.Name.XML
        pdf.Root.Metadata = pdf.make_indirect(meta)

        # 4) XML als Associated File
        ef = pikepdf.Stream(pdf, xml_bytes)
        ef.Type = pikepdf.Name.EmbeddedFile
        ef.Subtype = pikepdf.Name("/text/xml")   # pikepdf schreibt es als /text#2Fxml
        ef.Params = pikepdf.Dictionary(Size=len(xml_bytes), ModDate=pikepdf.String(pdf_zeit))
        ef_ref = pdf.make_indirect(ef)
        spec = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Filespec, F=pikepdf.String(dateiname), UF=pikepdf.String(dateiname),
            Desc=pikepdf.String("Factur-X/ZUGFeRD Rechnungsdaten"),
            AFRelationship=pikepdf.Name.Alternative,
            EF=pikepdf.Dictionary(F=ef_ref, UF=ef_ref)))
        names = pdf.Root.get("/Names") or pikepdf.Dictionary()
        names.EmbeddedFiles = pikepdf.Dictionary(Names=pikepdf.Array([pikepdf.String(dateiname), spec]))
        pdf.Root.Names = names
        pdf.Root.AF = pikepdf.Array([spec])

        # 5) Anmerkungen muessen druckbar sein (PDF/A), keine Transparenzgruppe erzwingen
        for page in pdf.pages:
            for annot in page.get("/Annots", []) or []:
                annot.F = 4

        out = io.BytesIO()
        pdf.save(out, min_version="1.7", fix_metadata_version=False, deterministic_id=False)
        return out.getvalue()
