#!/usr/bin/env python3
"""
E-Rechnungssystem – Standalone Launcher
Startet den Server und oeffnet automatisch den Browser.
Funktioniert unter Windows, macOS und Linux.
"""
import os
import sys
import time
import threading
import webbrowser

def get_base_path():
    """Ermittelt den Basispfad – funktioniert sowohl normal als auch als PyInstaller-Bundle."""
    if getattr(sys, 'frozen', False):
        # PyInstaller-Bundle: _MEIPASS enthält den temp-Ordner mit den Dateien
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def open_browser(port, delay=1.5):
    """Öffnet den Browser nach kurzem Warten, damit der Server bereit ist."""
    time.sleep(delay)
    url = f"http://localhost:{port}"
    print(f"\n  Browser wird geoeffnet: {url}")
    webbrowser.open(url)


def main():
    base = get_base_path()
    os.chdir(base)

    # Datenverzeichnisse anlegen (neben der .exe bzw. dem Script)
    for d in ("data/archiv", "data/export", "data/sent_mails",
              "data/test_mails", "data/logo"):
        os.makedirs(os.path.join(base, d), exist_ok=True)

    # Port aus Argument oder Standard
    port = 5000
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass

    # Banner
    print("=" * 60)
    print("  E-Rechnungssystem")
    print("  XRechnung / ZUGFeRD / EN 16931")
    print("=" * 60)
    print(f"\n  Server startet auf Port {port}...")
    print(f"  URL: http://localhost:{port}")
    print(f"  Datenverzeichnis: {os.path.join(base, 'data')}")
    print(f"\n  Zum Beenden: Strg+C druecken oder Fenster schliessen")
    print("-" * 60)

    # Browser in separatem Thread öffnen
    threading.Thread(target=open_browser, args=(port,), daemon=True).start()

    # Server starten
    from webapp import app, load_data
    load_data()
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
