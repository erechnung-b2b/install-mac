"""Lädt den offiziellen KoSIT-Validator + XRechnung-Konfiguration nach tools/kosit.
Aufruf (von der Erstinstallation):  python installer/kosit_laden.py
"""
import io, json, sys, urllib.request, zipfile
from pathlib import Path

ZIEL = Path(__file__).resolve().parent.parent / "tools" / "kosit"


def api(repo):
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "erechnung-setup"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["assets"]


def laden(url):
    req = urllib.request.Request(url, headers={"User-Agent": "erechnung-setup"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


try:
    jar = next(a for a in api("itplr-kosit/validator") if a["name"].endswith("standalone.jar"))
    konf = next(a for a in api("itplr-kosit/validator-configuration-xrechnung")
                if a["name"].endswith(".zip") and "test" not in a["name"].lower() and "source" not in a["name"].lower())
    ZIEL.mkdir(parents=True, exist_ok=True)
    (ZIEL / "validator.jar").write_bytes(laden(jar["browser_download_url"]))
    zipfile.ZipFile(io.BytesIO(laden(konf["browser_download_url"]))).extractall(ZIEL)
    ok = (ZIEL / "scenarios.xml").exists()
    print("KoSIT", jar["name"], "+", konf["name"], "OK" if ok else "unvollständig")
    sys.exit(0 if ok else 1)
except Exception as e:
    print("KoSIT-Download fehlgeschlagen:", e)
    (ZIEL / "validator.jar").unlink(missing_ok=True)
    sys.exit(1)
