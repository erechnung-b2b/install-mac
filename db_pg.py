"""Platzhalter: Die Einzelplatz-/Server-Version arbeitet ohne PostgreSQL.

Alle Daten liegen als Dateien im Ordner data/. Die Funktionen hier tun bewusst
nichts; Aufrufer behandeln die fehlende Datenbankverbindung (None) bereits.
"""


def _connect():
    return None


def is_available():
    return False


def sync_invoices(*args, **kwargs):
    return None


def sync_buyers(*args, **kwargs):
    return None


def sync_bank(*args, **kwargs):
    return None
