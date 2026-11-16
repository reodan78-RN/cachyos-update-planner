"""Arch- und CachyOS-News (RSS) mit Erkennung von Meldungen, die manuelles Eingreifen verlangen."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from ..sysutil import fetch

log = logging.getLogger(__name__)

FEEDS = {
    'Arch': 'https://archlinux.org/feeds/news/',
    'CachyOS': 'https://cachyos.org/rss.xml',
}
MANUAL = re.compile(r'manual intervention|requires? (manual|user) (action|intervention)|action required|'
                    r'before upgrading|must be (replaced|removed)', re.I)


async def items(limit: int = 6, ttl: float = 3600) -> list[dict]:
    out = []
    for source, url in FEEDS.items():
        try:
            raw = await fetch(url, ttl=ttl)
            if b'<!DOCTYPE' in raw[:4096].upper() or b'<!ENTITY' in raw.upper():
                raise ValueError('DTD/Entities im Feed – abgelehnt')  # Schutz vor Entity-Expansion
            root = ET.fromstring(raw)  # noqa: S314 – DTD-frei geprüft, ElementTree lädt keine externen Entities
        except Exception as exc:  # noqa: BLE001 – ein fehlender Feed darf die App nicht stoppen
            log.warning('Feed %s nicht lesbar: %s', source, exc)
            continue
        for it in root.iter('item'):
            title = (it.findtext('title') or '').strip()
            desc = re.sub(r'<[^>]+>', ' ', it.findtext('description') or '')
            try:
                date = parsedate_to_datetime(it.findtext('pubDate') or '').date().isoformat()
            except (TypeError, ValueError):
                date = ''
            link = (it.findtext('link') or '').strip()
            link = link if link.startswith('https://') else ''   # keine javascript:/http-Links aus Feeds
            out.append(dict(source=source, title=title[:300], link=link, date=date,
                            manual=bool(MANUAL.search(title) or MANUAL.search(desc[:600]))))
    out.sort(key=lambda n: n['date'], reverse=True)
    return out[:limit * 2]
