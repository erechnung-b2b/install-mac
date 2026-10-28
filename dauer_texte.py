"""Betreiberangaben fuer Dauerrechnungen (Online-Zugang im Abo).

Die Distribution enthaelt nur neutrale Vorgaben. Eine lokale Erweiterung
(Verzeichnis 'erweiterungen/') setzt die Werte des Betreibers per
DAUER_TEXT.update({...}) — sie werden erst beim Erzeugen gelesen.
"""

DAUER_TEXT = {
    "dienst": "",          # Name des Online-Angebots, z. B. eine Domain
    "kontoinhaber": "",    # Kontoinhaber fuer die Ueberweisung
    "agb_satz": "",        # Satz zu den geltenden AGB auf der Rechnung
    "gruss": "Mit freundlichen Grüßen",  # Grussformel/Signatur der Kundenmails
}


def dienst(praefix: str = "") -> str:
    """' <praefix> <dienst>' bzw. ' <dienst>' — leer, wenn kein Dienst gesetzt ist."""
    d = (DAUER_TEXT.get("dienst") or "").strip()
    if not d:
        return ""
    return f" {praefix} {d}" if praefix else f" {d}"
