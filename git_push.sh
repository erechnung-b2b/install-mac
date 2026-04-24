#!/usr/bin/env bash
# ================================================================
#  E-Rechnungssystem – Git Push (Windows + Mac Repo)
#  Nutzung: GIT_TOKEN="ghp_xxx" ./git_push.sh
# ================================================================
set -e

if [ -z "$GIT_TOKEN" ]; then
    echo ""
    echo "  Bitte Token setzen:"
    echo "  export GIT_TOKEN=\"ghp_DEIN_TOKEN\""
    echo "  ./git_push.sh"
    echo ""
    echo "  Token erzeugen: https://github.com/settings/tokens"
    echo "  Scope: repo"
    exit 1
fi

ORG="erechnung-b2b"
SRC="$HOME/erechnung"

echo ""
echo "  ========================================================"
echo "   Git Push – E-Rechnungssystem v2.0"
echo "  ========================================================"
echo ""

# ── 1. Windows-Repo ──
echo "  [1/2] Windows-Repo (install)..."
cd /tmp && rm -rf push-win
git clone "https://${ORG}:${GIT_TOKEN}@github.com/${ORG}/install.git" push-win 2>&1 | tail -1
cd push-win

# Alle Python-Module kopieren
cp "$SRC"/*.py . 2>/dev/null
# Windows-Scripts
cp "$SRC"/*.bat . 2>/dev/null
# Unix-Scripts
cp "$SRC"/*.sh . 2>/dev/null
# Docs
cp "$SRC"/*.md . 2>/dev/null
cp "$SRC"/*.txt . 2>/dev/null
# Frontend
cp "$SRC"/static/index.html static/
# Logo
cp "$SRC"/Logo.jpg . 2>/dev/null
# .gitignore erweitern
if ! grep -q "backup" .gitignore 2>/dev/null; then
    echo "" >> .gitignore
    echo "# Backups" >> .gitignore
    echo "backup*.zip" >> .gitignore
    echo ".venv/" >> .gitignore
fi

git config user.email "install@e-rechnung-b2b.de"
git config user.name "Rolf Krause"
git add -A
CHANGES=$(git diff --cached --stat | tail -1)
if [ -n "$CHANGES" ]; then
    git commit -m "v2.0: Auftragsmanagement, Produktkatalog, Mahnwesen, Steuerberater-Export

Neue Module:
- suppliers.py (Lieferanten-Verwaltung)
- transactions.py (8-Stufen-Workflow)
- products.py (Produktkatalog EK/VK)
- doc_generator.py (PDF-Erzeugung)
- dunning.py (Mahnwesen 3 Stufen)
- backup.sh/.bat (Datensicherung)

Geaendert:
- webapp.py (+1252 Zeilen, Steuerberater-Export, Produkt-API)
- index.html (+1755 Zeilen, Kunden/Produkte/Vorgaenge UI)
- erstinstallation.bat (Auto-Setup mit venv)
- starten.bat (Self-Healing Start)"
    git push
    echo "  ✓ Windows-Repo gepusht: $CHANGES"
else
    echo "  ✓ Windows-Repo: keine Änderungen"
fi

# ── 2. Mac-Repo ──
echo ""
echo "  [2/2] Mac-Repo (install-mac)..."
cd /tmp && rm -rf push-mac
git clone "https://${ORG}:${GIT_TOKEN}@github.com/${ORG}/install-mac.git" push-mac 2>&1 | tail -1
cd push-mac

# Python-Module
cp "$SRC"/*.py . 2>/dev/null
# Unix-Scripts (keine .bat für Mac)
cp "$SRC"/*.sh . 2>/dev/null
# Docs
cp "$SRC"/*.md . 2>/dev/null
cp "$SRC"/*.txt . 2>/dev/null
# Frontend
cp "$SRC"/static/index.html static/
# Logo
cp "$SRC"/Logo.jpg . 2>/dev/null
# .gitignore
if ! grep -q "backup" .gitignore 2>/dev/null; then
    echo "" >> .gitignore
    echo "# Backups" >> .gitignore
    echo "backup*.zip" >> .gitignore
    echo ".venv/" >> .gitignore
fi

git config user.email "install@e-rechnung-b2b.de"
git config user.name "Rolf Krause"
git add -A
CHANGES=$(git diff --cached --stat | tail -1)
if [ -n "$CHANGES" ]; then
    git commit -m "v2.0: Auftragsmanagement, Produktkatalog, Mahnwesen, Steuerberater-Export"
    git push
    echo "  ✓ Mac-Repo gepusht: $CHANGES"
else
    echo "  ✓ Mac-Repo: keine Änderungen"
fi

# ── Aufräumen ──
rm -rf /tmp/push-win /tmp/push-mac

echo ""
echo "  ========================================================"
echo "   ✓ Beide Repos aktualisiert!"
echo "  ========================================================"
echo "  https://github.com/${ORG}/install"
echo "  https://github.com/${ORG}/install-mac"
echo ""
