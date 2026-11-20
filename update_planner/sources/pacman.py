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
    """Zeitpunkt des letzten vollständigen Systemupdates laut pacman.log.

    Liest die Datei blockweise von hinten – das Protokoll wächst über Jahre auf viele MB, der
    gesuchte Eintrag steht fast immer im letzten Block.
    """
    marker = b'starting full system upgrade'
    block = 256 * 1024
    try:
        with log.open('rb') as f:
            pos = f.seek(0, 2)
            tail = b''
            while pos > 0:
                step = min(block, pos)
                pos -= step
                f.seek(pos)
                buf = f.read(step) + tail
                # die erste (evtl. angeschnittene) Zeile erst mit dem nächsten Block auswerten
                cut = buf.find(b'\n') if pos > 0 else -1
                head, body = (buf[:cut + 1], buf[cut + 1:]) if cut >= 0 else (b'', buf)
                for line in reversed(body.splitlines()):
                    if marker in line:
                        return line[1:20].decode(errors='replace')
                tail = head
            return None
    except OSError:
        return None


HISTORY_RE = re.compile(rb'^\[(\d{4}-\d{2}-\d{2})T[^\]]*\] \[ALPM\] (upgraded|installed|removed|downgraded) ')


def history(days: int = 60, log: Path = Path('/var/log/pacman.log'), max_bytes: int = 16 * 1024 * 1024) -> list[dict]:
    """Paketänderungen pro Tag der letzten `days` Tage: [{date, upgraded, installed, removed}], lückenlos."""
    from datetime import date, timedelta
    end = date.today()
    start = end - timedelta(days=days - 1)
    counts: dict[str, dict[str, int]] = {}
    try:
        with log.open('rb') as f:
            size = f.seek(0, 2)
            f.seek(max(0, size - max_bytes))
            data = f.read()
    except OSError:
        data = b''
    first = start.isoformat().encode()
    for line in data.split(b'\n'):
        m = HISTORY_RE.match(line)
        if not m or m.group(1) < first:
            continue
        kind = {b'downgraded': 'upgraded'}.get(m.group(2), m.group(2).decode())
        counts.setdefault(m.group(1).decode(), {}).setdefault(kind, 0)
        counts[m.group(1).decode()][kind] += 1
    out = []
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        c = counts.get(d, {})
        out.append(dict(date=d, upgraded=c.get('upgraded', 0), installed=c.get('installed', 0), removed=c.get('removed', 0)))
    return out


CACHE_DIR = Path('/var/cache/pacman/pkg')
PACCACHE_RE = re.compile(r'finished dry run: (\d+) candidates \(disk space saved: ([\d.]+) (B|KiB|MiB|GiB|TiB)\)')


def orphans() -> list[str]:
    """Verwaiste Pakete: als Abhängigkeit installiert, aber von keinem Paket mehr benötigt (pacman -Qdtq)."""
    out = run(['pacman', '-Qdtq'], timeout=60, ok_codes=(0, 1))   # 1 = keine Waisen
    return [valid_name(n) for n in out.split()]


def cache_info(keep: int = 2) -> dict:
    """Größe des Paket-Caches und was `paccache -rk<keep>` plus `-ruk0` freigeben würde (nur Probelauf)."""
    size = 0
    try:
        with __import__('os').scandir(CACHE_DIR) as it:
            for e in it:
                if e.is_file(follow_symlinks=False):
                    size += e.stat(follow_symlinks=False).st_size
    except OSError:
        pass
    count, saved = 0, 0.0
    for args in (['-dk', str(int(keep))], ['-duk0']):
        try:
            out = run(['paccache', *args], timeout=180, ok_codes=(0, 1))
        except (CommandError, OSError):
            continue
        m = PACCACHE_RE.search(out)
        if m:
            count += int(m.group(1))
            saved += float(m.group(2)) * {'B': 1 / 1024 ** 3, 'KiB': 1 / 1024 ** 2, 'MiB': 1 / 1024,
                                          'GiB': 1, 'TiB': 1024}[m.group(3)]
    return dict(size_gib=size / 1024 ** 3, candidates=count, saved_gib=saved, keep=int(keep))


def pacnew_files() -> list[str]:
    """Offene .pacnew-Dateien unter /etc (unlesbare Verzeichnisse werden übersprungen)."""
    found = []
    for root, _dirs, files in __import__('os').walk('/etc', onerror=lambda e: None):
        found += [f'{root}/{f}' for f in files if f.endswith('.pacnew')]
    return sorted(found)


def db_locked() -> bool:
    return Path('/var/lib/pacman/db.lck').exists()
