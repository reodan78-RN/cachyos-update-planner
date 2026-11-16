"""Arch Linux Security Tracker: welche installierten Pakete sind betroffen, welche Updates beheben etwas."""

from __future__ import annotations

import re

from ..sysutil import fetch_json, vercmp

URL = 'https://security.archlinux.org/issues/all.json'
SEVERITY = {'Critical': 'kritisch', 'High': 'hoch', 'Medium': 'mittel', 'Low': 'niedrig'}


async def advisories(ttl: float = 6 * 3600) -> list[dict]:
    return await fetch_json(URL, ttl=ttl)


def relevant(groups: list[dict], installed: dict[str, str], updates: dict[str, str]) -> list[dict]:
    """installed/updates: {name: version}. Liefert nur Gruppen, die das System aktuell betreffen."""
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
                fixed_in=fixed or '—', cves=g.get('issues', []), installed=ver,
                link=f'https://security.archlinux.org/{g["name"]}',
            ))
    order = ['kritisch', 'hoch', 'mittel', 'niedrig', 'unbekannt']
    return sorted(out, key=lambda c: (order.index(c['severity']), c['package']))
