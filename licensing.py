"""
E-Rechnungssystem - Lizenzverwaltung (RSA-2048, asymmetrisch)

Sicherheitskonzept:
  - Privater Schluessel: NUR beim Anbieter (lizenz_data/private_key.pem)
  - Oeffentlicher Schluessel: in der Kundensoftware eingebettet
  - Kunde kann Lizenzen VERIFIZIEREN, aber nicht ERZEUGEN
  - Selbst bei Dekompilierung der .exe ist kein Lizenzbetrug moeglich

Ablauf:
  1. Einmalig: Schluesselpaar erzeugen (generate_keypair)
  2. Anbieter: Lizenzcode signieren mit Private Key (sign_license)
  3. Kunde: Lizenzcode verifizieren mit Public Key (verify_license)

Geraete-ID:
  - 10-stellige Zahl aus Hardware-Fingerprint
  - Wird beim Erststart in data/device_id.txt gespeichert
  - Lizenz ist an diese Geraete-ID gebunden
"""
from __future__ import annotations
import hashlib, json, platform, uuid, os, subprocess
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

# ── RSA Kryptografie ─────────────────────────────────────────────────

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from cryptography.exceptions import InvalidSignature
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

TRIAL_DAYS = 28
KEY_BITS = 2048


# ══════════════════════════════════════════════════════════════════════
# Schluesselverwaltung
# ══════════════════════════════════════════════════════════════════════

def generate_keypair(output_dir: str = "./lizenz_data") -> dict:
    """Erzeugt ein RSA-Schluesselpaar. NUR EINMAL beim Anbieter ausfuehren."""
    if not HAS_CRYPTO:
        raise RuntimeError("Paket 'cryptography' fehlt: pip install cryptography")
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    priv_path = out / "private_key.pem"
    pub_path = out / "public_key.pem"
    if priv_path.exists():
        raise FileExistsError(
            f"Schluesselpaar existiert bereits in {out}. "
            "Loeschen Sie die Dateien manuell wenn Sie ein neues Paar erzeugen wollen."
        )
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=KEY_BITS)
    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    priv_path.write_bytes(priv_pem)
    pub_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    pub_path.write_bytes(pub_pem)
    return {
        "private_key_path": str(priv_path),
        "public_key_path": str(pub_path),
        "public_key_pem": pub_pem.decode("ascii"),
    }


def load_private_key(path: str = "./lizenz_data/private_key.pem"):
    if not HAS_CRYPTO:
        raise RuntimeError("Paket 'cryptography' fehlt: pip install cryptography")
    return serialization.load_pem_private_key(Path(path).read_bytes(), password=None)


def load_public_key(pem_data: str = ""):
    if not HAS_CRYPTO:
        raise RuntimeError("Paket 'cryptography' fehlt: pip install cryptography")
    if not pem_data:
        raise ValueError("Kein Public-Key-PEM uebergeben.")
    return serialization.load_pem_public_key(pem_data.encode("ascii"))


# ══════════════════════════════════════════════════════════════════════
# Eingebetteter Public Key (wird beim Build eingesetzt)
# ══════════════════════════════════════════════════════════════════════

# Dieser Wert wird durch den echten Public Key ersetzt,
# sobald ein Schluesselpaar erzeugt wurde. Das Skript
#   python license_admin.py --embed-pubkey
# erledigt das automatisch.
#
# Wenn hier noch kein Key steht, laedt das System den Key aus der Datei
# data/public_key.pem (Entwicklungsmodus).

EMBEDDED_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA7MoZGU0ZyBh4hJrwpKLz
CEGfN9YGci4LJJrvCNbizqWGWQd7d1JglNbPp9awU6+pir8ClpwiZCOhYZU6vlMR
c2VCTuvq+xIr69iZTilYYLg2UweNiWx5meOSn9nC8DR5i2HbDq9qVSDrABdYU8yu
fh51obvLcTrqNXa6x9X5hzLDOnIrD9aMZPpwNzSKrzBx2OxcZMx5xu1lxGQhBJ0V
f7esv2ejM8wDOU2hRG0+Z6aEy/tekr8Tu/4XTJ96cYCEHaHn1v63KD16wuykxNOa
/tnPkjHcKocmtsdYqD25kBHZzFD61GOuLCxMsRd2exI+RazCrafDvgVUdR234Ui/
HQIDAQAB
-----END PUBLIC KEY-----"""


def _get_public_key_pem(data_dir: str = "./data") -> str:
    """Gibt den Public Key als PEM-String zurueck."""
    if "PLACEHOLDER" not in EMBEDDED_PUBLIC_KEY and len(EMBEDDED_PUBLIC_KEY) > 100:
        return EMBEDDED_PUBLIC_KEY.strip()
    pub_file = Path(data_dir) / "public_key.pem"
    if pub_file.exists():
        return pub_file.read_text("ascii").strip()
    pub_file2 = Path("./lizenz_data/public_key.pem")
    if pub_file2.exists():
        return pub_file2.read_text("ascii").strip()
    return ""


# ══════════════════════════════════════════════════════════════════════
# Geraete-ID
# ══════════════════════════════════════════════════════════════════════

def _get_machine_fingerprint() -> str:
    parts = []
    parts.append(platform.node())
    try:
        parts.append(str(uuid.getnode()))
    except Exception:
        parts.append("no-mac")

    # Plattformspezifische Hardware-ID
    system = platform.system()
    if system == "Windows":
        try:
            result = subprocess.run(
                ["reg", "query", r"HKLM\SOFTWARE\Microsoft\Cryptography", "/v", "MachineGuid"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if "MachineGuid" in line:
                    parts.append(line.strip().split()[-1])
                    break
        except Exception:
            parts.append("no-guid")
    elif system == "Darwin":  # macOS
        try:
            result = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if "IOPlatformUUID" in line:
                    parts.append(line.strip().split('"')[-2])
                    break
        except Exception:
            parts.append("no-uuid")
    else:  # Linux
        try:
            mid = Path("/etc/machine-id")
            if mid.exists():
                parts.append(mid.read_text().strip())
            else:
                parts.append("no-machine-id")
        except Exception:
            parts.append("no-machine-id")

    parts.append(platform.processor())
    return "|".join(parts)


def generate_device_id() -> str:
    fp = _get_machine_fingerprint()
    h = hashlib.sha256(fp.encode()).hexdigest()
    digits = "".join(c for c in h if c.isdigit())
    if len(digits) < 10:
        digits = str(int(h[:16], 16))
    return digits[:10]


def get_or_create_device_id(data_dir: str = "./data") -> str:
    path = Path(data_dir) / "device_id.txt"
    if path.exists():
        stored = path.read_text(encoding="utf-8").strip()
        if len(stored) == 10 and stored.isdigit():
            return stored
    device_id = generate_device_id()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(device_id, encoding="utf-8")
    return device_id


# ══════════════════════════════════════════════════════════════════════
# Lizenz erzeugen (NUR mit Private Key = NUR beim Anbieter)
# ══════════════════════════════════════════════════════════════════════

def generate_license_key(customer: str, device_id: str,
                          valid_days: int = 365,
                          valid_from: date = None,
                          private_key_path: str = "./lizenz_data/private_key.pem") -> dict:
    """Erzeugt einen RSA-signierten Lizenzschluessel."""
    if not HAS_CRYPTO:
        raise RuntimeError("Paket 'cryptography' fehlt: pip install cryptography")
    private_key = load_private_key(private_key_path)
    if valid_from is None:
        valid_from = date.today()
    valid_until = valid_from + timedelta(days=valid_days)
    payload = json.dumps({
        "c": customer, "d": device_id,
        "f": valid_from.isoformat(), "u": valid_until.isoformat(),
    }, separators=(",", ":"), ensure_ascii=True)
    payload_bytes = payload.encode("utf-8")
    signature = private_key.sign(payload_bytes, padding.PKCS1v15(), hashes.SHA256())
    raw = payload_bytes.hex() + "." + signature.hex()
    groups = [raw[i:i + 5] for i in range(0, len(raw), 5)]
    key = "ERECH-" + "-".join(groups)
    return {
        "key": key, "customer": customer, "device_id": device_id,
        "valid_from": valid_from.isoformat(), "valid_until": valid_until.isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════
# Lizenz verifizieren (NUR mit Public Key = sicher fuer Kundensoftware)
# ══════════════════════════════════════════════════════════════════════

def validate_license_key(key: str, expected_device_id: str = "",
                          public_key_pem: str = "",
                          data_dir: str = "./data") -> tuple:
    """Verifiziert einen Lizenzschluessel. Gibt (valid, result_dict) zurueck.

    Interface-kompatibel mit der alten HMAC-Version.
    """
    if not HAS_CRYPTO:
        return False, {"error": "Kryptografie-Bibliothek fehlt (pip install cryptography)."}

    pem = public_key_pem or _get_public_key_pem(data_dir)
    if not pem or "PLACEHOLDER" in pem:
        return False, {"error": "Kein Public Key konfiguriert (Entwicklungsmodus)."}

    try:
        pub_key = load_public_key(pem)
    except Exception as e:
        return False, {"error": f"Public Key ungueltig: {e}"}

    # Key entformatieren
    raw = key.strip()
    if raw.upper().startswith("ERECH-"):
        raw = raw[6:]
    raw = raw.replace("-", "")

    if "." not in raw:
        return False, {"error": "Ungueltiges Lizenzformat."}

    dot_pos = raw.index(".")
    payload_hex = raw[:dot_pos]
    sig_hex = raw[dot_pos + 1:]

    try:
        payload_bytes = bytes.fromhex(payload_hex)
        signature = bytes.fromhex(sig_hex)
    except (ValueError, Exception):
        return False, {"error": "Lizenzcode konnte nicht dekodiert werden."}

    # Signatur pruefen
    try:
        pub_key.verify(signature, payload_bytes, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        return False, {"error": "Ungueltige Signatur - Lizenzcode ist gefaelscht oder beschaedigt."}
    except Exception as e:
        return False, {"error": f"Signaturpruefung fehlgeschlagen: {e}"}

    # Payload parsen
    try:
        data = json.loads(payload_bytes.decode("utf-8"))
    except Exception:
        return False, {"error": "Lizenzdaten beschaedigt."}

    customer = data.get("c", "")
    lic_device = data.get("d", "")
    valid_from = data.get("f", "")
    valid_until = data.get("u", "")

    result = {
        "customer": customer, "device_id": lic_device,
        "valid_from": valid_from, "valid_until": valid_until,
    }

    if expected_device_id and lic_device and expected_device_id != lic_device:
        result["error"] = (f"Geraete-ID stimmt nicht ueberein. "
                           f"Lizenz: {lic_device}, dieser Rechner: {expected_device_id}")
        return False, result

    try:
        until = date.fromisoformat(valid_until)
        if until < date.today():
            result["error"] = f"Lizenz abgelaufen am {valid_until}."
            result["expired"] = True
            return False, result
    except ValueError:
        pass

    return True, result


# ══════════════════════════════════════════════════════════════════════
# LicenseInfo und LicenseManager (Drop-in fuer webapp.py)
# ══════════════════════════════════════════════════════════════════════

@dataclass
class LicenseInfo:
    status: str = "TRIAL"
    customer_name: str = ""
    license_key: str = ""
    device_id: str = ""
    valid_from: str = ""
    valid_until: str = ""
    trial_start: str = ""
    trial_days_left: int = 0

    @property
    def is_active(self) -> bool:
        return self.status in ("TRIAL", "ACTIVE")

    @property
    def days_remaining(self) -> int:
        if self.status == "TRIAL":
            return self.trial_days_left
        if self.status == "ACTIVE" and self.valid_until:
            return max(0, (date.fromisoformat(self.valid_until) - date.today()).days)
        return 0

    @property
    def status_text(self) -> str:
        if self.status == "TRIAL":
            return f"Testversion ({self.trial_days_left} Tage verbleibend)"
        if self.status == "ACTIVE":
            d = self.days_remaining
            if d > 30:
                return f"Lizenziert bis {self.valid_until}"
            return f"Lizenziert (laeuft in {d} Tagen ab)"
        if self.status == "EXPIRED":
            if not self.license_key:
                return "Testphase abgelaufen - bitte Lizenzcode eingeben"
            return "Lizenz abgelaufen - bitte Verlaengerung anfordern"
        if self.status == "WRONG_DEVICE":
            return "Lizenz gehoert zu einem anderen Rechner"
        return "Kein gueltiger Lizenzstatus"

    def to_dict(self) -> dict:
        """Kompatibel mit der alten HMAC-Version."""
        return {
            "status": self.status,
            "is_active": self.is_active,
            "is_trial": self.status == "TRIAL",
            "is_expired": self.status in ("EXPIRED", "WRONG_DEVICE"),
            "customer_name": self.customer_name,
            "device_id": self.device_id,
            "license_key_short": self.license_key[:20] + "..." if self.license_key else "",
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "trial_start": self.trial_start,
            "trial_days_left": self.trial_days_left,
            "days_remaining": self.days_remaining,
            "status_text": self.status_text,
        }


class LicenseManager:
    """Verwaltet den Lizenzstatus der Installation."""

    def __init__(self, data_dir: str = "./data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.license_file = self.data_dir / "license.json"
        self.device_id = get_or_create_device_id(str(self.data_dir))
        self._info: Optional[LicenseInfo] = None

    def get_info(self) -> LicenseInfo:
        self._info = None
        lic_data = self._load()
        trial_start = lic_data.get("trial_start", "")
        if not trial_start:
            trial_start = date.today().isoformat()
            lic_data["trial_start"] = trial_start
            self._save(lic_data)
        trial_elapsed = (date.today() - date.fromisoformat(trial_start)).days
        trial_left = max(0, TRIAL_DAYS - trial_elapsed)

        info = LicenseInfo(
            device_id=self.device_id,
            trial_start=trial_start,
            trial_days_left=trial_left,
        )

        stored_key = lic_data.get("license_key", "")
        if stored_key:
            pub_pem = _get_public_key_pem(str(self.data_dir))
            valid, result = validate_license_key(
                stored_key, expected_device_id=self.device_id,
                public_key_pem=pub_pem, data_dir=str(self.data_dir),
            )
            info.license_key = stored_key
            info.customer_name = result.get("customer", "")
            info.valid_from = result.get("valid_from", "")
            info.valid_until = result.get("valid_until", "")
            if valid:
                info.status = "ACTIVE"
            elif result.get("error", "").startswith("Geraete-ID"):
                info.status = "WRONG_DEVICE"
            elif result.get("expired"):
                info.status = "EXPIRED"
            else:
                info.status = "EXPIRED" if trial_left == 0 else "TRIAL"
                info.license_key = ""
        else:
            info.status = "TRIAL" if trial_left > 0 else "EXPIRED"

        self._info = info
        return info

    def is_write_allowed(self) -> bool:
        info = self.get_info()
        return info.is_active

    def check_or_block(self) -> Optional[dict]:
        """Gibt None zurueck wenn Schreiben erlaubt, sonst Fehler-Dict."""
        if self.is_write_allowed():
            return None
        info = self.get_info()
        return {
            "error": "Lizenz erforderlich",
            "license_status": info.status,
            "device_id": info.device_id,
            "message": info.status_text,
        }

    def activate(self, license_key: str) -> tuple:
        """Aktiviert einen Lizenzschluessel. Gibt (success, message) zurueck."""
        license_key = license_key.strip()
        pub_pem = _get_public_key_pem(str(self.data_dir))
        valid, result = validate_license_key(
            license_key, expected_device_id=self.device_id,
            public_key_pem=pub_pem, data_dir=str(self.data_dir),
        )
        if not valid:
            return False, result.get("error", "Ungueltiger Lizenzcode")

        lic_data = self._load()
        lic_data["license_key"] = license_key
        lic_data["customer_name"] = result.get("customer", "")
        lic_data["activated_at"] = date.today().isoformat()
        self._save(lic_data)
        self._info = None
        return True, f"Lizenz aktiviert fuer {result.get('customer', '')}"

    def _load(self) -> dict:
        if self.license_file.exists():
            try:
                return json.loads(self.license_file.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save(self, data: dict):
        self.license_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
