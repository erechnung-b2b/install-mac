#!/usr/bin/env bash
# ========================================================
#  E-Rechnungssystem - Erstinstallation macOS
#  Installiert ALLE Voraussetzungen automatisch:
#    - Homebrew (falls fehlend)
#    - Python 3.12 (via brew)
#    - Java 17 Temurin (via brew, fuer KoSIT-Validator)
#    - Python-Pakete (pip3 install -r requirements.txt)
#    - KoSIT-Validator + XRechnung-Konfiguration
#    - Datenverzeichnisse
#  Aufruf einmalig per: ./erstinstallation.sh
# ========================================================
set -e

# Farben für lesbare Ausgabe
BLUE='\033[1;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo -e "${BLUE}  ========================================================${NC}"
echo -e "${BLUE}   E-Rechnungssystem - Erstinstallation macOS${NC}"
echo -e "${BLUE}   Diese Installation richtet ALLES ein, was die Software${NC}"
echo -e "${BLUE}   zum Laufen braucht. Bitte einmalig ausführen.${NC}"
echo -e "${BLUE}  ========================================================${NC}"
echo ""

# Ins Skript-Verzeichnis wechseln
cd "$(dirname "$0")"

# ── 1. Homebrew prüfen ───────────────────────────────────
echo -e "  [1/7] Prüfe Homebrew..."
if ! command -v brew &>/dev/null; then
    echo -e "         ${YELLOW}Homebrew nicht gefunden — installiere...${NC}"
    echo ""
    echo "  Hinweis: Die Homebrew-Installation fragt nach Ihrem Passwort."
    echo ""
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Apple Silicon: brew nach /opt/homebrew/bin
    if [ -f /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [ -f /usr/local/bin/brew ]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
fi
echo -e "         ${GREEN}$(brew --version | head -1)${NC}"
echo ""

# ── 2. Python prüfen ─────────────────────────────────────
echo -e "  [2/7] Prüfe Python..."
if ! command -v python3 &>/dev/null; then
    echo -e "         ${YELLOW}Python3 nicht gefunden — installiere via brew...${NC}"
    brew install python@3.12
fi
PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo -e "         ${YELLOW}Python $PY_VERSION ist zu alt — installiere 3.12...${NC}"
    brew install python@3.12
fi
echo -e "         ${GREEN}$(python3 --version)${NC}"
echo ""

# ── 3. Java prüfen (für KoSIT-Validator) ─────────────────
echo -e "  [3/7] Prüfe Java (für KoSIT-Validator)..."
SKIP_KOSIT=false
if ! command -v java &>/dev/null; then
    echo -e "         ${YELLOW}Java nicht gefunden — installiere Temurin JRE 17 via brew...${NC}"
    if brew install --cask temurin17 2>/dev/null; then
        echo -e "         ${GREEN}Java 17 installiert.${NC}"
    else
        echo -e "         ${YELLOW}WARNUNG: Java-Installation fehlgeschlagen.${NC}"
        echo -e "         ${YELLOW}Software läuft auch ohne Java, KoSIT-Validator entfällt.${NC}"
        SKIP_KOSIT=true
    fi
fi
if command -v java &>/dev/null; then
    JAVA_INFO=$(java -version 2>&1 | head -1)
    echo -e "         ${GREEN}$JAVA_INFO${NC}"
fi
echo ""

# ── 4. Python-Pakete installieren ────────────────────────
echo -e "  [4/7] Installiere Python-Pakete (kann 1-2 Minuten dauern)..."
python3 -m pip install --upgrade pip --quiet
# --break-system-packages auf neueren macOS-Pythons nötig
if python3 -m pip install -r requirements.txt --quiet 2>/dev/null; then
    :
else
    python3 -m pip install -r requirements.txt --quiet --break-system-packages
fi
echo -e "         ${GREEN}OK${NC}"
echo ""

# ── 5. KoSIT-Validator herunterladen ─────────────────────
echo -e "  [5/7] Lade KoSIT-Validator..."
if [ "$SKIP_KOSIT" = "true" ]; then
    echo -e "         ${YELLOW}Java fehlt — übersprungen.${NC}"
elif [ -f "tools/kosit/scenarios.xml" ]; then
    echo -e "         ${GREEN}KoSIT bereits installiert — übersprungen.${NC}"
else
    mkdir -p tools/kosit
    cd tools/kosit

    echo "         Lade Validator-JAR von GitHub..."
    JAR_URL=$(curl -fsSL https://api.github.com/repos/itplr-kosit/validator/releases/latest \
        | grep -o '"browser_download_url": *"[^"]*standalone\.jar"' \
        | head -1 \
        | sed 's/.*: *"\(.*\)"/\1/')

    if [ -z "$JAR_URL" ]; then
        echo -e "         ${YELLOW}WARNUNG: Konnte Validator-Download-URL nicht ermitteln.${NC}"
    else
        curl -fsSL "$JAR_URL" -o validator.jar
        echo "         Validator-JAR heruntergeladen."

        echo "         Lade XRechnung-Konfiguration von GitHub..."
        ZIP_URL=$(curl -fsSL https://api.github.com/repos/itplr-kosit/validator-configuration-xrechnung/releases/latest \
            | grep -o '"browser_download_url": *"[^"]*\.zip"' \
            | grep -v test \
            | grep -v source \
            | head -1 \
            | sed 's/.*: *"\(.*\)"/\1/')

        if [ -z "$ZIP_URL" ]; then
            echo -e "         ${YELLOW}WARNUNG: Konnte Konfigurations-ZIP nicht finden.${NC}"
        else
            curl -fsSL "$ZIP_URL" -o config.zip
            unzip -qo config.zip
            rm config.zip
            if [ -f "scenarios.xml" ]; then
                echo -e "         ${GREEN}OK — KoSIT-Validator einsatzbereit.${NC}"
            else
                echo -e "         ${YELLOW}WARNUNG: scenarios.xml nicht gefunden.${NC}"
            fi
        fi
    fi
    cd ../..
fi
echo ""

# ── 6. Datenverzeichnisse anlegen ────────────────────────
echo -e "  [6/7] Lege Datenverzeichnisse an..."
mkdir -p data/archiv data/export data/sent_mails data/logo
echo -e "         ${GREEN}OK${NC}"
echo ""

# ── 7. Funktionstest ─────────────────────────────────────
echo -e "  [7/7] Prüfe, ob die Anwendung startbereit ist..."
if python3 -c "from webapp import app" 2>/dev/null; then
    echo -e "         ${GREEN}OK${NC}"
else
    echo -e "         ${RED}FEHLER: Anwendung kann nicht geladen werden.${NC}"
    echo "         Bitte requirements.txt prüfen oder Support kontaktieren."
    exit 1
fi
echo ""

# Startskripte ausführbar machen
chmod +x starten.sh 2>/dev/null || true

echo -e "${BLUE}  ========================================================${NC}"
echo -e "${GREEN}   Installation erfolgreich abgeschlossen!${NC}"
echo -e "${BLUE}  ========================================================${NC}"
echo ""
echo "   Software starten mit:  ./starten.sh"
echo ""
echo "   Beim ersten Start:"
echo "     1. Browser öffnet sich automatisch auf http://localhost:5000"
echo "     2. 28 Tage kostenlose Testphase startet"
echo "     3. Lizenz später unter 'Einstellungen' eingeben"
echo ""
