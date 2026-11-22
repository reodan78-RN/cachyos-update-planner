"""Sichere Prozessaufrufe, Versionsvergleich und HTTP – Grundlage aller Datenquellen.

Regeln: nie shell=True, feste Locale (C) für parsebare Ausgaben, Paketnamen werden vor jeder
Übergabe an Befehle validiert, Downloads sind größen- und zeitbegrenzt.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Sequence
from functools import lru_cache

import httpx

from . import config
from .i18n import _

log = logging.getLogger(__name__)

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

STALE: dict[str, float] = {}   # URL → Alter (s) der Cache-Daten, die nach einem Netzfehler geliefert wurden


def host_allowed(url: str | httpx.URL) -> bool:
    """Nur HTTPS zu den bekannten Datenquellen (config.TRUSTED_DOMAINS, samt Subdomains)."""
    u = httpx.URL(str(url))
    host = (u.host or '').lower().rstrip('.')
    return u.scheme == 'https' and any(host == d or host.endswith('.' + d) for d in config.TRUSTED_DOMAINS)


def _require_trusted(url: str | httpx.URL) -> None:
    if not host_allowed(url):
        raise CommandError(_('Unsichere Weiterleitung abgelehnt: {url}', url=str(url)))


async def _check_request(request: httpx.Request) -> None:
    """Vor jeder Anfrage – auch jeder Weiterleitung: nur HTTPS und nur vertrauenswürdige Hosts.
    Ein Redirect auf http:// würde TLS aushebeln, einer auf einen fremden Host das Vertrauen ausweiten."""
    _require_trusted(request.url)


async def _require_https(response: httpx.Response) -> None:
    _require_trusted(response.url)


def https_client() -> httpx.AsyncClient:
    """Gemeinsamer HTTP-Client: TLS-Prüfung, Zeitlimit, höchstens 3 Weiterleitungen, nur HTTPS zu bekannten Hosts."""
    return httpx.AsyncClient(timeout=config.HTTP_TIMEOUT, follow_redirects=True, max_redirects=3,
                             headers={'User-Agent': config.USER_AGENT},
                             event_hooks={'request': [_check_request], 'response': [_require_https]})


def _cache_path(url: str):
    return config.HTTP_CACHE / hashlib.sha256(url.encode()).hexdigest()


def _cache_read(path, ttl: float | None = None) -> tuple[bytes, float] | None:
    """(Inhalt, Alter in s) oder None. Folgt keinen Symlinks, nimmt nur eigene reguläre Dateien bis MAX_DOWNLOAD."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return None
    with os.fdopen(fd, 'rb') as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_size > config.MAX_DOWNLOAD:
            return None
        age = max(0.0, time.time() - st.st_mtime)
        if ttl is not None and age >= ttl:
            return None
        data = f.read(config.MAX_DOWNLOAD + 1)
    return (data, age) if len(data) <= config.MAX_DOWNLOAD else None


def _cache_write(path, data: bytes) -> None:
    """Atomar und privat: eindeutige Temp-Datei (0600, O_EXCL), dann rename; Fehler kosten nur den Cache."""
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.', suffix='.tmp')
    except OSError as exc:
        log.info('HTTP-Cache nicht schreibbar: %s', exc)
        return
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.replace(tmp, path)
    except OSError as exc:
        log.info('HTTP-Cache nicht schreibbar: %s', exc)
        with contextlib.suppress(OSError):
            os.unlink(tmp)


async def _download(client: httpx.AsyncClient, method: str, url: str, **kw) -> bytes:
    async with client.stream(method, url, **kw) as resp:
        resp.raise_for_status()
        data = bytearray()
        async for chunk in resp.aiter_bytes():
            data += chunk
            if len(data) > config.MAX_DOWNLOAD:
                raise CommandError(_('Antwort zu groß: {url}', url=url))
    return bytes(data)


async def _cached(key: str, url: str, ttl: float, client: httpx.AsyncClient | None, method: str, **kw) -> bytes:
    """Gemeinsamer Ablauf von fetch/post_json. Bei Netzfehler wird ein alter Cache geliefert und in STALE
    vermerkt (mit Alter), damit die Oberfläche „nicht aktuell“ anzeigen kann statt veraltete Daten als aktuell."""
    _require_trusted(url)
    config.ensure_dirs()
    path = _cache_path(key)
    hit = _cache_read(path, ttl)
    if hit:
        return hit[0]
    own = client is None
    client = client or https_client()
    try:
        data = await _download(client, method, url, **kw)
        _cache_write(path, data)
        STALE.pop(url, None)
        return data
    except (httpx.HTTPError, CommandError) as exc:
        old = _cache_read(path)
        if old:
            STALE[url] = old[1]
            log.warning('%s nicht erreichbar (%s) – Cache von vor %d min verwendet', url, type(exc).__name__,
                        old[1] // 60)
            return old[0]
        raise
    finally:
        if own:
            await client.aclose()


async def fetch(url: str, *, ttl: float = 3600, client: httpx.AsyncClient | None = None) -> bytes:
    """GET mit TLS-Prüfung, Zeit- und Größenlimit, Cache (ttl Sekunden); bei Netzfehler alter Cache (→ STALE)."""
    return await _cached(url, url, ttl, client, 'GET')


async def fetch_json(url: str, **kw):
    return json.loads(await fetch(url, **kw))


async def post_json(url: str, body: dict, *, ttl: float = 600, client: httpx.AsyncClient | None = None):
    """POST mit JSON-Rumpf (z. B. Flathub-Suche); gleiche Grenzen und derselbe Cache wie `fetch`."""
    payload = json.dumps(body, sort_keys=True)
    return json.loads(await _cached(url + '\n' + payload, url, ttl, client, 'POST', content=payload,
                                    headers={'Content-Type': 'application/json'}))
