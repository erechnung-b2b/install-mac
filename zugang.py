"""Zugangssteuerung der Kundenversion: Einzelplatz (lokal) oder Server.

Einzelplatz (Standard, `starten.bat` / `./starten.sh`):
    Server lauscht nur auf 127.0.0.1. Solange keine data/auth.json existiert,
    ist keine Anmeldung noetig — nur dieser Rechner kommt an die Anwendung.

Server (`python run.py --server` oder ERECHNUNG_SERVER=1):
    Anmeldung ist Pflicht. Gibt es noch keinen Benutzer, fuehrt jeder Aufruf auf
    /einrichten. Dort wird das erste Konto mit dem Einrichtungscode angelegt, den
    das Programm beim Start ausgibt und in data/einrichtungscode.txt ablegt.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import time
from pathlib import Path

SERVERMODUS = os.environ.get("ERECHNUNG_SERVER", "").strip() in ("1", "true", "ja", "yes")

_FEHLVERSUCHE: dict[str, list[float]] = {}
_SPERRE_VERSUCHE = 5
_SPERRE_SEKUNDEN = 15 * 60


def code_datei(data_dir: Path) -> Path:
    return Path(data_dir) / "einrichtungscode.txt"


def einrichtungscode(data_dir: Path) -> str:
    """Liefert den Einrichtungscode; legt ihn beim ersten Aufruf an."""
    p = code_datei(data_dir)
    if p.exists():
        code = p.read_text(encoding="utf-8").strip()
        if code:
            return code
    roh = secrets.token_hex(6).upper()
    code = "-".join(roh[i:i + 4] for i in range(0, 12, 4))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(code + "\n", encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return code


def gesperrt(adresse: str) -> bool:
    jetzt = time.time()
    liste = [t for t in _FEHLVERSUCHE.get(adresse, []) if jetzt - t < _SPERRE_SEKUNDEN]
    _FEHLVERSUCHE[adresse] = liste
    return len(liste) >= _SPERRE_VERSUCHE


def fehlversuch(adresse: str) -> None:
    _FEHLVERSUCHE.setdefault(adresse, []).append(time.time())


def pruefe_und_lege_an(data_dir: Path, auth_file: Path, code: str, benutzer: str,
                       passwort: str, wiederholung: str, hash_fn) -> str | None:
    """Legt den ersten Benutzer an. Rueckgabe: Fehlermeldung oder None bei Erfolg."""
    if auth_file.exists():
        return "Die Einrichtung ist bereits abgeschlossen."
    soll = einrichtungscode(data_dir)
    if not hmac.compare_digest(code.strip().upper(), soll.upper()):
        return "Der Einrichtungscode stimmt nicht."
    benutzer = benutzer.strip()
    if len(benutzer) < 3:
        return "Der Benutzername muss mindestens 3 Zeichen haben."
    if len(passwort) < 8:
        return "Das Passwort muss mindestens 8 Zeichen haben."
    if passwort != wiederholung:
        return "Die beiden Passwörter stimmen nicht überein."
    auth_file.parent.mkdir(parents=True, exist_ok=True)
    auth_file.write_text(json.dumps(
        {"users": [{"username": benutzer, "password_hash": hash_fn(passwort)}]}, indent=2))
    try:
        os.chmod(auth_file, 0o600)
    except OSError:
        pass
    code_datei(data_dir).unlink(missing_ok=True)
    return None
