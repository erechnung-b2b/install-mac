# E-Rechnungssystem auf einem eigenen Server installieren

Diese Anleitung richtet das E-Rechnungssystem auf einem Linux-Server ein
(getestet mit Ubuntu 24.04, gilt sinngemäß für Debian 12). Für einen einzelnen
Arbeitsplatz-PC reicht die normale Installation aus der `README.md`.

## Was anders ist als am Arbeitsplatz

| | Einzelplatz | Server |
|---|---|---|
| Start | `starten.bat` / `./starten.sh` | `./starten.sh --server` bzw. systemd-Dienst |
| erreichbar | nur vom eigenen Rechner | im Netzwerk / Internet |
| Anmeldung | nicht nötig | **Pflicht** |
| erstes Konto | – | im Browser unter `/einrichten` mit Einrichtungscode |

## 1. Voraussetzungen

```bash
sudo apt update
sudo apt install -y curl unzip
```

Mehr ist nicht nötig: Python 3.12, Java und der offizielle KoSIT-Validator werden
bei der Installation automatisch in den Programmordner geladen (per SHA-256
geprüft). Das System-Python des Servers wird nicht verwendet und nicht verändert.
Unterstützt sind x86_64 und ARM64 mit glibc (Ubuntu, Debian, RHEL u. a.).

## 2. Programm herunterladen und installieren

```bash
sudo useradd --system --create-home --home-dir /opt/erechnung erechnung
cd /opt/erechnung
sudo -u erechnung curl -L -o install.zip https://github.com/erechnung-b2b/install/archive/refs/heads/main.zip
sudo -u erechnung unzip install.zip
sudo -u erechnung mv install-main app
cd app
sudo -u erechnung bash erstinstallation.sh
```

`erstinstallation.sh` richtet Python (`laufzeit/python`), die Programmumgebung
(`.venv`), Java (`laufzeit/java`) und den KoSIT-Validator (`tools/kosit`) ein und
schließt mit einem Selbsttest ab. Alle Daten landen später im Unterordner `data/`.

## 3. Als Dienst einrichten (startet automatisch mit dem Server)

```bash
sudo cp /opt/erechnung/app/erechnung.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now erechnung
sudo systemctl status erechnung
```

Den **Einrichtungscode** für das erste Konto zeigt:

```bash
sudo journalctl -u erechnung | grep -i einrichtungscode
# oder
sudo cat /opt/erechnung/app/data/einrichtungscode.txt
```

## 4. Erstes Konto anlegen

Im Browser `http://<server>:5000/einrichten` öffnen, Einrichtungscode,
Benutzername und Passwort eingeben. Danach ist die Einrichtung abgeschlossen,
der Code wird gelöscht, und jeder Aufruf verlangt die Anmeldung.

## 5. HTTPS (dringend empfohlen)

Ohne HTTPS gehen Passwort und Rechnungsdaten unverschlüsselt durchs Netz.
Stellen Sie den Dienst hinter nginx mit Let's-Encrypt-Zertifikat und lassen Sie
ihn selbst nur lokal lauschen. Dazu in `erechnung.service` die Zeile
`Environment=ERECHNUNG_HOST=127.0.0.1` aktivieren.

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
```

`/etc/nginx/sites-available/erechnung`:

```nginx
server {
    server_name rechnung.ihre-firma.de;
    client_max_body_size 50M;
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/erechnung /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d rechnung.ihre-firma.de
sudo systemctl restart erechnung
```

## 6. Lizenz

Nach der Installation läuft der 28-Tage-Testzeitraum. Die Geräte-ID steht unter
**Einstellungen → Lizenz**. Senden Sie sie an Ihren Anbieter; den erhaltenen
Lizenzcode tragen Sie an derselben Stelle ein.

## 7. Update

```bash
sudo systemctl stop erechnung
cd /opt/erechnung/app
sudo -u erechnung bash update.sh
sudo systemctl start erechnung
```

`update.sh` sichert `data/` nach `backup/backup-<Datum>`, lädt die aktuelle
Version, ersetzt die Programmdateien und aktualisiert die Pakete. Daten, Python,
Java und der KoSIT-Validator bleiben erhalten.

## 8. Datensicherung

Alles Wichtige liegt in `/opt/erechnung/app/data/`. Diesen Ordner regelmäßig
sichern, z. B. täglich per `tar czf` auf ein anderes Laufwerk.
