"""Verschlüsselte Ablage für Bank-Zugangsdaten (PIN) + FinTS-Dialog-State.

- AES-256-GCM über das vorhandene `cryptography`-Paket.
- Schlüssel in data/.bank_key (0600, einmalig autogeneriert, analog data/.secret_key).
- PIN und serialisierter FinTS-State werden AUSSCHLIESSLICH verschlüsselt abgelegt
  und niemals geloggt. Der Schlüssel liegt getrennt von den Daten.
"""
import os
import json
import base64
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_DATA = Path(__file__).resolve().parent
_KEY_FILE = _DATA / "data" / ".bank_key"
_CONN_FILE = _DATA / "data" / "bank_connections.json"


def _write_private(path: Path, data, binary: bool):
    """Atomar mit 0600 schreiben (Datei darf nur dem Dienst-User gehören)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(str(path), flags, 0o600)
    mode = "wb" if binary else "w"
    enc = None if binary else "utf-8"
    with os.fdopen(fd, mode, encoding=enc) as f:
        f.write(data)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _load_key() -> bytes:
    if _KEY_FILE.exists():
        return _KEY_FILE.read_bytes()
    key = AESGCM.generate_key(bit_length=256)
    _write_private(_KEY_FILE, key, binary=True)
    return key


def encrypt(plaintext) -> str:
    """str/bytes → base64(nonce|ciphertext). None → None."""
    if plaintext is None:
        return None
    if isinstance(plaintext, str):
        plaintext = plaintext.encode("utf-8")
    aes = AESGCM(_load_key())
    nonce = os.urandom(12)
    ct = aes.encrypt(nonce, plaintext, None)
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt(token: str) -> str:
    """base64(nonce|ciphertext) → str. None/'' → None."""
    if not token:
        return None
    raw = base64.b64decode(token)
    aes = AESGCM(_load_key())
    return aes.decrypt(raw[:12], raw[12:], None).decode("utf-8")


def decrypt_bytes(token: str) -> bytes:
    if not token:
        return None
    raw = base64.b64decode(token)
    aes = AESGCM(_load_key())
    return aes.decrypt(raw[:12], raw[12:], None)


def encrypt_bytes(data: bytes) -> str:
    if data is None:
        return None
    aes = AESGCM(_load_key())
    nonce = os.urandom(12)
    return base64.b64encode(nonce + aes.encrypt(nonce, data, None)).decode("ascii")


# ── Verbindungs-Store (data/bank_connections.json, 0600) ──────────────────

def load_connections() -> dict:
    if not _CONN_FILE.exists():
        return {}
    try:
        return json.loads(_CONN_FILE.read_text("utf-8"))
    except Exception:
        return {}


def save_connections(conns: dict):
    _write_private(_CONN_FILE, json.dumps(conns, indent=2, ensure_ascii=False), binary=False)
