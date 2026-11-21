"""Sichere Prozessaufrufe, Versionsvergleich und HTTP – Grundlage aller Datenquellen.

Regeln: nie shell=True, feste Locale (C) für parsebare Ausgaben, Paketnamen werden vor jeder
Übergabe an Befehle validiert, Downloads sind größen- und zeitbegrenzt.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Sequence
from functools import lru_cache

import httpx

from . import config
from .i18n import _

NAME_RE = re.compile(r'^[a-zA-Z0-9@_+][a-zA-Z0-9@._+-]*$')        # pacman/AUR-Paketnamen
FLATPAK_RE = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]*(//[A-Za-z0-9_.-]+)?')    # Flatpak-IDs (optional //branch), nie mit „-“ beginnend


class CommandError(RuntimeError):
    pass


def valid_name(name: str) -> str:
    if not NAME_RE.match(name) or len(name) > 128:
        raise ValueError(_('Ungültiger Paketname: {name}', name=repr(name)))
    return name


def valid_flatpak(ref: str) -> str:
    if not FLATPAK_RE.fullmatch(ref) or len(ref) > 255:             # fullmatch: „$“ ließe ein angehängtes \n durch
        raise ValueError(_('Ungültige Flatpak-ID: {ref}', ref=repr(ref)))
    return ref


def env_c() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(('LC_', 'LANG'))}
    env.update(LC_ALL='C', LANG='C')
    return env


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def run(cmd: Sequence[str], *, timeout: float = 120, check: bool = True, env: dict | None = None,
        ok_codes: Sequence[int] = (0,)) -> str:
    """Synchroner Aufruf ohne Shell; gibt stdout zurück."""
    proc = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout,
                          env=env or env_c(), stdin=subprocess.DEVNULL)
    if check and proc.returncode not in ok_codes:
        raise CommandError(_('{cmd} beendet mit {rc}', cmd=cmd[0], rc=proc.returncode) + f': {proc.stderr.strip()[:400]}')
    return proc.stdout


async def run_async(cmd: Sequence[str], **kw) -> str:
    return await asyncio.to_thread(run, cmd, **kw)


def _isdigit(c: str) -> bool:
    return '0' <= c <= '9'


def _isalpha(c: str) -> bool:
    return 'a' <= c <= 'z' or 'A' <= c <= 'Z'


def _rpmvercmp(a: str, b: str) -> int:
    """Segmentvergleich wie rpmvercmp() in libalpm (C-Locale, nur ASCII)."""
    if a == b:
        return 0
    i = j = 0          # Position im aktuellen Segment
    p1 = p2 = 0        # Ende des vorherigen Segments
    la, lb = len(a), len(b)
    while i < la and j < lb:
        while i < la and not (_isdigit(a[i]) or _isalpha(a[i])):
            i += 1
        while j < lb and not (_isdigit(b[j]) or _isalpha(b[j])):
            j += 1
        if i >= la or j >= lb:
            break
        if i - p1 != j - p2:                    # unterschiedlich lange Trennzeichen
            return -1 if i - p1 < j - p2 else 1
        p1, p2 = i, j
        isnum = _isdigit(a[p1])
        same = _isdigit if isnum else _isalpha
        while p1 < la and same(a[p1]):
            p1 += 1
        while p2 < lb and same(b[p2]):
            p2 += 1
        s1, s2 = a[i:p1], b[j:p2]
        if not s2:                              # Segmenttypen verschieden
            return 1 if isnum else -1
        if isnum:
            s1, s2 = s1.lstrip('0'), s2.lstrip('0')
            if len(s1) != len(s2):
                return 1 if len(s1) > len(s2) else -1
        if s1 != s2:
            return -1 if s1 < s2 else 1
        i, j = p1, p2
    if i >= la and j >= lb:
        return 0
    # ein übrig gebliebenes Alpha-Segment verliert gegen einen leeren Rest (1.0a < 1.0)
    if (i >= la and not (j < lb and _isalpha(b[j]))) or (i < la and _isalpha(a[i])):
        return -1
    return 1


def _parse_evr(evr: str) -> tuple[str, str, str | None]:
    k = 0
    while k < len(evr) and _isdigit(evr[k]):
        k += 1
    if k < len(evr) and evr[k] == ':':
        epoch, rest = evr[:k] or '0', evr[k + 1:]
    else:
        epoch, rest = '0', evr
    version, sep, release = rest.rpartition('-')
    return (epoch, version, release) if sep else (epoch, rest, None)


@lru_cache(maxsize=16384)
def vercmp(a: str, b: str) -> int:
    """pacman-Versionsvergleich (<0, 0, >0) – Portierung von alpm_pkg_vercmp().

    Früher wurde pro Vergleich das Werkzeug `vercmp` gestartet; beim Abgleich mit dem Security
    Tracker waren das Tausende Prozesse pro Aktualisierung.
    """
    if a == b:
        return 0
    e1, v1, r1 = _parse_evr(a)
    e2, v2, r2 = _parse_evr(b)
    ret = _rpmvercmp(e1, e2) or _rpmvercmp(v1, v2)
    if ret == 0 and r1 is not None and r2 is not None:
        ret = _rpmvercmp(r1, r2)
    return ret


def version_parts(version: str) -> tuple[int, list[int]]:
    """(epoch, numerische Hauptbestandteile) für die Einordnung Major/Minor/Patch."""
    epoch = 0
    if ':' in version:
        e, version = version.split(':', 1)
        epoch = int(e) if e.isdigit() else 0
    upstream = version.rsplit('-', 1)[0]
    nums = [int(x) for x in re.findall(r'\d+', upstream.split('+')[0])[:3]]
    return epoch, nums


# ------------------------------------------------------------------ HTTP mit Datei-Cache

async def _require_https(response: httpx.Response) -> None:
    """Auch nach Weiterleitungen nur HTTPS: ein Redirect auf http:// würde TLS aushebeln."""
    if response.url.scheme != 'https':
        raise CommandError(_('Unsichere Weiterleitung abgelehnt: {url}', url=str(response.url)))


def https_client() -> httpx.AsyncClient:
    """Gemeinsamer HTTP-Client: TLS-Prüfung, Zeitlimit, höchstens 3 Weiterleitungen, nur HTTPS."""
    return httpx.AsyncClient(timeout=config.HTTP_TIMEOUT, follow_redirects=True, max_redirects=3,
                             headers={'User-Agent': config.USER_AGENT}, event_hooks={'response': [_require_https]})


def _cache_path(url: str):
    return config.HTTP_CACHE / hashlib.sha256(url.encode()).hexdigest()


async def fetch(url: str, *, ttl: float = 3600, client: httpx.AsyncClient | None = None) -> bytes:
    """GET mit TLS-Prüfung, Zeit- und Größenlimit, Cache (ttl Sekunden); bei Netzfehler alter Cache."""
    config.ensure_dirs()
    path = _cache_path(url)
    if path.exists() and time.time() - path.stat().st_mtime < ttl:
        return path.read_bytes()
    own = client is None
    client = client or https_client()
    try:
        async with client.stream('GET', url) as resp:
            resp.raise_for_status()
            data = bytearray()
            async for chunk in resp.aiter_bytes():
                data += chunk
                if len(data) > config.MAX_DOWNLOAD:
                    raise CommandError(_('Antwort zu groß: {url}', url=url))
        tmp = path.with_suffix('.tmp')
        tmp.write_bytes(data)
        tmp.replace(path)
        return bytes(data)
    except (httpx.HTTPError, CommandError):
        if path.exists():
            return path.read_bytes()
        raise
    finally:
        if own:
            await client.aclose()


async def fetch_json(url: str, **kw):
    return json.loads(await fetch(url, **kw))


async def post_json(url: str, body: dict, *, ttl: float = 600, client: httpx.AsyncClient | None = None):
    """POST mit JSON-Rumpf (z. B. Flathub-Suche); gleiche Grenzen und derselbe Cache wie `fetch`."""
    config.ensure_dirs()
    payload = json.dumps(body, sort_keys=True)
    path = _cache_path(url + '\n' + payload)
    if path.exists() and time.time() - path.stat().st_mtime < ttl:
        return json.loads(path.read_bytes())
    own = client is None
    client = client or https_client()
    try:
        async with client.stream('POST', url, content=payload, headers={'Content-Type': 'application/json'}) as resp:
            resp.raise_for_status()
            data = bytearray()
            async for chunk in resp.aiter_bytes():
                data += chunk
                if len(data) > config.MAX_DOWNLOAD:
                    raise CommandError(_('Antwort zu groß: {url}', url=url))
        tmp = path.with_suffix('.tmp')
        tmp.write_bytes(data)
        tmp.replace(path)
        return json.loads(bytes(data))
    except (httpx.HTTPError, CommandError):
        if path.exists():
            return json.loads(path.read_bytes())
        raise
    finally:
        if own:
            await client.aclose()
