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
FLATPAK_RE = re.compile(r'^[A-Za-z0-9_.-]+(//[A-Za-z0-9_.-]+)?$')    # Flatpak-IDs (optional //branch)


class CommandError(RuntimeError):
    pass


def valid_name(name: str) -> str:
    if not NAME_RE.match(name) or len(name) > 128:
        raise ValueError(_('Ungültiger Paketname: {name}', name=repr(name)))
    return name


def valid_flatpak(ref: str) -> str:
    if not FLATPAK_RE.match(ref) or len(ref) > 255:
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


@lru_cache(maxsize=8192)
def vercmp(a: str, b: str) -> int:
    """pacman-Versionsvergleich (<0, 0, >0) über das Original-Werkzeug."""
    if a == b:
        return 0
    out = run(['vercmp', a, b], timeout=10)
    return int(out.strip())


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

def _cache_path(url: str):
    return config.HTTP_CACHE / hashlib.sha256(url.encode()).hexdigest()


async def fetch(url: str, *, ttl: float = 3600, client: httpx.AsyncClient | None = None) -> bytes:
    """GET mit TLS-Prüfung, Zeit- und Größenlimit, Cache (ttl Sekunden); bei Netzfehler alter Cache."""
    config.ensure_dirs()
    path = _cache_path(url)
    if path.exists() and time.time() - path.stat().st_mtime < ttl:
        return path.read_bytes()
    own = client is None
    client = client or httpx.AsyncClient(timeout=config.HTTP_TIMEOUT, follow_redirects=True,
                                         headers={'User-Agent': config.USER_AGENT})
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
