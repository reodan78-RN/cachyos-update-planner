"""Bekannte Probleme zu anstehenden Updates – Meldungen aus drei Quellen.

* Arch-Forum, Unterforum „Pacman & Package Upgrade Issues“ (Atom-Feed, die letzten 50 Threads),
* GitHub-Issues der CachyOS-Paket-Repos (CachyOS-PKGBUILDS, linux-cachyos),
* Arch GitLab: Issues mit Label `scope::bug` der Pakete, für die ein Update ansteht.

Forum und CachyOS sind Sammelabrufe (je eine Anfrage, die Zuordnung zu Paketen geschieht lokal – es verlässt
nichts über das System den Rechner). Arch GitLab wird je pkgbase abgefragt; dabei sieht GitLab, welche Pakete
ein Update bekommen (wie das AUR bei der RPC-Abfrage). Eine Meldung ist kein bestätigter Fehler: Die Bewertung
(analysis.issue_risk) gewichtet nur starke Treffer und ist gedeckelt.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

import httpx

from .. import config
from ..sysutil import CommandError, fetch, host_allowed, https_client, valid_name

log = logging.getLogger(__name__)

FORUM = 'https://bbs.archlinux.org/extern.php?action=feed&fid=44&type=atom&show=50'
CACHYOS = ('https://api.github.com/repos/CachyOS/CachyOS-PKGBUILDS/issues?state=open&per_page=100',
           'https://api.github.com/repos/CachyOS/linux-cachyos/issues?state=open&per_page=100')
GITLAB = ('https://gitlab.archlinux.org/api/v4/projects/archlinux%2Fpackaging%2Fpackages%2F{base}'
          '/issues?state=opened&labels=scope::bug&per_page=50')
MAX_GITLAB = 60          # höchstens so viele pkgbases je Prüfung (Kernkomponenten zuerst)
TTL_FORUM, TTL_CACHYOS, TTL_GITLAB = 3600, 3 * 3600, 6 * 3600
# Titel, die Wünsche statt Probleme sind (neue Version, Feature, Paketanfrage)
NOT_A_PROBLEM = re.compile(r'^\s*\[(update|feature|feat|request|enhancement|package request)\b', re.I)
SKIP_LABELS = {'out-of-date', 'enhancement', 'feature', 'question', 'duplicate', 'invalid', 'wontfix'}
ATOM = '{http://www.w3.org/2005/Atom}'
NAME_OK = re.compile(r'[a-zA-Z0-9@_+][a-zA-Z0-9@._+-]{0,127}')      # wie sysutil.valid_name


@dataclass(frozen=True)
class Report:
    source: str            # 'Arch-Forum' | 'CachyOS' | 'Arch GitLab'
    title: str
    url: str
    date: float            # Unix-Zeit: Erstellung (Tracker) bzw. letzte Aktivität (Forum)
    tracker: bool          # Fehler-Tracker (stärker gewichtet) statt Forum
    solved: bool = False   # im Forum als [SOLVED] markiert
    package: str = ''      # nur Arch GitLab: pkgbase, zu dem das Issue gehört (sonst Zuordnung über den Titel)


def _ts(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (AttributeError, ValueError):
        return 0.0


def _link(url) -> str:
    """Nur HTTPS-Links zu den bekannten Hosts landen in der Oberfläche."""
    return url if isinstance(url, str) and url.startswith('https://') and host_allowed(url) else ''


def _clean(title) -> str:
    return re.sub(r'\s+', ' ', html.unescape(str(title or ''))).strip()[:300]


def parse_forum(raw: bytes) -> list[Report]:
    if b'<!DOCTYPE' in raw[:4096].upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('DTD/Entities im Feed – abgelehnt')       # Schutz vor Entity-Expansion
    root = ET.fromstring(raw)  # noqa: S314 – DTD-frei geprüft, ElementTree lädt keine externen Entities
    out = []
    for e in root.iter(ATOM + 'entry'):
        title = _clean(e.findtext(ATOM + 'title'))
        link = next((x.get('href') for x in e.iter(ATOM + 'link') if x.get('rel', 'alternate') == 'alternate'), '')
        if title:
            out.append(Report('Arch-Forum', title, _link(link), _ts(e.findtext(ATOM + 'updated') or ''), False,
                              solved=bool(re.search(r'\[\s*solved\s*\]', title, re.I))))
    return out


def parse_github(data) -> list[Report]:
    if not isinstance(data, list):
        raise CommandError('unerwartetes Format der GitHub-Antwort')
    out = []
    for i in data:
        if not isinstance(i, dict) or 'pull_request' in i or not isinstance(i.get('title'), str):
            continue
        labels = {str(lb.get('name', '')).lower() for lb in i.get('labels') or [] if isinstance(lb, dict)}
        title = _clean(i['title'])
        if labels & SKIP_LABELS or NOT_A_PROBLEM.match(title):
            continue
        out.append(Report('CachyOS', title, _link(i.get('html_url')), _ts(i.get('created_at') or ''), True))
    return out


def parse_gitlab(data, base: str) -> list[Report]:
    if not isinstance(data, list):
        raise CommandError('unerwartetes Format der GitLab-Antwort')
    out = []
    for i in data:
        if not isinstance(i, dict) or not isinstance(i.get('title'), str):
            continue
        labels = {str(x) for x in i.get('labels') or []}
        if 'scope::bug' not in labels:
            continue
        out.append(Report('Arch GitLab', _clean(i['title']), _link(i.get('web_url')), _ts(i.get('created_at') or ''),
                          True, package=base))
    return out


async def collect(gitlab_bases: list[str], warn=None) -> list[Report]:
    """Alle Meldungen; eine nicht erreichbare Quelle fehlt nur (Hinweis über `warn`), der Rest bleibt."""
    reports: list[Report] = []
    async with https_client() as client:
        try:
            reports += parse_forum(await fetch(FORUM, ttl=TTL_FORUM, client=client))
        except (httpx.HTTPError, CommandError, ValueError, ET.ParseError) as exc:
            _note(warn, 'Arch-Forum', exc)
        for url in CACHYOS:
            try:
                reports += parse_github(json.loads(await fetch(url, ttl=TTL_CACHYOS, client=client)))
            except (httpx.HTTPError, CommandError, ValueError) as exc:
                _note(warn, 'CachyOS (GitHub)', exc)
        sem = asyncio.Semaphore(4)

        async def one(base: str) -> list[Report]:
            async with sem:
                try:
                    raw = await fetch(GITLAB.format(base=quote(valid_name(base), safe='')), ttl=TTL_GITLAB, client=client)
                    return parse_gitlab(json.loads(raw), base)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 404:       # 404: Paket nicht bei Arch (z. B. nur CachyOS)
                        log.info('Arch GitLab %s: %s', base, exc)
                except (httpx.HTTPError, CommandError, ValueError) as exc:
                    log.info('Arch GitLab %s: %s', base, exc)
                return []
        valid = {b for b in gitlab_bases if isinstance(b, str) and NAME_OK.fullmatch(b)}    # vor der Begrenzung filtern
        bases = sorted(valid, key=lambda b: (not config.matches(b, config.CRITICAL), b))[:MAX_GITLAB]
        for found in await asyncio.gather(*(one(b) for b in bases)):
            reports += found
    return reports


def _note(warn, source: str, exc: Exception) -> None:
    log.warning('Bekannte Probleme: %s nicht abrufbar: %s', source, exc)
    if warn:
        warn(source)
