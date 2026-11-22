"""Aktueller Systemstand für die Oberfläche – echt (Standard) oder Demo (--demo).

Die Seiten lesen die Modul-Attribute (PACKAGES, UPDATES, …). `refresh()` baut einen neuen Stand im
Hintergrund und tauscht ihn atomar aus; VERSION zählt hoch, damit offene Seiten neu zeichnen.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime
from urllib.parse import urlsplit

from . import analysis, aur_scanner, sysutil
from .i18n import _
from .sources import aur, discover, flatpak, news, pacman, security, system

log = logging.getLogger(__name__)

SOURCES = {
    'repo': {'label': 'Repo', 'icon': 'inventory_2', 'color': '#1e93c6'},
    'aur': {'label': 'AUR', 'icon': 'construction', 'color': '#ed8440'},
    'flatpak': {'label': 'Flatpak', 'icon': 'widgets', 'color': '#6a5c9e'},
}

PACKAGES: list[dict] = []
UPDATES: list[dict] = []
CONFLICTS: list[dict] = []
CVES: list[dict] = []
ADVISORIES: list[dict] = []           # Rohdaten des Security Trackers (für Kandidaten neuer Pakete)
NEWS: list[dict] = []
SCANS: dict = {}
AUR_META: dict[str, dict] = {}
GROUPS: list[list[str]] = []          # Repo-Updates, die nur gemeinsam funktionieren
SYSTEM: dict = {}
STATUS: dict = {'state': 'idle', 'step': '', 'warnings': [], 'loaded_at': None, 'demo': False}
VERSION = 0

_index: dict[str, dict] = {}
_upd_index: dict[str, dict] = {}
_dependents: dict[str, list[str]] = {}
_vuln_index: dict[str, dict] = {}
_lock = asyncio.Lock()


# ------------------------------------------------------------------ Zugriff für die Seiten

def package(name: str) -> dict | None:
    return _index.get(name)


def update(name: str) -> dict | None:
    return _upd_index.get(name)


SEVERITY_RANK = {'kritisch': 4, 'hoch': 3, 'mittel': 2, 'niedrig': 1, 'unbekannt': 0}


def vulnerability(name: str) -> dict | None:
    """Höchster Schweregrad und Anzahl der Tracker-Einträge für ein installiertes Paket, sonst None."""
    return _vuln_index.get(name)


def dependents(name: str) -> list[str]:
    return _dependents.get(name, [])


def risk_level(score: int) -> tuple[str, str]:
    if score >= 60:
        return 'hoch', 'negative'
    if score >= 30:
        return 'mittel', 'warning'
    return 'niedrig', 'positive'


def group_of(name: str) -> list[str]:
    return next((g for g in GROUPS if name in g), [name])


def ready() -> bool:
    return STATUS['state'] == 'ready'


def _publish(data: dict) -> None:
    global PACKAGES, UPDATES, CONFLICTS, CVES, NEWS, SCANS, AUR_META, GROUPS, SYSTEM, ADVISORIES, VERSION
    global _index, _upd_index, _dependents, _vuln_index
    PACKAGES, UPDATES, CONFLICTS = data['packages'], data['updates'], data['conflicts']
    CVES, NEWS, SCANS, AUR_META = data['cves'], data['news'], data['scans'], data['aur_meta']
    GROUPS, SYSTEM = data['groups'], data['system']
    ADVISORIES = data.get('advisories', [])
    discover.set_advisories(ADVISORIES)
    _index = {p['name']: p for p in PACKAGES}
    _upd_index = {u['name']: u for u in UPDATES}
    vuln: dict[str, dict] = {}
    for c in CVES:
        v = vuln.setdefault(c['package'], {'severity': 'unbekannt', 'count': 0, 'ids': []})
        v['count'] += 1
        v['ids'].append(c['id'])
        if SEVERITY_RANK.get(c['severity'], 0) > SEVERITY_RANK[v['severity']]:
            v['severity'] = c['severity']
    _vuln_index = vuln
    deps: dict[str, list[str]] = {}
    for p in PACKAGES:
        for d in p['deps']:
            deps.setdefault(d, []).append(p['name'])
    _dependents = deps
    VERSION += 1


# ------------------------------------------------------------------ Demo

def load_demo() -> None:
    from . import aur_samples, mockdata
    discover.DEMO = True
    _publish(dict(
        advisories=mockdata.ADVISORIES,
        packages=mockdata.PACKAGES, updates=mockdata.UPDATES, conflicts=mockdata.CONFLICTS,
        cves=[_demo_cve(c) for c in mockdata.CVES],
        news=[dict(n, source='Demo', link='', new=True) for n in mockdata.NEWS],
        scans=mockdata.SCANS, aur_meta=aur_samples.AUR_META, groups=[['linux-cachyos', 'linux-cachyos-nvidia'], ['mesa', 'llvm-libs']],
        system=dict(os='CachyOS (Demo)', kernel='6.17.7-1-cachyos', fs='btrfs', snapper=True, snap_pac=False,
                    limine_snapper=False, free_gib=120.0, reboot_pending=False, aur_helper='paru',
                    last_upgrade='2026-11-10T08:12:00', pacnew=['/etc/locale.gen.pacnew'], history=_demo_history(),
                    orphans=['python-setuptools-scm', 'lib32-libva-mesa-driver', 'qt5-tools'],
                    cache=dict(size_gib=14.2, candidates=842, saved_gib=5.6, keep=2)),
    ))
    STATUS.update(state='ready', step='', demo=True, loaded_at=time.time(), warnings=[])


def _demo_cve(c: dict) -> dict:
    fixed = None if c['fixed_in'] == '—' else c['fixed_in']
    sev = {'kritisch': 'Critical', 'hoch': 'High', 'mittel': 'Medium', 'niedrig': 'Low'}.get(c['severity'], 'Unknown')
    return dict(c, cves=[c['id']], link='', installed=c.get('installed', ''), new=fixed or '', fixed=fixed,
                tracker_status='Vulnerable', tracker_severity=sev, affected='', vector='Remote',
                description=c.get('description', ''))


def _demo_history() -> list[dict]:
    from datetime import date, timedelta
    pattern = [0, 0, 3, 0, 0, 0, 41, 0, 0, 2, 0, 0, 0, 0, 118, 0, 0, 0, 6, 0, 0, 0, 73, 0, 0, 1, 0, 0, 0, 0]
    start = date.today() - timedelta(days=59)
    return [dict(date=(start + timedelta(days=i)).isoformat(), upgraded=pattern[i % 30], installed=1 if i % 17 == 0 else 0,
                 removed=0) for i in range(60)]


# ------------------------------------------------------------------ echter Systemstand

_pending = False


async def refresh() -> None:
    """Neuen Systemstand laden. Läuft bereits eine Prüfung, wird genau eine weitere nachgeholt –
    so geht z. B. die Aktualisierung nach einem Update-Lauf nicht verloren."""
    global _pending
    if STATUS.get('demo'):
        return
    if _lock.locked():
        _pending = True
        return
    async with _lock:
        while True:
            _pending = False
            STATUS.update(state='loading', warnings=[])
            try:
                _publish(await _collect())
                STATUS.update(state='ready', step='', loaded_at=time.time())
            except Exception as exc:  # noqa: BLE001 – Fehler sichtbar machen statt die App zu beenden
                log.exception('Systemstand konnte nicht geladen werden')
                STATUS.update(state='error', step=str(exc)[:300])
            if not _pending:
                break


def _step(text: str) -> None:
    STATUS['step'] = text


def _warn(text: str) -> None:
    STATUS['warnings'].append(text)
    log.warning(text)


async def _collect() -> dict:
    _step(_('Installierte Pakete lesen …'))
    sysutil.STALE.clear()
    inst = await asyncio.to_thread(pacman.installed)
    sysinfo = await asyncio.to_thread(system.info)
    # pacman.log kann viele MB groß sein – nicht im Event-Loop lesen
    sysinfo['last_upgrade'] = await asyncio.to_thread(pacman.last_upgrade)
    sysinfo['history'] = await asyncio.to_thread(pacman.history, 60)
    try:
        sysinfo['orphans'] = await asyncio.to_thread(pacman.orphans)
    except Exception as exc:  # noqa: BLE001 – Zusatzinformation
        log.warning('Verwaiste Pakete nicht ermittelbar: %s', exc)
        sysinfo['orphans'] = []
    try:
        sysinfo['cache'] = await asyncio.to_thread(pacman.cache_info, 2)
    except Exception as exc:  # noqa: BLE001
        log.warning('Paket-Cache nicht auswertbar: %s', exc)
        sysinfo['cache'] = {}
    sysinfo['pacnew'] = await asyncio.to_thread(pacman.pacnew_files)
    sysinfo['db_locked'] = pacman.db_locked()

    _step(_('Repo-Updates prüfen (checkupdates) …'))
    try:
        repo_updates = await asyncio.to_thread(pacman.check_updates)
    except Exception as exc:  # noqa: BLE001
        _warn(_('Repo-Updates nicht prüfbar: {err}', err=exc))
        repo_updates = []

    _step(_('Flatpak prüfen …'))
    try:
        fp_items, fp_updates = await asyncio.gather(asyncio.to_thread(flatpak.installed),
                                                    asyncio.to_thread(flatpak.updates))
    except Exception as exc:  # noqa: BLE001 – Flatpak darf den Rest nicht blockieren
        _warn(_('Flatpak nicht prüfbar: {err}', err=exc))
        fp_items, fp_updates = [], []

    foreign = {n: p.version for n, p in inst.items() if p.repo == 'aur'}
    aur_updates, aur_missing, rpc = [], [], {}
    if foreign:
        _step(_('AUR prüfen …'))
        try:
            rpc = await aur.info(list(foreign))
            aur_updates, aur_missing = aur.updates(foreign, rpc)
        except Exception as exc:  # noqa: BLE001
            _warn(_('AUR nicht erreichbar: {err}', err=exc))

    _step(_('Sicherheitsdatenbank laden …'))
    upd_versions = {u.name: u.new for u in repo_updates} | {u['name']: u['new'] for u in aur_updates}
    groups: list[dict] = []
    try:
        groups = await security.advisories()
        cves = await asyncio.to_thread(security.relevant, groups, {n: p.version for n, p in inst.items()}, upd_versions)
    except Exception as exc:  # noqa: BLE001
        _warn(_('Arch Security Tracker nicht erreichbar: {err}', err=exc))
        cves = []
    if security.URL in sysutil.STALE:
        _warn(_('Arch Security Tracker nicht erreichbar – Sicherheitsdaten vom Stand vor {h} h (Cache)',
                h=round(sysutil.STALE[security.URL] / 3600, 1)))
    if cves:
        try:
            await security.describe(cves)
        except Exception as exc:  # noqa: BLE001 – Beschreibungen sind Zusatzinformation
            log.warning('CVE-Beschreibungen nicht ladbar: %s', exc)

    _step(_('News laden …'))
    try:
        news_items = await news.items()
    except Exception as exc:  # noqa: BLE001
        log.warning('News nicht ladbar: %s', exc)
        news_items = []
    last = sysinfo['last_upgrade'] or ''
    for n in news_items:
        n['new'] = bool(last) and n['date'] >= last[:10]

    aur_meta, scans = {}, {}
    if aur_updates:
        _step(_('{n} AUR-PKGBUILDs scannen …', n=len(aur_updates)))
        try:
            aur_meta = await aur.scan_inputs([u['name'] for u in aur_updates])
            scans = aur_scanner.scan_all(aur_meta)
        except Exception as exc:  # noqa: BLE001
            _warn(_('AUR-Scan unvollständig: {err}', err=exc))
    other = {urlsplit(u).hostname or u for u in sysutil.STALE if u != security.URL}
    if other:
        _warn(_('Nicht erreichbar, Daten aus dem Cache (evtl. veraltet): {hosts}', hosts=', '.join(sorted(other))))

    _step(_('Bewerten …'))
    data = await asyncio.to_thread(_assemble, inst, repo_updates, fp_items, fp_updates, aur_updates, aur_missing,
                                   rpc, cves, news_items, aur_meta, scans, sysinfo)
    data['advisories'] = groups
    return data


def _assemble(inst, repo_updates, fp_items, fp_updates, aur_updates, aur_missing, rpc, cves, news_items,
              aur_meta, scans, sysinfo) -> dict:
    # --- Pakete mit aufgelösten Abhängigkeiten (provides → Paketname)
    provider: dict[str, str] = {}
    for p in inst.values():
        for prov in p.provides:
            provider.setdefault(analysis.split_dep(prov)[0], p.name)
    packages = []
    for p in inst.values():
        deps = []
        for d in p.depends:
            n = analysis.split_dep(d)[0]
            n = n if n in inst else provider.get(n)
            if n and n not in deps:
                deps.append(n)
        packages.append(dict(name=p.name, source='aur' if p.repo == 'aur' else 'repo', version=p.version,
                             size=p.size / 1048576, deps=deps, desc=p.desc, repo=p.repo, reason=p.reason))
    for f in fp_items:
        packages.append(dict(name=f['id'], source='flatpak', version=f['version'], size=0.0,
                             deps=[f['runtime']] if f['runtime'] else [], desc=f['name'], repo=f['origin'],
                             reason=f['installation']))

    # --- Bewertungsgrundlagen
    groups, _needs = analysis.together_groups(inst, repo_updates)
    try:
        backups = pacman.modified_backups([u.name for u in repo_updates]) if repo_updates else {}
    except Exception as exc:  # noqa: BLE001 – nur ein Bewertungsdetail, darf den Stand nicht verhindern
        log.warning('Backup-Dateien nicht lesbar: %s', exc)
        backups = {}
    fixes: dict[str, list[str]] = {}
    for c in cves:
        if c['status'] == 'behoben durch Update':
            fixes.setdefault(c['package'], []).append(c['id'])
    manual_news = [n['title'] for n in news_items if n['manual'] and n['new']]

    updates = []
    for u in repo_updates:
        reasons = analysis.repo_risk(u, backups=backups, fixes=fixes, manual_news=manual_news)
        updates.append(dict(id=u.name, name=u.name, source='repo', old=u.old, new=u.new, size=u.download,
                            repo=u.repo, reasons=reasons, risk=analysis.score(reasons)))
    for u in aur_updates:
        reasons = analysis.aur_risk(aur_meta.get(u['name']) or (aur.meta_from_rpc(rpc[u['name']]) if u['name'] in rpc else None),
                                    scans.get(u['name']))
        if aur.is_vcs(u['name']):
            reasons.append((_('VCS-Paket (Git)'), 5))
        updates.append(dict(id=u['name'], name=u['name'], source='aur', old=u['old'], new=u['new'], size=0.0,
                            repo='aur', base=u['base'], reasons=reasons, risk=analysis.score(reasons)))
    fp_by_id = {f['id'].split('//')[0]: f for f in fp_items}
    for u in fp_updates:
        cur = fp_by_id.get(u['id'])
        reasons = [(_('Flatpak (Sandbox)'), 3)]
        if cur and cur['kind'] == 'runtime':
            reasons.append((_('Runtime – betrifft mehrere Apps'), 8))
        size = _parse_size(u['download'])
        updates.append(dict(id=u['id'], name=u['id'], source='flatpak', old=cur['version'] if cur else '?',
                            new=u['new'], size=size, repo=u['origin'], reasons=reasons, risk=analysis.score(reasons)))

    conflicts = _conflicts(inst, repo_updates, groups, backups, manual_news, news_items, aur_missing,
                           fp_items, sysinfo, updates)
    return dict(packages=packages, updates=updates, conflicts=conflicts, cves=cves, news=news_items,
                scans=scans, aur_meta=aur_meta, groups=groups, system=sysinfo)


def _parse_size(text: str) -> float:
    m = re.match(r'([\d.,]+)\s*([kMG]?B)', (text or '').replace('\xa0', ' '))
    if not m:
        return 0.0
    v = float(m.group(1).replace(',', '.'))
    return v * {'B': 1 / 1048576, 'kB': 1 / 1024, 'MB': 1, 'GB': 1024}[m.group(2)]


def _conflicts(inst, repo_updates, groups, backups, manual_news, news_items, aur_missing, fp_items, sysinfo,
               updates) -> list[dict]:
    out: list[dict] = []
    if sysinfo.get('db_locked'):
        out.append(dict(severity='hoch', title=_('pacman-Datenbank ist gesperrt'), packages=[],
                        text=_('/var/lib/pacman/db.lck existiert – läuft gerade eine andere Paketverwaltung?'),
                        advice=_('Andere pacman-/paru-Prozesse beenden. Nur wenn keiner läuft: Sperrdatei entfernen.')))
    need_gib = sum(u['size'] for u in updates) / 1024 * 2.5 + 1
    if sysinfo.get('free_gib', 99) < need_gib:
        out.append(dict(severity='hoch', title=_('Wenig freier Speicher'), packages=[],
                        text=_('Frei: {free} GiB, für das Update empfohlen: ~{need} GiB.', free=f'{sysinfo["free_gib"]:.1f}', need=f'{need_gib:.1f}'),
                        advice=_('Paket-Cache leeren (paccache -rk1) oder alte Snapshots entfernen.')))
    for n in news_items:
        if n['manual'] and n['new']:
            out.append(dict(severity='hoch', title=_('News mit manuellem Eingriff: {title}', title=n['title']), packages=[],
                            text=_('{source}-News vom {date}, nach dem letzten Systemupdate veröffentlicht.', source=n['source'], date=n['date']),
                            advice=_('Meldung vor dem Update lesen und die beschriebenen Schritte befolgen.'),
                            link=n['link']))
    for g in groups:
        crit = [n for n in g if analysis.config.matches(n, analysis.config.CRITICAL)]
        out.append(dict(severity='mittel' if crit else 'niedrig',
                        title=_('{n} Pakete nur gemeinsam aktualisieren', n=len(g)),
                        packages=g, text=_('Die neuen Versionen hängen voneinander ab (versionierte Abhängigkeit, Soname '
                                           'oder gemeinsames pkgbase). Werden nur einzelne davon zurückgehalten, bricht das Update ab '
                                           'oder es entsteht ein Teil-Update.'),
                        advice=_('Alle zusammen aktualisieren oder alle zusammen zurückhalten.')))
    for pkg, dep_pkg, dep in analysis.foreign_rebuilds(inst, repo_updates):
        out.append(dict(severity='hoch', title=_('{pkg} muss neu gebaut werden', pkg=pkg), packages=[pkg, dep_pkg],
                        text=_('{pkg} (AUR) verlangt „{dep}“, das nach dem Update von {other} nicht mehr erfüllt ist.', pkg=pkg, dep=dep, other=dep_pkg),
                        advice=_('Nach dem Update neu bauen: {cmd}', cmd=f'paru -S --rebuild {pkg}')))
    py = next((u for u in repo_updates if u.name == 'python'), None)
    if py and analysis.version_parts(py.old)[1][:2] != analysis.version_parts(py.new)[1][:2]:
        foreign_py = [n for n, p in inst.items() if p.repo == 'aur' and (n.startswith('python-') or 'python' in p.depends)]
        if foreign_py:
            out.append(dict(severity='hoch', title=_('Python-Minor-Update: AUR-Python-Pakete neu bauen'),
                            packages=['python', *foreign_py[:8]],
                            text=_('python {old} → {new}. Fremdpakete installieren in den alten site-packages-Pfad.', old=py.old, new=py.new),
                            advice=_('Nach dem Update alle betroffenen AUR-Pakete neu bauen.')))
    if backups:
        out.append(dict(severity='niedrig', title=_('.pacnew erwartet bei {n} Paketen', n=len(backups)),
                        packages=sorted(backups)[:10],
                        text=_('Lokal geänderte Konfigurationsdateien: {files}', files=', '.join(f for v in backups.values() for f in v[:1])[:300]),
                        advice=_('Nach dem Update mit pacdiff zusammenführen.')))
    if sysinfo.get('pacnew'):
        out.append(dict(severity='niedrig', title=_('{n} offene .pacnew-Dateien', n=len(sysinfo['pacnew'])), packages=[],
                        text=', '.join(sysinfo['pacnew'][:8]),
                        advice=_('Mit pacdiff vergleichen und zusammenführen (sudo DIFFPROG=meld pacdiff).')))
    if aur_missing:
        out.append(dict(severity='niedrig', title=_('{n} Fremdpakete nicht (mehr) im AUR', n=len(aur_missing)),
                        packages=aur_missing[:10], text=_('Keine Updates und keine Sicherheitsprüfung möglich.'),
                        advice=_('Prüfen, ob das Paket umbenannt wurde oder entfernt werden kann.')))
    unused = flatpak.unused_runtimes(fp_items)
    if unused:
        out.append(dict(severity='niedrig', title=_('{n} ungenutzte Flatpak-Runtimes', n=len(unused)), packages=unused[:8],
                        text=_('Keine installierte App verwendet diese Runtimes.'),
                        advice='flatpak uninstall --unused'))
    if sysinfo.get('reboot_pending'):
        out.append(dict(severity='mittel', title=_('Neustart ausstehend'), packages=[],
                        text=_('Laufender Kernel {kernel} ist nicht mehr installiert – Module fehlen.', kernel=sysinfo['kernel']),
                        advice=_('Vor weiteren Updates neu starten.')))
    return out


def last_upgrade_text() -> str:
    raw = SYSTEM.get('last_upgrade')
    if not raw:
        return _('unbekannt')
    try:
        days = (datetime.now() - datetime.fromisoformat(raw)).days
    except ValueError:
        return raw
    return _('heute') if days == 0 else _('gestern') if days == 1 else _('vor {n} Tagen', n=days)
