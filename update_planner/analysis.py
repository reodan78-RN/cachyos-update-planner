"""Bewertung: Risk Score je Update und potenzielle Inkompatibilitäten (Teil-Updates, Neubauten, Konfig)."""

from __future__ import annotations

import re
import time
from collections import defaultdict

from . import config
from .i18n import _
from .sysutil import vercmp, version_parts

DEP_RE = re.compile(r'^([^<>=]+)(?:(<=|>=|=|<|>)(.+))?$')


def split_dep(dep: str) -> tuple[str, str | None, str | None]:
    m = DEP_RE.match(dep.strip())
    return (m.group(1), m.group(2), m.group(3)) if m else (dep, None, None)


def _satisfies(version: str, op: str, want: str) -> bool:
    c = vercmp(version, want)
    return {'=': c == 0, '>=': c >= 0, '<=': c <= 0, '>': c > 0, '<': c < 0}[op]


def _provided(provides: list[str], name: str, op: str | None, want: str | None) -> bool | None:
    """True/False wenn das Paket `name` bereitstellt (mit/ohne Version), None wenn gar nicht."""
    hit = None
    for p in provides:
        pn, _, pv = split_dep(p)
        if pn != name:
            continue
        if not op:
            return True
        hit = bool(pv and _satisfies(pv, op, want))
        if hit:
            return True
    return hit


# ------------------------------------------------------------------ Teil-Update-Gruppen

class _UF:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        self.parent[self.find(a)] = self.find(b)


def together_groups(installed: dict, updates: list) -> tuple[list[list[str]], dict[str, set[str]]]:
    """Welche Repo-Updates nur gemeinsam funktionieren.

    Kante A→B, wenn die NEUE Version von A eine Abhängigkeit hat, die nur die NEUE Version von B erfüllt
    (versionierte Abhängigkeit oder Soname). Liefert Zusammenhangskomponenten (≥2) und je Paket die
    direkt benötigten Updates.
    """
    upd = {u.name: u for u in updates}
    provider: dict[str, list[str]] = defaultdict(list)
    for p in installed.values():
        for prov in p.provides:
            provider[split_dep(prov)[0]].append(p.name)
    needs: dict[str, set[str]] = defaultdict(set)
    uf = _UF()
    for u in updates:
        for dep in u.depends:
            name, op, want = split_dep(dep)
            cands = [name] if name in installed else provider.get(name, [])
            for b in cands:
                if b == u.name or b not in upd:
                    continue
                old_pkg, new_pkg = installed[b], upd[b]
                if b == name:  # echte Paketabhängigkeit
                    ok_old = not op or _satisfies(old_pkg.version, op, want)
                    ok_new = not op or _satisfies(new_pkg.new, op, want)
                else:          # über provides (z. B. libfoo.so=2-64)
                    ok_old = _provided(old_pkg.provides, name, op, want) is not False
                    ok_new = _provided(new_pkg.provides, name, op, want) is not False
                if not ok_old and ok_new:
                    needs[u.name].add(b)
                    uf.union(u.name, b)
    # gleiche pkgbase (Split-Pakete) gehören immer zusammen
    by_base: dict[str, list[str]] = defaultdict(list)
    for u in updates:
        if u.name in installed:
            by_base[installed[u.name].base].append(u.name)
    for names in by_base.values():
        for n in names[1:]:
            uf.union(names[0], n)
    comps: dict[str, list[str]] = defaultdict(list)
    for u in updates:
        comps[uf.find(u.name)].append(u.name)
    groups = sorted((sorted(c) for c in comps.values() if len(c) > 1), key=len, reverse=True)
    return groups, needs


def foreign_rebuilds(installed: dict, updates: list) -> list[tuple[str, str, str]]:
    """Fremdpakete (AUR), deren Soname-/Versionsabhängigkeit nach dem Update nicht mehr erfüllt ist."""
    upd = {u.name: u for u in updates}
    provider: dict[str, list[str]] = defaultdict(list)
    for p in installed.values():
        for prov in p.provides:
            provider[split_dep(prov)[0]].append(p.name)
    out = []
    for p in installed.values():
        if p.repo != 'aur':
            continue
        for dep in p.depends:
            name, op, want = split_dep(dep)
            for b in ([name] if name in upd else [x for x in provider.get(name, []) if x in upd]):
                nb = upd[b]
                ok = (_satisfies(nb.new, op, want) if op else True) if b == name else \
                    _provided(nb.provides, name, op, want) is not False
                if not ok:
                    out.append((p.name, b, dep))
    return out


# ------------------------------------------------------------------ Risk Score

def version_reasons(old: str, new: str) -> list[tuple[str, int]]:
    eo, o = version_parts(old)
    en, n = version_parts(new)
    if en != eo:
        return [(_('Epoch-Wechsel ({old}→{new})', old=eo, new=en), 15)]
    if o[:1] != n[:1]:
        return [(_('Neue Hauptversion ({old} → {new})', old='.'.join(map(str, o[:2])), new='.'.join(map(str, n[:2]))), 20)]
    if o[:2] != n[:2]:
        return [(_('Neue Minor-Version'), 8)]
    if old.rsplit('-', 1)[0] == new.rsplit('-', 1)[0]:
        return [(_('Nur Neubau (pkgrel)'), 1)]
    return [(_('Patch-Release'), 3)]


def repo_risk(u, *, backups: dict[str, list[str]], fixes: dict[str, list[str]], manual_news: list[str]) -> list[tuple[str, int]]:
    reasons = version_reasons(u.old, u.new)
    if config.matches(u.name, config.CRITICAL):
        reasons.append((_('Kernkomponente des Systems'), 15))
    if config.matches(u.name, config.REBOOT):
        reasons.append((_('Neustart nötig'), 10))
    if u.name in backups:
        files = ', '.join(backups[u.name][:2])
        reasons.append((_('.pacnew erwartet (lokal geändert: {files})', files=files), 8))
    for news in manual_news:
        if re.search(rf'(^|[\s(]){re.escape(u.name)}([\s>=<)]|$)', news, re.I):
            reasons.append((_('Arch-News: {title}', title=news[:60]), 25))
    for avg in fixes.get(u.name, []):
        reasons.append((_('Behebt Sicherheitslücke {avg}', avg=avg), -10))
    return reasons


def aur_risk(meta: dict | None, scan) -> list[tuple[str, int]]:
    reasons = [(_('AUR – Build aus Quellcode'), 10)]
    if meta:
        if not meta.get('maintainer'):
            reasons.append((_('AUR – verwaist (kein Maintainer)'), 15))
        if meta.get('out_of_date'):
            reasons.append((_('Als veraltet markiert'), 5))
    if scan and scan.score:
        reasons.append((_('AUR-Scan: {verdict} ({n} Befunde)', verdict=_(scan.verdict[0]), n=len(scan.findings)), round(scan.score * 0.6)))
    return reasons


# ------------------------------------------------------------------ Bekannte Probleme

ISSUE_CAP = 20                 # alle Meldungen zusammen höchstens so viele Punkte (Arch-News-Eingriff: 25)
ISSUE_TRACKER, ISSUE_FORUM = 15, 10
RECENT_DAYS = 21               # ohne Baudatum: so alt darf eine Meldung höchstens sein
# Namen, die auch als gewöhnliches Wort oder Thema vorkommen – nur mit direkt folgender Versionsnummer zählen
GENERIC = frozenset({'linux', 'python', 'pacman', 'which', 'make', 'git', 'sudo', 'wayland', 'file', 'less', 'time',
                     'patch', 'bash', 'tar', 'gzip', 'acl', 'attr', 'zsh', 'fish', 'update', 'kernel', 'firmware',
                     'base', 'core', 'grub', 'mesa-utils', 'perl', 'ruby', 'node', 'rust', 'go', 'java', 'qt', 'gtk'})


def _upstream(version: str) -> str:
    """'1:26.3.1-1' → '26.3.1' (ohne Epoch und pkgrel)."""
    v = version.split(':', 1)[-1]
    return v.rsplit('-', 1)[0] if '-' in v else v


def generic(name: str) -> bool:
    """Kurze oder allgemeine Namen (gcc, linux, python, wayland …) kommen in Titeln oft in anderem Sinn vor."""
    return len(name) < 4 or name in GENERIC


def mentions(title: str, name: str) -> bool:
    """Nennt der Titel das Paket – als eigenes Wort, nicht als Teil eines längeren Namens (mesa ≠ mesa-utils)?
    Kurze oder allgemeine Namen zählen nur mit direkt folgender Versionsnummer („linux 7.3.5“)."""
    pat = rf'(?<![\w.+@/-]){re.escape(name)}(?![\w+@-]|\.\w)'
    if generic(name):
        pat += r'[\s:]*v?\d'
    return re.search(pat, title, re.IGNORECASE) is not None


def mentions_version(title: str, version: str) -> bool:
    """Nennt der Titel genau diese Version (mit oder ohne Epoch/pkgrel)? '26.3.1' passt nicht auf '26.3.10'.
    Bei Snapshot-Versionen wie '16.2.1+r501+g4c5507b' zählt auch der Release-Teil davor ('16.2.1')."""
    up = _upstream(version)
    forms = {up, up.split('+', 1)[0]} - {''}
    return any(re.search(rf'(?<![\w.]){re.escape(f)}(?![\d])', title) for f in forms)


def issue_risk(name: str, base: str, new: str, released: float | None, reports, now: float | None = None
               ) -> tuple[list[tuple[str, int]], list[dict]]:
    """(Gründe für den Risk Score, zugeordnete Meldungen mit Link) für ein Update.

    Eine Meldung gehört zum Update, wenn sie das Paket nennt (Arch GitLab: wenn sie zu dessen pkgbase gehört) und
    entweder die neue Version nennt oder seit dem Bau der neuen Version entstand bzw. aktiv war. Punkte: Fehler-
    Tracker 15, Forum 10, als gelöst markiert 0 – zusammen höchstens ISSUE_CAP, damit eine Fehlzuordnung ein Update
    nicht allein aus der Standardauswahl drängt. Kurze/allgemeine Namen zählen nur, wenn genau die neue Version
    genannt wird („GCC 7.3 kernels“ ist kein gcc-16-Problem)."""
    now = time.time() if now is None else now
    found = []
    for r in reports:
        exact = mentions_version(r.title, new)
        if r.package:
            hit = r.package == base
        else:
            hit = any(mentions(r.title, n) and (exact or not generic(n)) for n in {name, base})
        if not hit:
            continue
        recent = r.date >= released - 86400 if released else now - r.date <= RECENT_DAYS * 86400
        if not (exact or recent):
            continue
        points = 0 if r.solved else ISSUE_TRACKER if r.tracker else ISSUE_FORUM
        found.append(dict(source=r.source, title=r.title, url=r.url, points=points, solved=r.solved, exact=exact,
                          date=time.strftime('%Y-%m-%d', time.localtime(r.date)) if r.date else ''))
    found.sort(key=lambda f: f['date'], reverse=True)               # neueste zuerst …
    found.sort(key=lambda f: (-f['points'], not f['exact']))        # … innerhalb gleicher Gewichtung
    reasons = []
    if found:
        total = min(ISSUE_CAP, sum(f['points'] for f in found))
        reasons.append((_('Bekannte Probleme ({n}): {title}', n=len(found), title=found[0]['title'][:60]), total))
    return reasons, found


def score(reasons: list[tuple[str, int]]) -> int:
    return max(0, min(100, 5 + sum(p for _, p in reasons)))
