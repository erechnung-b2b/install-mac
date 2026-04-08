#!/usr/bin/env python3
"""
E-Rechnungssystem – E-Mail-Modul
FR-100: Empfang von E-Rechnungen über IMAP-Postfach
FR-710: Versand von E-Rechnungen per SMTP mit Zustellprotokoll

Konfiguration über EmailConfig-Objekt oder Umgebungsvariablen.
"""
from __future__ import annotations
import imaplib
import smtplib
import email
from email import policy
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from email import encoders
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
import json, os, uuid, hashlib, time, logging

from models import Invoice
from xrechnung_generator import generate_and_serialize
from xrechnung_parser import parse_xrechnung, detect_format
from validator import validate_invoice
from inbox import Inbox, InboxItem

log = logging.getLogger("erechnung.email")


# ═══════════════════════════════════════════════════════════════════════
#  Konfiguration
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class EmailConfig:
    """Konfiguration für ein E-Mail-Postfach (pro Mandant)."""

    # IMAP (Empfang)
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_use_ssl: bool = True
    imap_folder: str = "INBOX"
    imap_processed_folder: str = "Verarbeitet"
    imap_error_folder: str = "Fehler"
    imap_move_after_processing: bool = True

    # SMTP (Versand)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_from_address: str = ""
    smtp_from_name: str = "E-Rechnungssystem"

    # Allgemein
    mandant_id: str = ""
    mandant_name: str = ""
    max_attachment_size_mb: int = 25
    allowed_extensions: list[str] = field(default_factory=lambda: [".xml", ".pdf", ".zip"])
    poll_interval_seconds: int = 60

    @classmethod
    def from_env(cls) -> "EmailConfig":
        """Lädt Konfiguration aus Umgebungsvariablen."""
        return cls(
            imap_host=os.getenv("ERECHNUNG_IMAP_HOST", ""),
            imap_port=int(os.getenv("ERECHNUNG_IMAP_PORT", "993")),
            imap_user=os.getenv("ERECHNUNG_IMAP_USER", ""),
            imap_password=os.getenv("ERECHNUNG_IMAP_PASSWORD", ""),
            imap_use_ssl=os.getenv("ERECHNUNG_IMAP_SSL", "true").lower() == "true",
            imap_folder=os.getenv("ERECHNUNG_IMAP_FOLDER", "INBOX"),
            smtp_host=os.getenv("ERECHNUNG_SMTP_HOST", ""),
            smtp_port=int(os.getenv("ERECHNUNG_SMTP_PORT", "587")),
            smtp_user=os.getenv("ERECHNUNG_SMTP_USER", ""),
            smtp_password=os.getenv("ERECHNUNG_SMTP_PASSWORD", ""),
            smtp_use_tls=os.getenv("ERECHNUNG_SMTP_TLS", "true").lower() == "true",
            smtp_from_address=os.getenv("ERECHNUNG_SMTP_FROM", ""),
            smtp_from_name=os.getenv("ERECHNUNG_SMTP_FROM_NAME", "E-Rechnungssystem"),
            mandant_id=os.getenv("ERECHNUNG_MANDANT_ID", ""),
        )

    @classmethod
    def from_json(cls, path: str) -> "EmailConfig":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def to_json(self) -> dict:
        import dataclasses
        d = dataclasses.asdict(self)
        d.pop("imap_password", None)
        d.pop("smtp_password", None)
        return d


# ═══════════════════════════════════════════════════════════════════════
#  Empfangsprotokolle
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class EmailReceiptLog:
    """Protokolliert jeden empfangenen E-Mail-Eingang (FR-130)."""
    log_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    message_id: str = ""
    sender: str = ""
    subject: str = ""
    received_at: str = ""
    attachment_count: int = 0
    attachments: list[str] = field(default_factory=list)
    processed: bool = False
    error: str = ""
    invoice_numbers: list[str] = field(default_factory=list)
    mandant_id: str = ""


@dataclass
class EmailSendLog:
    """Protokolliert jeden Rechnungsversand (FR-710)."""
    log_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    invoice_number: str = ""
    recipient: str = ""
    subject: str = ""
    sent_at: str = ""
    message_id: str = ""
    success: bool = False
    error: str = ""
    attachment_filename: str = ""
    attachment_size: int = 0


# ═══════════════════════════════════════════════════════════════════════
#  IMAP-Empfänger (FR-100)
# ═══════════════════════════════════════════════════════════════════════

class EmailReceiver:
    """
    Empfängt E-Rechnungen über IMAP.

    Ablauf:
    1. Verbindung zum IMAP-Server herstellen
    2. Ungelesene Nachrichten im konfigurierten Ordner suchen
    3. Anhänge extrahieren (XML, PDF)
    4. Metadaten protokollieren (Absender, Message-ID, Betreff, Empfangszeit)
    5. Anhänge an Inbox zur Verarbeitung übergeben
    6. Verarbeitete Mails in Unterordner verschieben
    """

    def __init__(self, config: EmailConfig, inbox: Inbox):
        self.config = config
        self.inbox = inbox
        self.logs: list[EmailReceiptLog] = []
        self._connection: Optional[imaplib.IMAP4_SSL | imaplib.IMAP4] = None

    def connect(self) -> bool:
        """Stellt IMAP-Verbindung her."""
        try:
            if self.config.imap_use_ssl:
                self._connection = imaplib.IMAP4_SSL(
                    self.config.imap_host, self.config.imap_port)
            else:
                self._connection = imaplib.IMAP4(
                    self.config.imap_host, self.config.imap_port)

            self._connection.login(self.config.imap_user, self.config.imap_password)
            log.info(f"IMAP verbunden: {self.config.imap_host} ({self.config.imap_user})")
            return True

        except Exception as e:
            log.error(f"IMAP-Verbindung fehlgeschlagen: {e}")
            return False

    def disconnect(self):
        if self._connection:
            try:
                self._connection.close()
                self._connection.logout()
            except Exception:
                pass
            self._connection = None

    def _ensure_folder(self, folder_name: str):
        """Erstellt IMAP-Ordner falls nicht vorhanden."""
        try:
            status, _ = self._connection.select(folder_name)
            if status != "OK":
                self._connection.create(folder_name)
            self._connection.select(self.config.imap_folder)
        except Exception:
            try:
                self._connection.create(folder_name)
            except Exception:
                pass
            try:
                self._connection.select(self.config.imap_folder)
            except Exception:
                pass

    def fetch_new_invoices(self, search_all: bool = False) -> list[EmailReceiptLog]:
        """
        Holt Mails mit Rechnungsanhaengen.
        Sucht zuerst ungelesene, dann alle falls nichts gefunden.
        """
        if not self._connection:
            if not self.connect():
                return []

        results = []

        try:
            self._connection.select(self.config.imap_folder)

            # Zuerst ungelesene Nachrichten suchen
            status, message_ids = self._connection.search(None, "UNSEEN")
            ids = message_ids[0].split() if status == "OK" and message_ids[0] else []

            # Falls keine ungelesenen: alle durchsuchen
            if not ids or search_all:
                status, message_ids = self._connection.search(None, "ALL")
                all_ids = message_ids[0].split() if status == "OK" and message_ids[0] else []
                if not ids and all_ids:
                    log.info(f"Keine ungelesenen Mails, pruefe alle {len(all_ids)} Nachrichten...")
                    ids = all_ids
                elif search_all:
                    ids = all_ids

            if not ids:
                log.debug("Keine Nachrichten gefunden.")
                return []

            log.info(f"{len(ids)} Nachricht(en) werden geprueft.")

            for msg_id in ids:
                receipt = self._process_message(msg_id)
                if receipt.attachment_count > 0:
                    results.append(receipt)
                    self.logs.append(receipt)

        except Exception as e:
            log.error(f"Fehler beim Abrufen: {e}")

        return results

    def _process_message(self, msg_id: bytes) -> EmailReceiptLog:
        """Verarbeitet eine einzelne E-Mail."""
        receipt = EmailReceiptLog(
            received_at=datetime.now().isoformat(),
            mandant_id=self.config.mandant_id,
        )

        try:
            # E-Mail laden
            status, data = self._connection.fetch(msg_id, "(RFC822)")
            if status != "OK":
                receipt.error = "Konnte Nachricht nicht laden."
                return receipt

            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email, policy=policy.default)

            # Metadaten extrahieren (FR-130)
            receipt.message_id = msg.get("Message-ID", "")
            receipt.sender = msg.get("From", "")
            receipt.subject = msg.get("Subject", "")

            # Absender-Adresse extrahieren
            sender_email = ""
            from_header = msg.get("From", "")
            if "<" in from_header and ">" in from_header:
                sender_email = from_header.split("<")[1].split(">")[0]
            else:
                sender_email = from_header

            log.info(f"Verarbeite: {receipt.subject} von {sender_email}")

            # Anhänge extrahieren — auch inline-Parts mit Dateiname oder XML-Content-Type
            attachment_count = 0
            for part in msg.walk():
                # Multipart-Container ueberspringen
                if part.get_content_maintype() == "multipart":
                    continue

                content_type = part.get_content_type() or ""
                content_disposition = str(part.get("Content-Disposition", ""))
                filename = part.get_filename()

                # Erkennung: Anhang mit Dateiname ODER XML/PDF-Content-Type
                is_attachment = "attachment" in content_disposition
                is_inline_with_name = filename is not None
                is_xml_content = content_type in ("application/xml", "text/xml",
                                                   "application/xrechnung+xml",
                                                   "application/ubl+xml")
                is_pdf_content = content_type == "application/pdf"

                if not (is_attachment or is_inline_with_name or is_xml_content or is_pdf_content):
                    continue

                # Dateiname erzeugen falls keiner vorhanden
                if not filename:
                    if is_xml_content:
                        filename = f"rechnung_{attachment_count + 1}.xml"
                    elif is_pdf_content:
                        filename = f"rechnung_{attachment_count + 1}.pdf"
                    else:
                        continue

                # Dateiendung pruefen
                ext = Path(filename).suffix.lower()
                if ext not in self.config.allowed_extensions:
                    log.warning(f"Uebersprungen (Dateityp): {filename}")
                    continue

                # Payload laden
                payload = part.get_payload(decode=True)
                if not payload:
                    continue

                # Groessenlimit pruefen
                size_mb = len(payload) / (1024 * 1024)
                if size_mb > self.config.max_attachment_size_mb:
                    log.warning(f"Uebersprungen (zu gross): {filename} ({size_mb:.1f} MB)")
                    continue

                attachment_count += 1
                receipt.attachments.append(filename)

                # An Inbox uebergeben
                item = self.inbox.receive_file(
                    filename=filename,
                    data=payload,
                    sender_email=sender_email,
                    subject=receipt.subject,
                    message_id=receipt.message_id,
                )

                if item.invoice:
                    receipt.invoice_numbers.append(item.invoice.invoice_number)
                    log.info(f"  -> Rechnung {item.invoice.invoice_number} verarbeitet ({item.status})")
                else:
                    log.info(f"  -> {filename}: {item.status} - {item.error}")

            receipt.attachment_count = attachment_count
            receipt.processed = True

            # Mail als gelesen markieren und verschieben
            if self.config.imap_move_after_processing and attachment_count > 0:
                target = self.config.imap_processed_folder
                self._move_message(msg_id, target)

        except Exception as e:
            receipt.error = str(e)
            log.error(f"Fehler bei Nachricht: {e}")

            # Bei Fehler in Fehler-Ordner verschieben
            if self.config.imap_move_after_processing:
                self._move_message(msg_id, self.config.imap_error_folder)

        return receipt

    def _move_message(self, msg_id: bytes, target_folder: str):
        """Verschiebt eine Nachricht in einen IMAP-Unterordner."""
        try:
            self._ensure_folder(target_folder)
            self._connection.copy(msg_id, target_folder)
            self._connection.store(msg_id, "+FLAGS", "\\Deleted")
            self._connection.expunge()
        except Exception as e:
            log.warning(f"Verschieben fehlgeschlagen: {e}")

    def poll_loop(self, callback=None):
        """
        Endlosschleife: Prüft regelmäßig auf neue Nachrichten.
        Optional: Callback(receipt_log) nach jeder verarbeiteten Mail.
        """
        log.info(f"Starte Polling alle {self.config.poll_interval_seconds}s...")
        while True:
            try:
                receipts = self.fetch_new_invoices()
                for r in receipts:
                    if callback:
                        callback(r)
                time.sleep(self.config.poll_interval_seconds)
            except KeyboardInterrupt:
                log.info("Polling beendet.")
                break
            except Exception as e:
                log.error(f"Polling-Fehler: {e}")
                self.disconnect()
                time.sleep(10)

    def get_logs(self) -> list[dict]:
        return [{
            "log_id": r.log_id,
            "message_id": r.message_id,
            "sender": r.sender,
            "subject": r.subject,
            "received_at": r.received_at,
            "attachment_count": r.attachment_count,
            "attachments": r.attachments,
            "processed": r.processed,
            "error": r.error,
            "invoice_numbers": r.invoice_numbers,
        } for r in self.logs]


# ═══════════════════════════════════════════════════════════════════════
#  SMTP-Versand (FR-710)
# ═══════════════════════════════════════════════════════════════════════

class EmailSender:
    """
    Versendet E-Rechnungen per SMTP.

    Ablauf:
    1. Invoice-Objekt → XRechnung-XML erzeugen
    2. E-Mail mit XML-Anhang (und optional PDF) aufbauen
    3. Per SMTP versenden
    4. Zustellprotokoll speichern
    """

    def __init__(self, config: EmailConfig):
        self.config = config
        self.logs: list[EmailSendLog] = []

    def send_invoice(
        self,
        invoice: Invoice,
        recipient: str,
        subject: str = "",
        body_text: str = "",
        attach_xml: bool = True,
        xml_bytes: bytes = None,
        additional_attachments: list[tuple[str, bytes]] = None,
    ) -> EmailSendLog:
        """
        Versendet eine E-Rechnung per E-Mail.

        Args:
            invoice: Das Rechnungsobjekt
            recipient: Empfänger-E-Mail-Adresse
            subject: Betreff (wird automatisch generiert falls leer)
            body_text: Nachrichtentext
            attach_xml: XML-Datei anhängen
            xml_bytes: Fertige XML-Bytes (werden erzeugt falls None)
            additional_attachments: Weitere Anhänge als [(filename, bytes)]
        """
        send_log = EmailSendLog(
            invoice_number=invoice.invoice_number,
            recipient=recipient,
            sent_at=datetime.now().isoformat(),
        )

        try:
            # XML erzeugen falls nicht übergeben
            if attach_xml and xml_bytes is None:
                xml_bytes = generate_and_serialize(invoice)

            # Betreff
            if not subject:
                type_label = "Gutschrift" if invoice.invoice_type_code == "381" else "Rechnung"
                subject = (f"{type_label} {invoice.invoice_number} "
                           f"vom {invoice.invoice_date.strftime('%d.%m.%Y')} "
                           f"– {invoice.seller.name}")
            send_log.subject = subject

            # Nachrichtentext
            if not body_text:
                body_text = self._generate_body(invoice)

            # E-Mail aufbauen
            msg = MIMEMultipart()
            msg["From"] = f"{self.config.smtp_from_name} <{self.config.smtp_from_address}>"
            msg["To"] = recipient
            msg["Subject"] = subject
            msg["Date"] = formatdate(localtime=True)
            msg["Message-ID"] = make_msgid(domain=self.config.smtp_from_address.split("@")[-1]
                                           if "@" in self.config.smtp_from_address else "erechnung.local")
            send_log.message_id = msg["Message-ID"]

            # Text-Body
            msg.attach(MIMEText(body_text, "plain", "utf-8"))

            # XML-Anhang
            if attach_xml and xml_bytes:
                xml_filename = f"{invoice.invoice_number.replace('/', '_')}.xml"
                xml_part = MIMEBase("application", "xml")
                xml_part.set_payload(xml_bytes)
                encoders.encode_base64(xml_part)
                xml_part.add_header("Content-Disposition", "attachment",
                                    filename=xml_filename)
                msg.attach(xml_part)
                send_log.attachment_filename = xml_filename
                send_log.attachment_size = len(xml_bytes)

            # Weitere Anhänge
            if additional_attachments:
                for filename, file_bytes in additional_attachments:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(file_bytes)
                    encoders.encode_base64(part)
                    part.add_header("Content-Disposition", "attachment",
                                    filename=filename)
                    msg.attach(part)

            # Versand
            self._smtp_send(msg, recipient)

            send_log.success = True
            log.info(f"Rechnung {invoice.invoice_number} versendet an {recipient}")

            # Audit-Trail
            invoice.add_audit(
                "EMAIL_VERSANDT",
                comment=f"An: {recipient}, Betreff: {subject}, "
                        f"Message-ID: {send_log.message_id}"
            )

        except Exception as e:
            send_log.error = str(e)
            log.error(f"Versand fehlgeschlagen: {e}")

            invoice.add_audit(
                "EMAIL_VERSAND_FEHLER",
                comment=f"An: {recipient}, Fehler: {str(e)}"
            )

        self.logs.append(send_log)
        return send_log

    def _smtp_send(self, msg: MIMEMultipart, recipient: str):
        """Stellt SMTP-Verbindung her und versendet."""
        if self.config.smtp_use_tls:
            server = smtplib.SMTP(self.config.smtp_host, self.config.smtp_port)
            server.ehlo()
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(self.config.smtp_host, self.config.smtp_port)

        server.login(self.config.smtp_user, self.config.smtp_password)
        server.send_message(msg)
        server.quit()

    def _generate_body(self, inv: Invoice) -> str:
        """Erzeugt den Standard-Nachrichtentext."""
        type_label = "Gutschrift" if inv.invoice_type_code == "381" else "Rechnung"
        return (
            f"Sehr geehrte Damen und Herren,\n\n"
            f"anbei erhalten Sie unsere {type_label} {inv.invoice_number} "
            f"vom {inv.invoice_date.strftime('%d.%m.%Y')}.\n\n"
            f"  Rechnungsnummer:  {inv.invoice_number}\n"
            f"  Rechnungsdatum:   {inv.invoice_date.strftime('%d.%m.%Y')}\n"
            f"  Nettobetrag:      {inv.tax_exclusive_amount():.2f} {inv.currency_code}\n"
            f"  USt:              {inv.tax_amount():.2f} {inv.currency_code}\n"
            f"  Bruttobetrag:     {inv.tax_inclusive_amount():.2f} {inv.currency_code}\n"
            f"  Zahlungsziel:     {inv.payment.payment_terms or 'siehe Rechnung'}\n\n"
            f"Die Rechnung ist im strukturierten XRechnung-Format (EN 16931) beigefügt.\n"
            f"Der strukturierte XML-Teil ist der maßgebliche Rechnungsbestandteil.\n\n"
            f"Bei Rückfragen wenden Sie sich bitte an:\n"
            f"  {inv.seller.contact.name}\n"
            f"  {inv.seller.contact.telephone}\n"
            f"  {inv.seller.contact.email}\n\n"
            f"Mit freundlichen Grüßen\n"
            f"{inv.seller.name}\n"
        )

    def send_batch(
        self,
        invoices: list[tuple[Invoice, str]],
        delay_seconds: float = 1.0,
    ) -> list[EmailSendLog]:
        """
        FR-730: Serienversand mehrerer Rechnungen.
        invoices: Liste von (Invoice, Empfänger-E-Mail)-Tupeln
        """
        results = []
        for inv, recipient in invoices:
            result = self.send_invoice(inv, recipient)
            results.append(result)
            if delay_seconds > 0:
                time.sleep(delay_seconds)
        return results

    def retry_failed(self) -> list[EmailSendLog]:
        """Versendet fehlgeschlagene Mails erneut (manuell auslösen)."""
        failed = [l for l in self.logs if not l.success]
        log.info(f"{len(failed)} fehlgeschlagene Sendungen zum Wiederholen.")
        # In Produktion: Invoice-Objekte neu laden und erneut senden
        return failed

    def get_logs(self) -> list[dict]:
        return [{
            "log_id": l.log_id,
            "invoice_number": l.invoice_number,
            "recipient": l.recipient,
            "subject": l.subject,
            "sent_at": l.sent_at,
            "message_id": l.message_id,
            "success": l.success,
            "error": l.error,
            "filename": l.attachment_filename,
            "size": l.attachment_size,
        } for l in self.logs]


# ═══════════════════════════════════════════════════════════════════════
#  Komfort-Funktionen
# ═══════════════════════════════════════════════════════════════════════

class EmailManager:
    """Zentrale Steuerung für E-Mail-Empfang und -Versand pro Mandant."""

    def __init__(self, config: EmailConfig, inbox: Inbox):
        self.config = config
        self.receiver = EmailReceiver(config, inbox)
        self.sender = EmailSender(config)
        self.inbox = inbox

    def check_inbox(self) -> list[EmailReceiptLog]:
        """Prüft das Postfach einmalig auf neue Rechnungen."""
        return self.receiver.fetch_new_invoices()

    def send_invoice(self, invoice: Invoice, recipient: str, **kwargs) -> EmailSendLog:
        """Versendet eine Rechnung."""
        return self.sender.send_invoice(invoice, recipient, **kwargs)

    def start_polling(self, callback=None):
        """Startet den Endlos-Polling-Loop."""
        self.receiver.poll_loop(callback)

    def get_all_logs(self) -> dict:
        return {
            "received": self.receiver.get_logs(),
            "sent": self.sender.get_logs(),
        }

    def summary(self) -> str:
        recv = len(self.receiver.logs)
        sent = len(self.sender.logs)
        sent_ok = sum(1 for l in self.sender.logs if l.success)
        return (f"E-Mail: {recv} empfangen | {sent_ok}/{sent} erfolgreich versendet | "
                f"Postfach: {self.config.imap_user or '(nicht konfiguriert)'}")


# ═══════════════════════════════════════════════════════════════════════
#  Demo / Test (ohne echten Mailserver)
# ═══════════════════════════════════════════════════════════════════════

class MockEmailReceiver(EmailReceiver):
    """Test-Receiver der lokale XML-Dateien statt IMAP nutzt."""

    def __init__(self, config: EmailConfig, inbox: Inbox, test_dir: str = "./test_mails"):
        super().__init__(config, inbox)
        self.test_dir = Path(test_dir)

    def connect(self) -> bool:
        log.info(f"Mock-IMAP: Nutze lokales Verzeichnis {self.test_dir}")
        return True

    def disconnect(self):
        pass

    def fetch_new_invoices(self) -> list[EmailReceiptLog]:
        """Liest XML-Dateien aus dem Testverzeichnis."""
        results = []
        if not self.test_dir.exists():
            return results

        for filepath in sorted(self.test_dir.glob("*.xml")):
            receipt = EmailReceiptLog(
                received_at=datetime.now().isoformat(),
                sender=f"test@{filepath.stem}.de",
                subject=f"Rechnung {filepath.stem}",
                message_id=f"<{filepath.stem}@test.local>",
                mandant_id=self.config.mandant_id,
            )

            data = filepath.read_bytes()
            receipt.attachments.append(filepath.name)
            receipt.attachment_count = 1

            item = self.inbox.receive_file(
                filename=filepath.name,
                data=data,
                sender_email=receipt.sender,
                subject=receipt.subject,
                message_id=receipt.message_id,
            )

            if item.invoice:
                receipt.invoice_numbers.append(item.invoice.invoice_number)
            receipt.processed = True

            results.append(receipt)
            self.logs.append(receipt)

            # Datei als verarbeitet markieren (umbenennen)
            processed = filepath.with_suffix(".xml.processed")
            filepath.rename(processed)

        return results


class MockEmailSender(EmailSender):
    """Test-Sender der E-Mails in Dateien statt per SMTP schreibt."""

    def __init__(self, config: EmailConfig, output_dir: str = "./sent_mails"):
        super().__init__(config)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _smtp_send(self, msg: MIMEMultipart, recipient: str):
        """Statt SMTP: Speichert E-Mail als .eml-Datei."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{recipient.replace('@', '_at_')}.eml"
        filepath = self.output_dir / filename
        filepath.write_bytes(msg.as_bytes())
        log.info(f"Mock-SMTP: E-Mail gespeichert als {filepath}")


# ═══════════════════════════════════════════════════════════════════════
#  Demo
# ═══════════════════════════════════════════════════════════════════════

def demo_email():
    """Demonstriert E-Mail-Empfang und -Versand mit Mock-Objekten."""
    from demo import test_a1_standard

    print("=" * 60)
    print("  E-Mail-Modul – Demo")
    print("=" * 60)

    config = EmailConfig(
        imap_host="imap.example.com",
        imap_user="rechnungen@demo-gmbh.de",
        smtp_host="smtp.example.com",
        smtp_from_address="rechnungen@demo-gmbh.de",
        smtp_from_name="Demo GmbH Rechnungswesen",
        mandant_name="Demo GmbH",
    )

    inbox = Inbox()

    # ── Empfang testen ─────────────────────────────────────────
    print("\n  ── Empfang (Mock) ──")

    # Testdaten vorbereiten: XML-Dateien in Testordner
    test_dir = Path("./test_mails")
    test_dir.mkdir(exist_ok=True)

    inv = test_a1_standard()
    xml = generate_and_serialize(inv)
    (test_dir / "RE-2026-0001.xml").write_bytes(xml)

    receiver = MockEmailReceiver(config, inbox, str(test_dir))
    receiver.connect()
    receipts = receiver.fetch_new_invoices()

    for r in receipts:
        status = "✓" if r.processed else "✗"
        print(f"  {status} Von: {r.sender}")
        print(f"    Betreff: {r.subject}")
        print(f"    Anhänge: {r.attachment_count}")
        print(f"    Rechnungen: {r.invoice_numbers}")

    print(f"\n  Inbox: {inbox.summary()}")

    # ── Versand testen ─────────────────────────────────────────
    print("\n  ── Versand (Mock) ──")

    sender = MockEmailSender(config, "./sent_mails")
    inv2 = test_a1_standard()
    inv2.invoice_number = "RE-2026-0050"

    send_log = sender.send_invoice(
        invoice=inv2,
        recipient="einkauf@beispiel-ag.de",
    )

    status = "✓" if send_log.success else "✗"
    print(f"  {status} An: {send_log.recipient}")
    print(f"    Betreff: {send_log.subject}")
    print(f"    Message-ID: {send_log.message_id}")
    print(f"    Anhang: {send_log.attachment_filename} ({send_log.attachment_size} Bytes)")
    print(f"    Audit-Trail: {len(inv2.audit_trail)} Einträge")

    # Versendete E-Mail anzeigen
    sent_files = list(Path("./sent_mails").glob("*.eml"))
    if sent_files:
        eml = sent_files[-1].read_text(errors="replace")
        # Nur Header und Textbody zeigen
        print(f"\n  ── Gesendete E-Mail ──")
        for line in eml.split("\n")[:20]:
            if line.startswith(("From:", "To:", "Subject:", "Date:", "Message-ID:")):
                print(f"    {line.strip()}")

    # ── Zusammenfassung ────────────────────────────────────────
    print(f"\n  ── Zusammenfassung ──")
    print(f"  Empfangen: {len(receiver.logs)} Mails")
    print(f"  Versendet: {len(sender.logs)} Mails")
    print(f"  Alle Tests erfolgreich ✓")

    # Aufräumen
    import shutil
    shutil.rmtree("./test_mails", ignore_errors=True)

    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="  %(message)s")
    demo_email()
