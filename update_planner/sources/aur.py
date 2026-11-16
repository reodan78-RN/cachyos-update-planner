"""AUR: RPC-Metadaten, Update-Erkennung für Fremdpakete, PKGBUILD-Abruf für den Scanner."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx

from .. import config
from ..sysutil import fetch, fetch_json, valid_name, vercmp

RPC = 'https://aur.archlinux.org/rpc/v5/info'
CGIT = 'https://aur.archlinux.org/cgit/aur.git/plain/{file}?h={base}'
VCS_SUFFIX = ('-git', '-svn', '-hg', '-bzr', '-darcs', '-fossil', '-nightly')
MAX_FILE = 512 * 1024


def is_vcs(name: str) -> bool:
    return name.endswith(VCS_SUFFIX)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=config.HTTP_TIMEOUT, follow_redirects=True,
                             headers={'User-Agent': config.USER_AGENT})


async def info(names: list[str], ttl: float = 900) -> dict[str, dict]:
    """RPC-Info für bis zu beliebig viele Pakete (in Blöcken zu 150)."""
    result: dict[str, dict] = {}
    async with _client() as client:
        for i in range(0, len(names), 150):
            chunk = [valid_name(n) for n in names[i:i + 150]]
            url = RPC + '?' + '&'.join('arg[]=' + quote(n) for n in chunk)
            data = await fetch_json(url, ttl=ttl, client=client)
            for r in data.get('results', []):
                result[r['Name']] = r
    return result


def _date(ts: int | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).date().isoformat() if ts else None


def meta_from_rpc(r: dict) -> dict:
    return dict(
        maintainer=r.get('Maintainer'), votes=r.get('NumVotes', 0), popularity=float(r.get('Popularity', 0)),
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


def _install_name(pkgbuild: str) -> str | None:
    m = re.search(r'^\s*install=["\']?([^"\'\s]+)', pkgbuild, re.M)
    if not m:
        return None
    name = m.group(1).replace('${pkgname}', '').replace('$pkgname', '')
    return name if re.fullmatch(r'[\w.+-]+', name) else None


def _old_file(base: str, file: str) -> str:
    """Zuletzt gebaute Fassung aus den Caches von paru, yay oder dem Planer selbst (für den Diff)."""
    home = Path.home()
    for d in (home / '.cache/paru/clone' / base, home / '.cache/yay' / base, config.AUR_BUILD / base):
        p = d / file
        if p.is_file() and p.stat().st_size <= MAX_FILE:
            return p.read_text(errors='replace')
    return ''


async def files(base: str) -> dict[str, tuple[str, str]]:
    """{Datei: (alt, neu)} für PKGBUILD und ggf. .install – neu direkt aus dem AUR."""
    valid_name(base)
    async with _client() as client:
        new_pkgbuild = (await fetch(CGIT.format(file='PKGBUILD', base=quote(base)), ttl=600,
                                    client=client)).decode(errors='replace')[:MAX_FILE]
        out = {'PKGBUILD': (_old_file(base, 'PKGBUILD'), new_pkgbuild)}
        inst = _install_name(new_pkgbuild)
        if inst:
            try:
                text = (await fetch(CGIT.format(file=quote(inst), base=quote(base)), ttl=600,
                                    client=client)).decode(errors='replace')[:MAX_FILE]
                out[inst] = (_old_file(base, inst), text)
            except httpx.HTTPError:
                pass
    return out


async def scan_inputs(names: list[str]) -> dict[str, dict]:
    """Metadaten + Dateien je Paket, parallel (max. 6 gleichzeitig)."""
    rpc = await info(names)
    sem = asyncio.Semaphore(6)

    async def one(name: str):
        r = rpc.get(name)
        if not r:
            return name, None
        meta = meta_from_rpc(r)
        async with sem:
            meta['files'] = await files(meta['base'])
        return name, meta

    pairs = await asyncio.gather(*(one(n) for n in names), return_exceptions=True)
    return {n: m for p in pairs if not isinstance(p, BaseException) for n, m in [p] if m}
