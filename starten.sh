#!/usr/bin/env bash
# ================================================================
#  E-Rechnungssystem – Server starten (Linux / macOS / WSL)
#  Nutzt .venv wenn vorhanden, prüft Port, startet Browser.
# ================================================================

cd "$(dirname "$0")"
BASE="$(pwd)"

echo ""
echo "  ========================================================"
echo "   E-Rechnungssystem"
echo "   XRechnung / ZUGFeRD / EN 16931"
echo "  ========================================================"
echo ""

# ── Erstinstallation? ────────────────────────────────────────
if [ ! -f ".deps_installed" ]; then
    echo "  Erstmalige Einrichtung erkannt."
    if [ -f "install.sh" ]; then
        echo "  Starte install.sh..."
        bash install.sh
    else
        echo "  ✗ install.sh nicht gefunden."
        echo "    Bitte zuerst ./install.sh ausführen."
        exit 1
    fi
fi

# ── Virtual Environment aktivieren ───────────────────────────
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Python prüfen
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "  ✗ Python nicht gefunden!"
    exit 1
fi

# ── Freien Port finden ───────────────────────────────────────
# Port 5000 ist auf macOS oft von AirPlay belegt, auf manchen
# Linux-Distros von anderen Diensten.
PORT=${1:-5000}

find_free_port() {
    local port=$1
    local max_tries=10
    for i in $(seq 1 $max_tries); do
        if ! $PYTHON -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.bind(('127.0.0.1', $port))
    s.close()
    exit(0)
except OSError:
    exit(1)
" 2>/dev/null; then
            echo "  ⚠ Port $port belegt, versuche $(($port + 1))..." >&2
            port=$(($port + 1))
        else
            echo $port
            return
        fi
    done
    echo $port
}

PORT=$(find_free_port $PORT)

# ── Starten ──────────────────────────────────────────────────
echo "  Server startet auf Port $PORT..."
echo "  URL: http://localhost:$PORT"
echo "  Daten: $BASE/data/"
echo ""
echo "  Zum Beenden: Strg+C"
echo "  ────────────────────────────────────────────────────"
echo ""

$PYTHON run.py $PORT
