"""
KoSIT-Validator-Hook — bindet den offiziellen XRechnung-Validator ein.

Der KoSIT-Validator (https://github.com/itplr-kosit/validator) ist das
offizielle Prüftool für XRechnung-Konformität. Er wird als Java-JAR
ausgeliefert und hier per subprocess aufgerufen.

Einsatz:
- Setup auf dem Server: KoSIT-Validator-JAR + Scenario-Repository
  (validator-configuration-xrechnung) in einen Ordner legen und den
  Pfad in mandant_settings.json unter "kosit_validator_path" eintragen.
- Die Funktion validate_with_kosit() liefert ein strukturiertes Ergebnis
  mit valid/errors/warnings und dem Validator-Report-XML.
- Wenn Java oder das JAR nicht verfügbar sind, wird ein Ergebnis mit
  available=False zurückgegeben — der Aufrufer kann dann auf den
  eingebauten validator.py zurückfallen.

Referenzen:
- Pflichtenheft P-04: "Das System muss jede erzeugte E-Rechnung gegen
  die jeweils gültigen Geschäftsregeln und Syntaxartefakte validieren.
  Das BMF empfiehlt die Validierung ausdrücklich."
- FR-210: Validierung gegen unterstütztes Regelwerk
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Union

from lxml import etree


@dataclass
class KositResult:
    """Ergebnis einer KoSIT-Validierung."""
    available: bool = False          # War der Validator überhaupt aufrufbar?
    valid: bool = False              # Alle Regeln bestanden?
    error_count: int = 0
    warning_count: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    report_xml: str = ""             # Roher Validator-Report
    validator_version: str = ""
    scenario: str = ""
    unavailable_reason: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        # Report-XML kann groß werden; auf Anfrage auslassen
        return d


def _find_validator_jar(explicit_path: Optional[str] = None) -> Optional[Path]:
    """
    Sucht den KoSIT-Validator-JAR. Reihenfolge:
    1. explizit übergebener Pfad
    2. Umgebungsvariable KOSIT_VALIDATOR_PATH
    3. Standardpfade /opt/kosit, ./kosit, ./tools/kosit
    """
    candidates: list[Path] = []
    if explicit_path:
        candidates.append(Path(explicit_path))
    env = os.environ.get("KOSIT_VALIDATOR_PATH")
    if env:
        candidates.append(Path(env))
    candidates += [
        Path("/opt/kosit"),
        Path.cwd() / "kosit",
        Path.cwd() / "tools" / "kosit",
    ]

    for base in candidates:
        if not base.exists():
            continue
        if base.is_file() and base.suffix == ".jar":
            return base
        # Ordner: suche validator*.jar
        for jar in base.glob("validator*.jar"):
            return jar
    return None


def _find_scenarios(validator_jar: Path) -> Optional[Path]:
    """
    Sucht die scenarios.xml des validator-configuration-xrechnung.
    Konvention: liegt im selben Ordner wie der Validator-JAR oder in
    einem Unterordner scenarios/.
    """
    base = validator_jar.parent
    for candidate in [
        base / "scenarios.xml",
        base / "scenarios" / "scenarios.xml",
        base / "configuration" / "scenarios.xml",
    ]:
        if candidate.exists():
            return candidate
    # Glob-Suche
    for s in base.rglob("scenarios.xml"):
        return s
    return None


def is_available(validator_path: Optional[str] = None) -> tuple[bool, str]:
    """
    Prüft, ob der KoSIT-Validator aufrufbar ist.
    Returns (available, reason_if_not).
    """
    if shutil.which("java") is None:
        return False, "Java nicht im PATH gefunden"
    jar = _find_validator_jar(validator_path)
    if jar is None:
        return False, "KoSIT-Validator-JAR nicht gefunden"
    scenarios = _find_scenarios(jar)
    if scenarios is None:
        return False, f"scenarios.xml neben {jar.name} nicht gefunden"
    return True, ""


def validate_with_kosit(
    xml_bytes: bytes,
    validator_path: Optional[str] = None,
    timeout_sec: int = 60,
) -> KositResult:
    """
    Validiert ein XRechnung-XML gegen den offiziellen KoSIT-Validator.

    Args:
        xml_bytes: Das zu prüfende XML (UBL oder CII)
        validator_path: Optionaler Pfad zum Validator-JAR oder -Ordner
        timeout_sec: Timeout für den Java-Prozess

    Returns:
        KositResult mit available/valid/errors/warnings/report_xml
    """
    available, reason = is_available(validator_path)
    if not available:
        return KositResult(available=False, unavailable_reason=reason)

    jar = _find_validator_jar(validator_path)
    scenarios = _find_scenarios(jar)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        input_file = tmp / "invoice.xml"
        output_dir = tmp / "reports"
        output_dir.mkdir()
        input_file.write_bytes(xml_bytes)

        cmd = [
            "java", "-jar", str(jar),
            "-s", str(scenarios),
            "-o", str(output_dir),
            "-r", str(scenarios.parent),  # Repository-Wurzel
            str(input_file),
        ]

        try:
            proc = subprocess.run(
                cmd, capture_output=True, timeout=timeout_sec, check=False,
            )
        except subprocess.TimeoutExpired:
            return KositResult(
                available=True,
                unavailable_reason=f"Validator-Timeout nach {timeout_sec}s",
            )
        except OSError as e:
            return KositResult(
                available=False,
                unavailable_reason=f"Java-Aufruf fehlgeschlagen: {e}",
            )

        # Report-XML einsammeln (Dateinamen-Muster: <input>-report.xml)
        report_files = list(output_dir.glob("*-report.xml"))
        if not report_files:
            return KositResult(
                available=True,
                unavailable_reason="Kein Report erzeugt",
                report_xml=proc.stdout.decode("utf-8", errors="replace"),
            )

        report_bytes = report_files[0].read_bytes()
        return _parse_kosit_report(report_bytes)


def _parse_kosit_report(report_bytes: bytes) -> KositResult:
    """
    Parst das vom KoSIT-Validator erzeugte Report-XML.
    Format: schematron-svrl mit <rep:scenarioMatched>, <rep:validation> etc.
    """
    result = KositResult(available=True, report_xml=report_bytes.decode("utf-8", errors="replace"))

    try:
        root = etree.fromstring(report_bytes)
    except etree.XMLSyntaxError as e:
        result.unavailable_reason = f"Report nicht parsebar: {e}"
        return result

    # Namespaces des KoSIT-Reports
    ns = {
        "rep": "http://www.xoev.de/de/validator/varl/1",
        "svrl": "http://purl.oclc.org/dsdl/svrl",
    }

    # Validator-Version
    engine = root.find(".//rep:engine", ns)
    if engine is not None and engine.text:
        result.validator_version = engine.text.strip()

    # Scenario
    scenario = root.find(".//rep:scenario", ns)
    if scenario is not None:
        name = scenario.find("rep:name", ns)
        if name is not None and name.text:
            result.scenario = name.text.strip()

    # Failed Asserts durchzählen
    for fa in root.findall(".//svrl:failed-assert", ns):
        flag = fa.get("flag", "").lower()
        role = fa.get("role", "").lower()
        severity = flag or role
        text_el = fa.find("svrl:text", ns)
        msg = (text_el.text or "").strip() if text_el is not None else ""
        rule_id = fa.get("id", "")
        entry = f"{rule_id}: {msg}" if rule_id else msg

        if severity in ("fatal", "error"):
            result.errors.append(entry)
        else:
            result.warnings.append(entry)

    # Gesamtergebnis: accept / reject
    accept = root.find(".//rep:accepts", ns)
    if accept is not None and accept.text:
        result.valid = accept.text.strip().lower() == "true"
    else:
        result.valid = len(result.errors) == 0

    result.error_count = len(result.errors)
    result.warning_count = len(result.warnings)
    return result
