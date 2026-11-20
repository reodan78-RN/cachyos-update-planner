"""Arch Linux Security Tracker: welche installierten Pakete sind betroffen, welche Updates beheben etwas."""

from __future__ import annotations

import asyncio
import logging
import re

import httpx

from .. import config
from ..sysutil import fetch_json, vercmp

log = logging.getLogger(__name__)

URL = 'https://security.archlinux.org/issues/all.json'
CVE_URL = 'https://security.archlinux.org/{cve}.json'
SEVERITY = {'Critical': 'kritisch', 'High': 'hoch', 'Medium': 'mittel', 'Low': 'niedrig'}
CVE_RE = re.compile(r'CVE-\d{4}-\d{4,}')


async def advisories(ttl: float = 6 * 3600) -> list[dict]:
    return await fetch_json(URL, ttl=ttl)


def relevant(groups: list[dict], installed: dict[str, str], updates: dict[str, str]) -> list[dict]:
    """installed/updates: {name: version}. Liefert nur Gruppen, die das System aktuell betreffen.

    Neben der eigenen Bewertung (`status`) bleiben die Rohwerte des Trackers erhalten:
    `tracker_status` (Vulnerable, Fixed, Unknown, Testing), `tracker_severity` (Critical … Low, Unknown),
    `fixed` (Version oder None) und `affected`.
    """
    out = []
    for g in groups:
        if g.get('status') == 'Not affected' or not re.fullmatch(r'AVG-\d+', str(g.get('name', ''))):
            continue
        fixed = g.get('fixed')
        for pkg in g.get('packages', []):
            ver = installed.get(pkg)
            if not ver:
                continue
            if fixed and vercmp(ver, fixed) >= 0:
                continue                      # bereits behoben
            if g.get('affected') and vercmp(ver, g['affected']) < 0 and not fixed:
                continue                      # älter als betroffene Version, kein Fix bekannt
            new = updates.get(pkg)
            if fixed and new and vercmp(new, fixed) >= 0:
                status = 'behoben durch Update'
            elif fixed:
                status = 'Fix im Repo – Update nicht verfügbar'
            else:
                status = 'offen – kein Fix'
            out.append(dict(
                id=g['name'], package=pkg, severity=SEVERITY.get(g.get('severity'), 'unbekannt'),
                status=status, title=(g.get('type') or 'unknown').replace('-', ' '),
                fixed_in=fixed or '—', cves=[c for c in g.get('issues', []) if CVE_RE.fullmatch(c)],
                installed=ver, new=new or '',
                tracker_status=g.get('status') or 'Unknown', tracker_severity=g.get('severity') or 'Unknown',
                fixed=fixed, affected=g.get('affected') or '',
                link=f'https://security.archlinux.org/{g["name"]}',
                description='', vector='',
            ))
    order = ['kritisch', 'hoch', 'mittel', 'niedrig', 'unbekannt']
    return sorted(out, key=lambda c: (order.index(c['severity']), c['package']))


async def describe(items: list[dict], ttl: float = 7 * 86400, per_group: int = 2) -> None:
    """Ergänzt `description` und `vector` aus den CVE-Einträgen des Trackers.

    Der Tracker begrenzt die Anfragerate (HTTP 429): höchstens zwei Anfragen gleichzeitig, kurze Pausen,
    Neuversuch nach `Retry-After`. Antworten werden 7 Tage zwischengespeichert, Folgeläufe brauchen also
    kaum Netz. Fehlt eine Beschreibung trotzdem, bleibt das Feld leer – die Anzeige funktioniert weiter.
    """
    details: dict[str, dict] = {}
    sem = asyncio.Semaphore(2)

    async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT, follow_redirects=True,
                                 headers={'User-Agent': config.USER_AGENT}) as client:
        async def one(cve: str) -> None:
            if cve in details:
                return
            async with sem:
                for attempt in range(4):
                    try:
                        data = await fetch_json(CVE_URL.format(cve=cve), ttl=ttl, client=client)
                        if isinstance(data, dict):
                            details[cve] = data
                        return
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code != 429 or attempt == 3:
                            log.info('CVE %s nicht abrufbar: %s', cve, exc)
                            return
                        retry = exc.response.headers.get('Retry-After', '')
                        await asyncio.sleep(min(float(retry) if retry.isdigit() else 1.5 * (attempt + 1), 10))
                    except Exception as exc:  # noqa: BLE001 – einzelne CVEs dürfen fehlen
                        log.info('CVE %s nicht abrufbar: %s', cve, exc)
                        return
                    finally:
                        await asyncio.sleep(0.15)

        # Runde 1: erste CVE jeder Gruppe; Runde 2: weitere CVEs nur, wo noch nichts gefunden wurde
        for rank in range(per_group):
            todo = sorted({it['cves'][rank] for it in items if len(it['cves']) > rank and not _has_desc(it, details)})
            await asyncio.gather(*(one(c) for c in todo))

    for it in items:
        for cve in it['cves'][:per_group]:
            d = details.get(cve)
            if d and d.get('description'):
                it['description'] = str(d['description']).replace('\r', '').strip()[:1200]
                it['vector'] = str(d.get('vector') or '')
                break


def _has_desc(item: dict, details: dict[str, dict]) -> bool:
    return any(details.get(c, {}).get('description') for c in item['cves'])
