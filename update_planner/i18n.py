"""Übersetzung der Oberfläche passend zur Systemsprache.

Quelltexte sind deutsch; `_()` liefert die Übersetzung für die aktive Sprache (derzeit Deutsch und
Englisch, alle anderen Sprachen fallen auf Englisch zurück). Platzhalter werden über str.format befüllt:
    _('{n} Pakete', n=3)
Interne Schlüssel (Schweregrade, Status) bleiben deutsch und werden erst bei der Anzeige übersetzt.
"""

from __future__ import annotations

import configparser
import os
from pathlib import Path

SUPPORTED = ('de', 'en')
FALLBACK = 'en'
LANG = FALLBACK
_catalog: dict[str, str] = {}


def detect() -> str:
    """Sprache wie Desktop-Programme: LANGUAGE > LC_ALL > LC_MESSAGES > LANG > KDE-Einstellung."""
    forced = os.environ.get('UPDATE_PLANNER_LANG')
    candidates = [forced] if forced else []
    language = os.environ.get('LANGUAGE', '')
    candidates += language.split(':') if language else []
    candidates += [os.environ.get(k, '') for k in ('LC_ALL', 'LC_MESSAGES', 'LANG')]
    kde = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'plasma-localerc'
    if kde.is_file():
        cp = configparser.ConfigParser(interpolation=None)
        try:
            cp.read(kde)
            candidates.append(cp.get('Translations', 'LANGUAGE', fallback='').split(':')[0])
        except configparser.Error:
            pass
    for c in candidates:
        code = (c or '').split('.')[0].split('_')[0].split('-')[0].lower()
        if code in ('c', 'posix', ''):
            continue
        return code if code in SUPPORTED else FALLBACK
    return FALLBACK


def set_language(lang: str | None = None) -> str:
    global LANG, _catalog
    LANG = lang if lang in SUPPORTED else detect()
    if LANG == 'de':
        _catalog = {}
    else:
        from .locales import en
        _catalog = en.MESSAGES
    return LANG


def _(text: str, **kw) -> str:
    out = _catalog.get(text, text)
    return out.format(**kw) if kw else out


def quasar_language() -> str:
    return 'de' if LANG == 'de' else 'en-US'


def date_format() -> str:
    return '%d.%m.%Y' if LANG == 'de' else '%Y-%m-%d'


set_language()
