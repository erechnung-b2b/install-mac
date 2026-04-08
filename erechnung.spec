# -*- mode: python ; coding: utf-8 -*-
"""
E-Rechnungssystem – PyInstaller Build-Spezifikation

Erzeugt eine Standalone-Anwendung im Ordner dist/erechnung/
Enthält alle Python-Module, Templates und statische Dateien.

Build-Befehl:
    pyinstaller erechnung.spec
"""

import os

# Alle Python-Module des Projekts
python_modules = [
    'models.py',
    'xrechnung_generator.py',
    'xrechnung_parser.py',
    'validator.py',
    'inbox.py',
    'viewer.py',
    'wf_engine.py',
    'export.py',
    'archive.py',
    'mandant.py',
    'zugferd.py',
    'dashboard.py',
    'email_handler.py',
    'notifications.py',
    'advanced.py',
    'girocode.py',
    'webapp.py',
    'persistence.py',
    'demo.py',
    'api.py',
]

# Datendateien die mitgeliefert werden
datas = [
    ('static', 'static'),           # HTML-Frontend
    ('requirements.txt', '.'),       # Für Referenz
]

# Versteckte Imports die PyInstaller nicht automatisch findet
hidden_imports = [
    'lxml',
    'lxml.etree',
    'lxml._elementpath',
    'flask',
    'flask.json',
    'jinja2',
    'markupsafe',
    'werkzeug',
    'werkzeug.serving',
    'werkzeug.debug',
    'qrcode',
    'qrcode.image.svg',
    'email',
    'email.mime',
    'email.mime.text',
    'email.mime.multipart',
    'email.mime.base',
    'imaplib',
    'smtplib',
    'decimal',
    'hashlib',
    'uuid',
    'copy',
    'shutil',
    'base64',
    'csv',
    'json',
    'pathlib',
    'threading',
    'webbrowser',
    # Alle Projekt-Module
    'models',
    'xrechnung_generator',
    'xrechnung_parser',
    'validator',
    'inbox',
    'viewer',
    'wf_engine',
    'export',
    'archive',
    'mandant',
    'zugferd',
    'dashboard',
    'email_handler',
    'notifications',
    'advanced',
    'girocode',
    'webapp',
    'demo',
    'api',
]

a = Analysis(
    ['run.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'numpy', 'scipy', 'pandas',
        'PIL', 'pytest', 'unittest',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='E-Rechnungssystem',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # Konsolenfenster für Server-Output
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='erechnung',
)
