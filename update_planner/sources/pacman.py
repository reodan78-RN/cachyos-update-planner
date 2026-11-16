"""Installierte Pakete und Repo-Updates über expac, checkupdates und pacman (nur lesend, ohne root)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from ..sysutil import CommandError, env_c, run, valid_name

SEP = '\x1f'
LIST = '\x1e'


@dataclass
class Installed:
    name: str
    version: str
    size: int                       # Bytes
    base: str
    reason: str                     # explicit | dependency
    depends: list[str]
    provides: list[str]
    required_by: list[str]
    desc: str
    repo: str = 'local'             # Repo laut Sync-DB, 'aur' für Fremdpakete


@dataclass
class RepoUpdate:
    name: str
    old: str
    new: str
    repo: str = ''
    download: float = 0.0           # MiB
    depends: list[str] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)


def _split(v: str) -> list[str]:
    return [x for x in v.split(LIST) if x] if v else []


def installed() -> dict[str, Installed]:
    fmt = SEP.join(['%n', '%v', '%m', '%e', '%w', '%D', '%P', '%N', '%d'])
    out = run(['expac', '-Q', '-l', LIST, fmt], timeout=60)
    pkgs: dict[str, Installed] = {}
    for line in out.split('\n'):  # splitlines() würde auch an \x1e/\x1f trennen
        f = line.split(SEP)
        if len(f) != 9:
            continue
        pkgs[f[0]] = Installed(f[0], f[1], int(f[2] or 0), f[3] or f[0], f[4], _split(f[5]), _split(f[6]),
                               _split(f[7]), f[8])
    # Herkunfts-Repo aus der Sync-DB; was dort fehlt, ist fremd (AUR/lokal gebaut)
    repos = run(['expac', '-S', '%r' + SEP + '%n'], timeout=60)
    origin: dict[str, str] = {}
    for line in repos.split('\n'):
        repo, _, name = line.partition(SEP)
        origin.setdefault(name, repo)  # erste Fundstelle = Repo mit höchster Priorität
    for p in pkgs.values():
        p.repo = origin.get(p.name, 'aur')
    return pkgs


def check_updates() -> list[RepoUpdate]:
    """checkupdates synchronisiert eine private Kopie der Datenbank – das System bleibt unberührt."""
    config.ensure_dirs()
    env = env_c() | {'CHECKUPDATES_DB': str(config.CHECKUP_DB)}
    # Rückgabe 2 = keine Updates
    out = run(['checkupdates', '--nocolor'], timeout=300, env=env, ok_codes=(0, 2))
    updates = []
    for line in out.splitlines():
        m = re.match(r'^(\S+) (\S+) -> (\S+)$', line.strip())
        if m:
            updates.append(RepoUpdate(m.group(1), m.group(2), m.group(3)))
    _enrich(updates)
    return updates


def _enrich(updates: list[RepoUpdate]) -> None:
    """Repo, Download-Größe, Abhängigkeiten und Provides der NEUEN Versionen aus der privaten DB."""
    if not updates:
        return
    by_name = {u.name: u for u in updates}
    names = [valid_name(u.name) for u in updates]
    for i in range(0, len(names), 200):
        try:
            out = run(['pacman', '-Si', '--dbpath', str(config.CHECKUP_DB), '--logfile', '/dev/null',
                       *names[i:i + 200]], timeout=60, ok_codes=(0, 1))
        except CommandError:
            continue
        for block in parse_info(out):
            u = by_name.get(block.get('Name', ''))
            if not u or u.repo:            # erster Block = Repo mit höchster Priorität
                continue
            u.repo = block.get('Repository', '')
            u.download = _mib(block.get('Download Size', '0'))
            u.depends = _list(block.get('Depends On', ''))
            u.provides = _list(block.get('Provides', ''))


def parse_info(text: str) -> list[dict[str, str]]:
    """Parst die Blockausgabe von pacman -Si/-Qi (Fortsetzungszeilen werden angehängt)."""
    blocks, cur, key = [], {}, None
    for line in text.splitlines():
        if not line.strip():
            if cur:
                blocks.append(cur)
            cur, key = {}, None
            continue
        m = re.match(r'^([A-Za-z][A-Za-z ]+?)\s*: (.*)$', line)
        if m and not line.startswith(' '):
            key = m.group(1).strip()
            cur[key] = m.group(2).strip()
        elif key:
            cur[key] += '\n' + line.strip()
    if cur:
        blocks.append(cur)
    return blocks


def _list(v: str) -> list[str]:
    return [] if v.strip() in ('', 'None') else v.split()


def _mib(v: str) -> float:
    m = re.match(r'([\d.]+)\s*(B|KiB|MiB|GiB)', v)
    if not m:
        return 0.0
    return float(m.group(1)) * {'B': 1 / 1048576, 'KiB': 1 / 1024, 'MiB': 1, 'GiB': 1024}[m.group(2)]


def modified_backups(names: list[str]) -> dict[str, list[str]]:
    """Lokal geänderte Konfigurationsdateien je Paket – dort entstehen bei Updates .pacnew-Dateien."""
    result: dict[str, list[str]] = {}
    for i in range(0, len(names), 200):
        out = run(['pacman', '-Qii', *[valid_name(n) for n in names[i:i + 200]]], timeout=60, ok_codes=(0, 1))
        for block in parse_info(out):
            files = [ln.split()[0] for ln in block.get('Backup Files', '').splitlines() if '[modified]' in ln]
            if files:
                result[block['Name']] = files
    return result


def last_upgrade(log: Path = Path('/var/log/pacman.log')) -> str | None:
    """Zeitpunkt des letzten vollständigen Systemupdates laut pacman.log."""
    try:
        last = None
        with log.open(errors='replace') as f:
            for line in f:
                if 'starting full system upgrade' in line:
                    last = line[1:20]
        return last
    except OSError:
        return None


def pacnew_files() -> list[str]:
    """Offene .pacnew-Dateien unter /etc (unlesbare Verzeichnisse werden übersprungen)."""
    found = []
    for root, _dirs, files in __import__('os').walk('/etc', onerror=lambda e: None):
        found += [f'{root}/{f}' for f in files if f.endswith('.pacnew')]
    return sorted(found)


def db_locked() -> bool:
    return Path('/var/lib/pacman/db.lck').exists()
