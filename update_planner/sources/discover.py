"""Neue Pakete finden: Repo (CachyOS/Arch), AUR und Flatpak – nur lesend, ohne root.

Alle Kandidaten haben dasselbe Format:
    dict(name, source='repo'|'aur'|'flatpak', repo, version, desc, popularity, pop_label, votes, flags, base,
         origin, sec)
`popularity` ist nur innerhalb einer Quelle vergleichbar (Repo: pkgstats-Prozent, AUR: Popularity-Wert,
Flatpak: Installationen im letzten Monat). `sec` ist die Sicherheitsauswertung (siehe `annotate_security`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from urllib.parse import quote

from .. import config
from ..i18n import _, thousands
from ..sysutil import (
    fetch,
    fetch_json,
    have,
    https_client,
    post_json,
    run,
    valid_flatpak,
    valid_name,
)
from . import flatpak, pacman, security

log = logging.getLogger(__name__)

PKGSTATS = 'https://pkgstats.archlinux.de/api/packages?limit=10000&offset={offset}'
PKGSTATS_PAGES = 5                      # ~36 000 Pakete, 10 000 pro Seite (API-Höchstwert)
AUR_SEARCH = 'https://aur.archlinux.org/rpc/v5/search/{term}?by=name-desc'
FLATHUB_POPULAR = 'https://flathub.org/api/v2/collection/popular?page=1&per_page={n}'
FLATHUB_SEARCH = 'https://flathub.org/api/v2/search'
TOP = 50                                # Startliste ohne Suchbegriff (nur Blatt-Pakete, siehe pacman.leaf_packages)
MAX_RESULTS = 200
TERM_RE = re.compile(r'^[\w.+-]{1,64}$')
FEW_VOTES = 10
POP_TTL = 6 * 3600                      # pkgstats im Speicher halten (auf Platte 24 h)

DEMO = False                            # --demo: feste Beispieldaten statt Netz und pacman
_ADVISORIES: list[dict] = []            # vom Katalog gesetzt (Rohdaten des Security Trackers)
_POP: tuple[float, dict[str, float]] | None = None   # (Zeitpunkt, {Paket: Prozent})


def set_advisories(groups: list[dict]) -> None:
    global _ADVISORIES
    _ADVISORIES = groups


def terms(text: str) -> list[str]:
    """Suchbegriff in Wörter zerlegen; nur [\\w.+-] ist erlaubt (kein Regex-Missbrauch)."""
    words = (text or '').split()
    for w in words:
        if not TERM_RE.match(w):
            raise ValueError(_('Ungültiger Suchbegriff: {term}', term=repr(w[:30])))
    return words[:5]


def origin_label(repo: str) -> str:
    return 'CachyOS' if repo.startswith('cachyos') else 'Arch'


def candidate(name: str, source: str, *, repo: str = '', version: str = '', desc: str = '', popularity: float = 0.0,
              pop_label: str = '', votes: int = 0, flags: list[str] | None = None, base: str | None = None) -> dict:
    return dict(name=name, source=source, repo=repo, version=version, desc=desc, popularity=popularity,
                pop_label=pop_label, votes=votes, flags=flags or [], base=base or name,
                origin=origin_label(repo) if source == 'repo' else '', sec=None)


# ------------------------------------------------------------------ Parser

def parse_sl(text: str) -> dict[str, tuple[str, str, bool]]:
    """`pacman -Sl`: 'repo name version [installed]' → {name: (repo, version, installiert)}; erste Fundstelle gewinnt."""
    out: dict[str, tuple[str, str, bool]] = {}
    for line in text.splitlines():
        f = line.split()
        if len(f) >= 3 and f[1] not in out:
            out[f[1]] = (f[0], f[2], '[installed' in line)
    return out


def parse_ss(text: str) -> list[dict]:
    """`pacman -Ss`: Zeile 'repo/name version [installed]' gefolgt von der eingerückten Beschreibung."""
    out: list[dict] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if line[0] in ' \t':
            if out:
                out[-1]['desc'] = (out[-1]['desc'] + ' ' + line.strip()).strip()
            continue
        m = re.match(r'^([^/\s]+)/(\S+) (\S+)(.*)$', line)
        if m:
            out.append(dict(repo=m.group(1), name=m.group(2), version=m.group(3), installed='[installed' in m.group(4),
                            desc=''))
    return out


def parse_pkgstats(pages: list[dict]) -> dict[str, float]:
    pop: dict[str, float] = {}
    for page in pages:
        for p in page.get('packagePopularities', []):
            if isinstance(p.get('name'), str):
                pop[p['name']] = float(p.get('popularity') or 0)
    return pop


def parse_aur_search(data: dict) -> list[dict]:
    out = []
    for r in data.get('results', []):
        name = r.get('Name', '')
        if not re.fullmatch(r'[\w@.+-]+', name) or name[0] in '-.':
            continue
        votes = int(r.get('NumVotes') or 0)
        flags = []
        if not r.get('Maintainer'):
            flags.append(_('verwaist (kein Maintainer)'))
        if r.get('OutOfDate'):
            flags.append(_('als veraltet markiert'))
        if votes < FEW_VOTES:
            flags.append(_('wenige Stimmen'))
        pop = float(r.get('Popularity') or 0)
        out.append(candidate(name, 'aur', repo='aur', version=r.get('Version', ''), desc=r.get('Description') or '',
                             popularity=pop, pop_label=f'{pop:.2f} · {votes} ' + _('Stimmen'), votes=votes,
                             flags=flags, base=r.get('PackageBase') or name))
    return sorted(out, key=lambda c: (-c['popularity'], c['name']))


def parse_flathub(data: dict, skip: set[str] = frozenset()) -> list[dict]:
    out = []
    for h in data.get('hits', []):
        app_id = h.get('app_id') or ''
        if not app_id or app_id in skip or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', app_id):
            continue
        installs = int(h.get('installs_last_month') or 0)
        out.append(candidate(app_id, 'flatpak', repo='flathub', version='', desc=(h.get('name') or app_id)
                             + (' – ' + h['summary'] if h.get('summary') else ''),
                             popularity=float(installs), pop_label=_('{n} Inst./Monat', n=thousands(installs))))
    return sorted(out, key=lambda c: (-c['popularity'], c['name']))


# ------------------------------------------------------------------ Sicherheit

def annotate_security(cands: list[dict], advisories: list[dict]) -> None:
    """Offene Lücken der VERFÜGBAREN Version je Repo-Kandidat (nur `status` Vulnerable bzw. ohne Fix)."""
    repo = {c['name']: c['version'] for c in cands if c['source'] == 'repo'}
    found = security.relevant(advisories, repo, {}) if repo and advisories else []
    by_pkg: dict[str, list[dict]] = {}
    for f in found:
        by_pkg.setdefault(f['package'], []).append(f)
    for c in cands:
        if c['source'] == 'repo':
            items = by_pkg.get(c['name'], [])
            c['sec'] = dict(severity=items[0]['severity'], count=len(items), items=items) if items else \
                dict(severity='', count=0, items=[])
        elif c['source'] == 'flatpak':
            c['sec'] = dict(severity='', count=0, items=[], note=_('keine Tracker-Daten (Sandbox)'))
        else:
            c['sec'] = dict(severity='', count=0, items=[], note=_('Tracker deckt AUR kaum ab – Scanner nutzen'))


# ------------------------------------------------------------------ Repo

def _db() -> list[str]:
    return ['--dbpath', str(config.CHECKUP_DB), '--logfile', '/dev/null']


def _escape(term: str) -> str:
    return re.sub(r'([.+])', r'\\\1', term)


async def popularity() -> dict[str, float]:
    """pkgstats-Beliebtheit aller Pakete in Prozent. Die Antwort ist ~5 MB JSON (Parsen ≈ 60 ms): im Speicher
    `POP_TTL` lang halten statt bei jeder Suche neu zu lesen, Parsen außerhalb der Event-Loop. Fehler → letzter
    bekannter Stand (nächster Versuch in 5 min), sonst Ausnahme."""
    global _POP
    if _POP and time.time() - _POP[0] < POP_TTL:
        return _POP[1]
    try:
        async with https_client() as client:
            blobs = await asyncio.gather(*(fetch(PKGSTATS.format(offset=i * 10000), ttl=86400, client=client)
                                           for i in range(PKGSTATS_PAGES)))
        pop = await asyncio.to_thread(lambda: parse_pkgstats([json.loads(b) for b in blobs]))
    except Exception:
        if _POP:
            _POP = (time.time() - POP_TTL + 300, _POP[1])
            return _POP[1]
        raise
    _POP = (time.time(), pop)
    return pop


async def repo(term: str, warn=None) -> list[dict]:
    if DEMO:
        return _demo('repo', term)
    words = terms(term)
    if words:
        # „--“: ein Suchwort wie „--version“ darf nicht als pacman-Option gelten
        out = await asyncio.to_thread(run, ['pacman', '-Ss', *_db(), '--', *(_escape(w) for w in words)],
                                      timeout=60, ok_codes=(0, 1))
        first: dict[str, dict] = {}                     # dasselbe Paket in mehreren Repos: das mit der höchsten Priorität
        for r in parse_ss(out):
            first.setdefault(r['name'], r)
        found = [r for r in first.values() if not r['installed']]
    else:
        out = await asyncio.to_thread(run, ['pacman', '-Sl', *_db()], timeout=60)
        found = [dict(repo=r, name=n, version=v, desc='') for n, (r, v, inst) in parse_sl(out).items() if not inst]
        try:                                  # Startliste: nur Programme, keine Bibliotheken/Module (nichts hängt davon ab)
            leaves = await asyncio.to_thread(pacman.leaf_packages)
            found = [r for r in found if r['name'] in leaves] or found
        except Exception as exc:  # noqa: BLE001 – ohne Filter zeigt die Liste alles, wie zuvor
            log.warning('Abhängigkeitsfilter nicht verfügbar: %s', exc)
    pop: dict[str, float] = {}
    try:
        pop = await popularity()
    except Exception as exc:  # noqa: BLE001 – Popularität ist Zusatzinformation
        log.warning('pkgstats nicht erreichbar: %s', exc)
        if warn:
            warn(_('Beliebtheit (pkgstats) nicht erreichbar: {err}', err=exc))
    cands = [candidate(r['name'], 'repo', repo=r['repo'], version=r['version'], desc=r['desc'],
                       popularity=pop.get(r['name'], 0.0), pop_label=f'{pop.get(r["name"], 0.0):.1f} %')
             for r in found if re.fullmatch(r'[\w@.+-]+', r['name'])]
    cands.sort(key=lambda c: (-c['popularity'], c['name']))
    cands = cands[:MAX_RESULTS if words else TOP]
    if not words:                         # Beschreibungen der Startliste nachladen
        await asyncio.to_thread(_describe, cands)
    annotate_security(cands, _ADVISORIES)
    return cands


def _describe(cands: list[dict]) -> None:
    if not cands:
        return
    try:
        out = run(['pacman', '-Si', *_db(), *[valid_name(c['name']) for c in cands]], timeout=60, ok_codes=(0, 1))
    except Exception as exc:  # noqa: BLE001
        log.warning('Beschreibungen nicht lesbar: %s', exc)
        return
    desc = {b.get('Name'): b.get('Description', '') for b in pacman.parse_info(out)}
    for c in cands:
        c['desc'] = desc.get(c['name'], c['desc'])


def repo_info(name: str) -> dict[str, str]:
    """Details eines Repo-Pakets (`pacman -Si`) für den Detailbereich."""
    if DEMO:
        c = next((c for c in _demo_data()['repo'] if c['name'] == name), {})
        return {'Name': name, 'Description': c.get('desc', ''), 'Depends On': 'glibc  openssl  zlib'}
    out = run(['pacman', '-Si', *_db(), valid_name(name)], timeout=60, ok_codes=(0, 1))
    blocks = pacman.parse_info(out)
    return blocks[0] if blocks else {}


# ------------------------------------------------------------------ AUR

async def _installed_names() -> set[str]:
    try:
        return set((await asyncio.to_thread(run, ['pacman', '-Qq'], timeout=30)).split())
    except Exception as exc:  # noqa: BLE001 – nur zum Ausblenden gedacht
        log.warning('Installierte Pakete nicht lesbar: %s', exc)
        return set()


def _match_all(cand: dict, words: list[str]) -> bool:
    text = f'{cand["name"]} {cand["desc"]}'.lower()
    return all(w.lower() in text for w in words)


async def aur(term: str) -> list[dict]:
    """Die AUR-Suche kennt nur eine Zeichenfolge (zwei Wörter wären eine Phrase, „yay helper“ fände nichts):
    nach dem längsten Wort suchen und die übrigen lokal in Name/Beschreibung verlangen – wie bei den Repos."""
    if DEMO:
        return _demo('aur', term)
    words = terms(term)
    key = max(words, key=len) if words else ''
    if len(key) < 2:
        return []
    data = await fetch_json(AUR_SEARCH.format(term=quote(key, safe='')), ttl=900)
    if data.get('type') == 'error':
        raise RuntimeError(str(data.get('error', 'AUR'))[:200])
    installed = await _installed_names()                # wie bei Repo und Flatpak: Installiertes nicht anbieten
    cands = [c for c in parse_aur_search(data) if c['name'] not in installed and _match_all(c, words)]
    return cands[:MAX_RESULTS]


# ------------------------------------------------------------------ Flatpak

async def flathub(term: str) -> list[dict]:
    if DEMO:
        return _demo('flatpak', term)
    if not have('flatpak'):
        raise RuntimeError(_('Flatpak ist nicht installiert'))
    words = terms(term)
    installed = {i['id'].split('//')[0] for i in await asyncio.to_thread(flatpak.installed)}
    if words:
        data = await post_json(FLATHUB_SEARCH, {'query': ' '.join(words)})
    else:
        data = await fetch_json(FLATHUB_POPULAR.format(n=TOP + len(installed)), ttl=3600)
    cands = parse_flathub(data, installed)
    cands = cands[:MAX_RESULTS if words else TOP]
    for c in cands:
        valid_flatpak(c['name'])
    annotate_security(cands, [])
    return cands


async def search(source: str, term: str, warn=None) -> list[dict]:
    if source == 'repo':
        return await repo(term, warn)
    if source == 'aur':
        return await aur(term)
    return await flathub(term)


# ------------------------------------------------------------------ Demo

def _demo_data() -> dict[str, list[dict]]:
    from .. import mockdata
    return mockdata.DISCOVER


def _demo(source: str, term: str) -> list[dict]:
    words = [w.lower() for w in terms(term)]
    if source == 'aur' and len(' '.join(words)) < 2:
        return []
    cands = [candidate(**(c | {'flags': [_(f) for f in c.get('flags', [])]})) for c in _demo_data()[source]
             if all(w in (c['name'] + ' ' + c.get('desc', '')).lower() for w in words)]
    for c in cands:
        if source == 'aur':
            c['pop_label'] = f'{c["popularity"]:.2f} · {c["votes"]} ' + _('Stimmen')
        elif source == 'flatpak':
            c['pop_label'] = _('{n} Inst./Monat', n=thousands(int(c['popularity'])))
    cands.sort(key=lambda c: (-c['popularity'], c['name']))
    annotate_security(cands, _ADVISORIES)
    return cands if words else cands[:TOP]
