"""AUR: RPC-Metadaten, Update-Erkennung für Fremdpakete, PKGBUILD-Abruf für den Scanner."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx

from .. import config
from ..aur_scanner import install_names
from ..sysutil import CommandError, fetch, fetch_json, https_client, valid_name, vercmp

RPC = 'https://aur.archlinux.org/rpc/v5/info'
CGIT = 'https://aur.archlinux.org/cgit/aur.git/plain/{file}?h={base}'
VCS_SUFFIX = ('-git', '-svn', '-hg', '-bzr', '-darcs', '-fossil', '-nightly')
MAX_FILE = 512 * 1024


def is_vcs(name: str) -> bool:
    return name.endswith(VCS_SUFFIX)


def _client() -> httpx.AsyncClient:
    return https_client()


async def info(names: list[str], ttl: float = 900) -> dict[str, dict]:
    """RPC-Info für bis zu beliebig viele Pakete (in Blöcken zu 150)."""
    result: dict[str, dict] = {}
    async with _client() as client:
        for i in range(0, len(names), 150):
            chunk = [valid_name(n) for n in names[i:i + 150]]
            url = RPC + '?' + '&'.join('arg[]=' + quote(n) for n in chunk)
            data = await fetch_json(url, ttl=ttl, client=client)
            results = data.get('results') if isinstance(data, dict) else None
            if not isinstance(results, list):
                raise CommandError('unerwartetes Format der AUR-RPC-Antwort')
            for r in results:
                if isinstance(r, dict) and isinstance(r.get('Name'), str) and isinstance(r.get('Version'), str):
                    result[r['Name']] = r
    return result


def _date(ts) -> str | None:
    try:
        return datetime.fromtimestamp(int(ts), timezone.utc).date().isoformat() if ts else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _num(value, kind=int, default=0):
    try:
        return kind(value)
    except (TypeError, ValueError):
        return default


def meta_from_rpc(r: dict) -> dict:
    return dict(
        maintainer=r.get('Maintainer') if isinstance(r.get('Maintainer'), str) else None,
        votes=_num(r.get('NumVotes')), popularity=_num(r.get('Popularity'), float, 0.0),
        first_submitted=_date(r.get('FirstSubmitted')), last_modified=_date(r.get('LastModified')),
        out_of_date=bool(r.get('OutOfDate')), base=r.get('PackageBase', r['Name']), url=r.get('URL') or '',
        version=r.get('Version', ''), maintainer_changed=None,
    )


def updates(foreign: dict[str, str], rpc: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """(Updates, nicht im AUR gefundene Pakete). VCS-Pakete werden nicht über die Version verglichen."""
    found, missing = [], []
    for name, version in foreign.items():
        r = rpc.get(name)
        if not r:
            missing.append(name)
            continue
        if not is_vcs(name) and vercmp(r['Version'], version) > 0:
            found.append(dict(name=name, old=version, new=r['Version'], base=r.get('PackageBase', name)))
    return found, missing


def _old_file(base: str, file: str) -> str:
    """Zuletzt gebaute Fassung aus den Caches von paru, yay oder dem Planer selbst (für den Diff)."""
    home = Path.home()
    for d in (home / '.cache/paru/clone' / base, home / '.cache/yay' / base, config.AUR_BUILD / base):
        p = d / file
        if p.is_file() and p.stat().st_size <= MAX_FILE:
            return p.read_text(errors='replace')
    return ''


async def files(base: str, client: httpx.AsyncClient | None = None, name: str | None = None) -> dict[str, tuple[str, str]]:
    """{Datei: (alt, neu)} für PKGBUILD und alle per install= referenzierten Dateien – neu direkt aus dem AUR.

    Eine referenzierte, aber nicht abrufbare .install-Datei wird als leerer Inhalt geführt: Der Freigabe-Hash
    passt dann nicht zum gebauten Paket, und der Executor fragt nach, statt eine ungeprüfte Datei durchzulassen."""
    valid_name(base)
    if client is None:
        async with _client() as own:
            return await files(base, own, name)
    new_pkgbuild = (await fetch(CGIT.format(file='PKGBUILD', base=quote(base)), ttl=600,
                                client=client)).decode(errors='replace')[:MAX_FILE]
    out = {'PKGBUILD': (await asyncio.to_thread(_old_file, base, 'PKGBUILD'), new_pkgbuild)}
    for inst in install_names(new_pkgbuild, base, name or base):
        try:
            text = (await fetch(CGIT.format(file=quote(inst), base=quote(base)), ttl=600,
                                client=client)).decode(errors='replace')[:MAX_FILE]
        except httpx.HTTPError:
            text = ''
        out[inst] = (await asyncio.to_thread(_old_file, base, inst), text)
    return out


async def scan_inputs(names: list[str]) -> dict[str, dict]:
    """Metadaten + Dateien je Paket, parallel (max. 6 gleichzeitig, eine gemeinsame HTTP-Verbindung)."""
    rpc = await info(names)
    sem = asyncio.Semaphore(6)

    async with _client() as client:
        async def one(name: str):
            r = rpc.get(name)
            if not r:
                return name, None
            meta = meta_from_rpc(r)
            async with sem:
                meta['files'] = await files(meta['base'], client, name)
            return name, meta

        pairs = await asyncio.gather(*(one(n) for n in names), return_exceptions=True)
    return {n: m for p in pairs if not isinstance(p, BaseException) for n, m in [p] if m}
