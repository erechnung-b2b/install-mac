#!/usr/bin/env python3
"""
E-Rechnungssystem – Launcher
Funktioniert unter Windows, macOS und Linux.

  python run.py                 Einzelplatz: nur dieser Rechner, Browser öffnet sich
  python run.py 5050            Einzelplatz auf anderem Port
  python run.py --server        Serverbetrieb: im Netzwerk erreichbar, Anmeldung Pflicht
  python run.py --server 8080   Serverbetrieb auf Port 8080

Umgebungsvariablen (optional, z. B. für systemd):
  ERECHNUNG_SERVER=1            wie --server
  ERECHNUNG_HOST=0.0.0.0        Adresse, auf der gelauscht wird
  ERECHNUNG_PORT=5000           Port
"""
import os
import sys
import threading
import time
import webbrowser


def get_base_path():
    """Basispfad – funktioniert normal und als PyInstaller-Bundle."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def open_browser(port, delay=1.5):
    time.sleep(delay)
    url = f"http://localhost:{port}"
    print(f"\n  Browser wird geoeffnet: {url}")
    webbrowser.open(url)


def main():
    base = get_base_path()
    os.chdir(base)

    args = [a for a in sys.argv[1:]]
    server = "--server" in args or os.environ.get("ERECHNUNG_SERVER", "").strip() in ("1", "true", "ja", "yes")
    args = [a for a in args if a != "--server"]
    if server:
        os.environ["ERECHNUNG_SERVER"] = "1"   # vor dem Import von webapp setzen

    port = int(os.environ.get("ERECHNUNG_PORT") or 5000)
    if args:
        try:
            port = int(args[0])
        except ValueError:
            pass
    host = os.environ.get("ERECHNUNG_HOST") or ("0.0.0.0" if server else "127.0.0.1")

    for d in ("data/archiv", "data/export", "data/sent_mails",
              "data/test_mails", "data/logo"):
        os.makedirs(os.path.join(base, d), exist_ok=True)

    print("=" * 60)
    print("  E-Rechnungssystem")
    print("  XRechnung / ZUGFeRD / EN 16931")
    print("=" * 60)
    print(f"\n  Betriebsart: {'Server (Anmeldung Pflicht)' if server else 'Einzelplatz (nur dieser Rechner)'}")
    print(f"  Adresse: http://{'localhost' if host in ('127.0.0.1', 'localhost') else host}:{port}")
    print(f"  Datenverzeichnis: {os.path.join(base, 'data')}")

    from webapp import app, load_data, _AUTH_FILE, _DATA
    import zugang

    if server and not _AUTH_FILE.exists():
        code = zugang.einrichtungscode(_DATA / "data")
        print("\n  ── ERSTE EINRICHTUNG ─────────────────────────────────")
        print("  Noch kein Benutzer angelegt. Im Browser /einrichten öffnen")
        print(f"  und diesen Einrichtungscode eingeben:   {code}")
        print("  (steht auch in data/einrichtungscode.txt)")
        print("  ──────────────────────────────────────────────────────")

    print("\n  Zum Beenden: Strg+C druecken oder Fenster schliessen")
    print("-" * 60, flush=True)

    load_data()

    if server:
        try:
            from waitress import serve
            print(f"  Webserver: waitress auf {host}:{port}", flush=True)
            serve(app, host=host, port=port, threads=8)
            return
        except ImportError:
            print("  Hinweis: 'waitress' fehlt (pip install waitress) – nutze den eingebauten Server.", flush=True)
    else:
        threading.Thread(target=open_browser, args=(port,), daemon=True).start()

    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
