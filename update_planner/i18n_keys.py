"""Sammelt alle übersetzbaren Quelltexte (für Tests und Pflege des Katalogs)."""

from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).parent

# Texte, die als interne Schlüssel gespeichert und erst bei der Anzeige mit _() übersetzt werden
DISPLAY_KEYS = [
    'kritisch', 'hoch', 'mittel', 'niedrig', 'info', 'unbekannt',
    'behoben durch Update', 'Fix im Repo – Update nicht verfügbar', 'offen – kein Fix',
    'Gefährlich – nicht installieren', 'Manuell prüfen', 'Auffällig', 'Unbedenklich', 'Gefährlich',
    'Vorbereitung', 'Snapshot', 'Repo', 'AUR', 'Flatpak', 'Abschluss', 'Download', 'Prüfen', 'Installieren', 'Hooks',
    'Übersicht', 'Pakete', 'Updates', 'Konflikte', 'Sicherheit', 'AUR-Scanner',
    'Design: System', 'Design: Dunkel', 'Design: Hell',
]


def keys() -> set[str]:
    found: set[str] = set(DISPLAY_KEYS)
    for path in PKG.rglob('*.py'):
        if path.name in ('mockdata.py', 'aur_samples.py', 'i18n_keys.py') or 'locales' in path.parts:
            continue
        tree = ast.parse(path.read_text(), str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name) and fn.id == '_' and node.args and isinstance(node.args[0], ast.Constant) \
                        and isinstance(node.args[0].value, str):
                    found.add(node.args[0].value)
                if isinstance(fn, ast.Name) and fn.id == 'Rule':
                    # Rule(id, severity, category, title, pattern, advice, ...)
                    for i in (2, 3, 5):
                        if len(node.args) > i and isinstance(node.args[i], ast.Constant):
                            found.add(node.args[i].value)
    return found


if __name__ == '__main__':
    for k in sorted(keys()):
        print(repr(k))
