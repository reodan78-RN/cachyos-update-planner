"""Führt einen Update-Plan aus – echt (sudo) oder als Probelauf.

Sicherheit:
* Das Passwort lebt nur in diesem Objekt im Arbeitsspeicher, geht ausschließlich über stdin an
  `sudo -S` und wird nie protokolliert; nach dem Lauf wird es verworfen.
* Keine Shell, alle Paketnamen werden validiert, sudo erhält immer ein festes Programm mit Argumenten.
* AUR-Pakete werden als normaler Benutzer gebaut (nie als root); das PKGBUILD, das gebaut wird, wird
  unmittelbar vorher erneut gescannt.
* Jeder Lauf schreibt ein Protokoll (0600) nach ~/.cache/update-planner/logs/.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import aur_scanner, config
from .i18n import _
from .sources import pacman as pacman_src
from .sysutil import env_c, have, valid_flatpak, valid_name

SUDO = ['sudo', '-S', '-k', '-p', '']
AUR_GIT = 'https://aur.archlinux.org/{base}.git'
READ_CHUNK = 64 * 1024
MAX_LINE = 1024 * 1024          # längere „Zeilen“ (z. B. Compiler-Ausgaben ohne \n) werden zerteilt
UI_LINE = 2000                  # in der Oberfläche gekürzt, im Protokoll vollständig
_LINE_SPLIT = re.compile(rb'\r\n|\r|\n')
TERM_GRACE = 5.0                # nach SIGTERM so lange warten, dann SIGKILL
GIT_TIMEOUT = 600.0             # git clone/fetch eines AUR-Repos
MIN_FREE_ROOT = 2 * 1024 ** 3   # freier Platz auf / und im Paket-Cache vor einem Repo-Update
MIN_FREE_BUILD = 1024 ** 3      # freier Platz im AUR-Bauverzeichnis
PACMAN_CACHE = Path('/var/cache/pacman/pkg')
# Eine Abhängigkeit aus .SRCINFO: Name, optional mit Versionsbedingung (nie mit „-“ beginnend → keine Option)
DEP_RE = re.compile(r'[a-zA-Z0-9@_+][a-zA-Z0-9@._+-]*(?:(?:<=|>=|<|>|=)[A-Za-z0-9._:+~-]+)?')

_RUNNING = asyncio.Lock()       # nur ein Update-Lauf gleichzeitig – auch über mehrere Fenster hinweg


def running() -> bool:
    return _RUNNING.locked()


@dataclass
class Plan:
    repo: bool = True                        # pacman -Syu ausführen
    hold: list[str] = field(default_factory=list)       # Repo-Pakete zurückhalten (--ignore)
    aur: list[tuple[str, str]] = field(default_factory=list)  # (pkgname, pkgbase)
    flatpak: list[str] = field(default_factory=list)
    snapshot: bool = True
    dry_run: bool = False
    reviewed: dict[str, str] = field(default_factory=dict)    # trotz Scanner-Warnung freigegeben: {Paket: Hash des geprüften Inhalts}
    remove_orphans: bool = False                      # nach dem Update verwaiste Pakete anbieten (Rückfrage)
    clean_cache: bool = False                         # Paket-Cache mit paccache aufräumen
    cache_keep: int = 2                               # so viele Versionen je Paket behalten
    install: list[str] = field(default_factory=list)  # NEU zu installierende Repo-Pakete (pacman -Syu --needed …)
    install_aur: set[str] = field(default_factory=set)  # Teilmenge von `aur`, die neu installiert wird (kein Update)
    flatpak_install: list[str] = field(default_factory=list)  # neue Flatpak-IDs aus Flathub
    flatpak_user: bool = False                        # Flatpak nur für den Benutzer statt systemweit
    remove: list[str] = field(default_factory=list)   # Repo-/AUR-Pakete entfernen (pacman -Rs[n])
    remove_configs: bool = False                      # dabei auch Konfigurationsdateien löschen (-n)
    flatpak_remove: list[str] = field(default_factory=list)   # Flatpak-Apps entfernen
    flatpak_remove_user: bool = False                 # die Apps liegen in der Benutzerinstallation
    flatpak_remove_data: bool = False                 # auch die Anwendungsdaten (~/.var/app) löschen

    def kind(self) -> str:
        """'install', 'remove' oder 'update' – bestimmt die Beschriftung (kein „Update“ bei einer Installation)."""
        if self.install or self.install_aur or self.flatpak_install:
            return 'install'
        if self.remove or self.flatpak_remove:
            return 'remove'
        return 'update'

    def phases(self) -> list[str]:
        p = ['Vorbereitung']
        if self.snapshot and not self.dry_run:
            p.append('Snapshot')
        if self.repo or self.install:
            p.append('Repo')
        if self.aur:
            p.append('AUR')
        if self.flatpak or self.flatpak_install:
            p.append('Flatpak')
        if self.remove or self.flatpak_remove:
            p.append('Entfernen')
        if self.remove_orphans or self.clean_cache:
            p.append('Aufräumen')
        p.append('Abschluss')
        return p


@dataclass
class Callbacks:
    phase: Callable[[str], None]
    progress: Callable[[float, float], None]       # (gesamt, phase) 0..1
    log: Callable[[str], None]
    ask: Callable[[str, str, str, str], Awaitable[bool]]   # (Titel, Text, Ja, Nein)


@dataclass
class Result:
    ok: bool = True
    updated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    pacnew: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)     # entfernte verwaiste Pakete
    cache_cleaned: bool = False
    reboot: bool = False
    snapshot: str | None = None
    log_file: str | None = None
    duration: float = 0.0


async def check_password(password: str, timeout: float = 30) -> bool:
    proc = await asyncio.create_subprocess_exec(*SUDO, '-v', stdin=asyncio.subprocess.PIPE,
                                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                                                env=env_c())
    try:
        await asyncio.wait_for(proc.communicate((password + '\n').encode()), timeout)
    except (TimeoutError, BrokenPipeError, ConnectionResetError):
        return False
    finally:
        await _reap(proc)                  # auch bei Zeitüberschreitung/Abbruch: stdin zu, beenden, abwarten
    return proc.returncode == 0


def _signal(proc, kill: bool) -> None:
    try:
        proc.kill() if kill else proc.terminate()
    except (ProcessLookupError, PermissionError):
        pass


async def _reap(proc, grace: float | None = None) -> None:
    """Prozess sicher beenden und einsammeln: stdin schließen, SIGTERM, nach `grace` Sekunden SIGKILL, dann wait().

    Läuft auch, wenn der aufrufende Task gerade abgebrochen wird – es bleibt kein Kind (und kein wartendes sudo)
    zurück. Ein bereits beendeter Prozess wird nur noch eingesammelt."""
    grace = TERM_GRACE if grace is None else grace
    stdin = getattr(proc, 'stdin', None)
    if stdin is not None:
        try:
            stdin.close()
        except (OSError, RuntimeError):
            pass
    if proc.returncode is not None:
        return
    _signal(proc, kill=False)
    try:
        await asyncio.shield(asyncio.wait_for(proc.wait(), grace))
        return
    except (TimeoutError, asyncio.CancelledError):
        pass
    _signal(proc, kill=True)
    try:
        await asyncio.shield(asyncio.wait_for(proc.wait(), grace))
    except (TimeoutError, asyncio.CancelledError):
        pass


async def _read_lines(stream):
    """Zeilen aus einem Prozess-Stream – ohne das 64-KiB-Limit von StreamReader.readline()."""
    buf = b''
    while chunk := await stream.read(READ_CHUNK):
        buf += chunk
        cr = buf.endswith(b'\r')          # \r am Stückende: gehört evtl. zu \r\n im nächsten Stück
        *lines, buf = _LINE_SPLIT.split(buf[:-1] if cr else buf)
        for line in lines:
            yield line
        if cr:
            buf += b'\r'
        while len(buf) > MAX_LINE:
            yield buf[:MAX_LINE]
            buf = buf[MAX_LINE:]
    buf = buf.rstrip(b'\r')
    if buf:
        yield buf


class Runner:
    def __init__(self, plan: Plan, cb: Callbacks, password: str | None, sysinfo: dict):
        if os.geteuid() == 0:
            raise RuntimeError(_('Der Update Planner darf nicht als root laufen.'))
        self.plan, self.cb, self._pw, self.sys = plan, cb, password, sysinfo
        self.result = Result()
        self._phases = plan.phases()
        self._idx = 0
        logdir = config.CACHE / 'logs'
        logdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        now = time.time()
        stem = time.strftime('update-%Y%m%d-%H%M%S', time.localtime(now)) + f'-{int(now * 1000) % 1000:03d}'
        for n in range(100):                      # zwei Läufe in derselben Millisekunde kollidieren nicht
            self._logpath = logdir / (stem + (f'-{n}' if n else '') + '.log')
            try:
                fd = os.open(self._logpath, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                break
            except FileExistsError:
                if n == 99:
                    raise
        self._logfile = os.fdopen(fd, 'w', buffering=1)
        self.result.log_file = str(self._logpath)

    # ---------------------------------------------------------------- Hilfen
    def _log(self, line: str) -> None:
        if self._pw and self._pw in line:          # Paranoia: Passwort nie ausgeben
            line = line.replace(self._pw, '•••')
        self._logfile.write(line + '\n')
        self.cb.log(line if len(line) <= UI_LINE else line[:UI_LINE] + ' …')

    def _enter(self, name: str) -> None:
        self._idx = self._phases.index(name)
        self.cb.phase(name)
        self._progress(0.0)

    def _progress(self, frac: float) -> None:
        frac = max(0.0, min(1.0, frac))
        self.cb.progress((self._idx + frac) / len(self._phases), frac)

    async def _stream(self, cmd: list[str], *, sudo: bool = False, cwd: Path | None = None,
                      env: dict | None = None, on_line: Callable[[str], None] | None = None,
                      timeout: float | None = None) -> int:
        """Befehl ausführen und Ausgabe zeilenweise protokollieren. Bei Zeitüberschreitung (`timeout`), Abbruch oder
        Fehler wird der Prozess beendet und eingesammelt; Zeitüberschreitung ergibt Rückgabewert 124."""
        full = [*SUDO, '--', *cmd] if sudo else cmd
        shown = ('sudo ' if sudo else '') + ' '.join(cmd)
        self._log(f'$ {shown}')
        if self.plan.dry_run and sudo:
            self._log(_('  (Probelauf – nicht ausgeführt)'))
            return 0
        proc = await asyncio.create_subprocess_exec(
            *full, cwd=str(cwd) if cwd else None, env=env or env_c(),
            stdin=asyncio.subprocess.PIPE if sudo else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            async with asyncio.timeout(timeout):
                if sudo:
                    try:
                        proc.stdin.write(((self._pw or '') + '\n').encode())
                        await proc.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError):
                        pass                      # Prozess hat schon beendet – Rückgabewert zählt
                    finally:
                        proc.stdin.close()
                assert proc.stdout
                async for raw in _read_lines(proc.stdout):
                    line = raw.decode(errors='replace').rstrip()
                    if line:
                        self._log(line)
                        if on_line:
                            on_line(line)
                return await proc.wait()
        except TimeoutError:
            self._log(_('!! Zeitlimit überschritten ({s} s): {cmd}', s=int(timeout or 0), cmd=shown))
            return 124
        finally:
            await _reap(proc)                     # bei Abbruch/Fehler keinen Prozess verwaisen lassen

    # ---------------------------------------------------------------- Ablauf
    async def run(self) -> Result:
        start = time.time()
        try:
            if _RUNNING.locked():
                raise _Abort(_('Es läuft bereits ein Update'))
            async with _RUNNING:
                await self._steps()
        except (_Abort, ValueError) as exc:
            self.result.ok = False
            self.result.errors.append(str(exc))
            self._log(_('!! Abbruch: {err}', err=exc))
        except Exception as exc:  # noqa: BLE001 – unerwartete Fehler (z. B. fehlendes Programm) melden statt hängen
            self.result.ok = False
            self.result.errors.append(f'{type(exc).__name__}: {exc}')
            self._log(_('!! Abbruch: {err}', err=f'{type(exc).__name__}: {exc}'))
        finally:
            self._pw = None                       # Passwort verwerfen
            self.result.duration = time.time() - start
            self._logfile.close()
        return self.result

    async def _steps(self) -> None:
        await self._prepare()
        if 'Snapshot' in self._phases:
            await self._snapshot('pre')
        if self.plan.repo or self.plan.install:
            await self._repo()
        if self.plan.aur:
            await self._aur()
        if self.plan.flatpak or self.plan.flatpak_install:
            await self._flatpak()
        if self.plan.remove or self.plan.flatpak_remove:
            await self._remove()
        if 'Aufräumen' in self._phases:
            await self._cleanup()
        await self._finish()

    async def _prepare(self) -> None:
        self._enter('Vorbereitung')
        started = {'install': _(':: Update Planner – Installation gestartet'), 'remove': _(':: Update Planner – Entfernen gestartet')}
        self._log(_(':: Update Planner – Probelauf gestartet') if self.plan.dry_run
                  else started.get(self.plan.kind(), _(':: Update Planner – Update gestartet')))
        if pacman_src.db_locked():
            raise _Abort(_('pacman-Datenbank ist gesperrt (/var/lib/pacman/db.lck).'))
        for n in [*self.plan.hold, *self.plan.install, *(n for n, _b in self.plan.aur),
                  *(b for _n, b in self.plan.aur)]:
            valid_name(n)
        for n in self.plan.remove:
            valid_name(n)
        for ref in [*self.plan.flatpak, *self.plan.flatpak_install, *self.plan.flatpak_remove]:
            valid_flatpak(ref)
        await self._preflight()
        self._pacnew_before = set(await asyncio.to_thread(pacman_src.pacnew_files))
        self._progress(1.0)

    async def _preflight(self) -> None:
        """Alles prüfen, was sich ohne Änderung am System prüfen lässt – bevor Snapshot, pacman, AUR oder Flatpak
        irgendetwas verändern. Schlägt etwas fehl, bricht der Lauf ab, ohne dass etwas passiert ist.

        Geprüft werden: benötigte Programme, freier Speicher, ob neu zu installierende Repo-Pakete in den
        Paketdatenbanken stehen und ob das Entfernen Abhängigkeiten bräche. Ein späterer Fehler (z. B. ein
        Download bricht ab) ist damit nicht ausgeschlossen – dafür gibt es den Snapshot vor dem Update."""
        p = self.plan
        tools = {'pacman': p.repo or p.install or p.remove or p.aur or p.remove_orphans,
                 'sudo': not p.dry_run and (p.repo or p.install or p.remove or p.aur or p.remove_orphans or p.clean_cache),
                 'git': p.aur, 'makepkg': p.aur, 'paccache': p.clean_cache,
                 'flatpak': p.flatpak or p.flatpak_install or p.flatpak_remove}
        missing = [t for t, needed in tools.items() if needed and not have(t)]
        if missing:
            raise _Abort(_('Benötigte Programme fehlen: {tools}', tools=', '.join(missing)))
        if not p.dry_run:
            need = [(Path('/'), MIN_FREE_ROOT), (PACMAN_CACHE, MIN_FREE_ROOT)] if p.repo or p.install or p.aur else []
            if p.aur:
                need.append((config.AUR_BUILD, MIN_FREE_BUILD))
            for path, minimum in need:
                free = _free_bytes(path)
                if free is not None and free < minimum:
                    raise _Abort(_('Zu wenig freier Speicher in {path}: {free} MiB frei, mindestens {need} MiB nötig',
                                   path=str(path), free=free // 2 ** 20, need=minimum // 2 ** 20))
        if p.install:
            db = ['--dbpath', str(config.CHECKUP_DB)] if (config.CHECKUP_DB / 'sync').is_dir() else []
            out: list[str] = []
            rc = await self._stream(['pacman', '-Sp', *db, '--logfile', '/dev/null', '--print-format', '%n',
                                     '--', *p.install], on_line=out.append, timeout=120)
            found = {x.strip() for x in out}
            absent = [n for n in p.install if n not in found]
            if rc != 0 or absent:
                raise _Abort(_('Nicht in den Paketdatenbanken gefunden: {pkgs}', pkgs=', '.join(absent or p.install)))
        self._removal: tuple[list[str], list[str]] | None = None
        if p.remove:
            out = []
            rc = await self._stream(['pacman', '-Rsp', '--print-format', '%n', '--', *p.remove],
                                    on_line=out.append, timeout=120)
            gone, broken = pacman_src.parse_removal('\n'.join(out))
            if broken:
                raise _Abort(_('Entfernen nicht möglich – davon abhängig: {pkgs}', pkgs=', '.join(broken[:8])))
            if rc != 0:
                raise _Abort(_('Entfernen nicht möglich – Paket nicht installiert? (pacman {rc})', rc=rc))
            self._removal = (gone, broken)

    async def _snapshot(self, kind: str) -> None:
        if kind == 'pre':
            self._enter('Snapshot')
        if self.sys.get('snap_pac'):
            if kind == 'pre':
                self._log(_(':: snap-pac legt für die pacman-Transaktion automatisch pre/post-Snapshots an')
                          + (_(' (limine-snapper-sync trägt sie ins Bootmenü ein)') if self.sys.get('limine_snapper') else ''))
                self._progress(1.0)
            return
        if not self.sys.get('snapper'):
            if kind == 'pre':
                self._log(_(':: Kein Snapper-Profil „root“ – Snapshot übersprungen'))
            return
        out: list[str] = []
        cmd = ['snapper', '-c', 'root', 'create', '-t', kind, '-c', 'number', '-d', 'update-planner']
        cmd += ['-p'] if kind == 'pre' else ['--pre-number', self.result.snapshot or '0']
        rc = await self._stream(cmd, sudo=True, on_line=out.append)
        if rc != 0:
            raise _Abort(_('Snapshot fehlgeschlagen – Update nicht gestartet.'))
        if kind == 'pre':
            self.result.snapshot = next((x.strip() for x in out if x.strip().isdigit()), None)
            self._progress(1.0)

    async def _repo(self) -> None:
        self._enter('Repo')
        total = {'n': 0, 'done': 0, 'dl': 0}
        sub = {'Download': 0.0, 'Prüfen': 0.35, 'Installieren': 0.5, 'Hooks': 0.9}
        state = {'sub': 'Download', 'shown': ''}

        def parse(line: str) -> None:
            m = _RE_TOTAL.match(line)
            if m:
                total['n'] = int(m.group(1))
            if ':: Retrieving packages' in line:
                state['sub'] = 'Download'
            elif _RE_CHECK.search(line):
                state['sub'] = 'Prüfen'
            elif ':: Processing package changes' in line:
                state['sub'] = 'Installieren'
            elif ':: Running post-transaction hooks' in line:
                state['sub'] = 'Hooks'
            if _RE_DL.search(line):
                total['dl'] += 1
            m = _RE_STEP.match(line)
            if m:
                total['done'], total['n'] = int(m.group(1)), int(m.group(2))
            m = _RE_PACNEW.search(line)
            if m:
                self.result.pacnew.append(m.group(2))
            base = sub[state['sub']]
            span = {'Download': 0.35, 'Prüfen': 0.15, 'Installieren': 0.4, 'Hooks': 0.1}[state['sub']]
            n = max(total['n'], 1)
            inner = total['dl'] / n if state['sub'] == 'Download' else total['done'] / n if state['sub'] == 'Installieren' else 0.5
            self._progress(base + span * min(inner, 1.0))
            if state['shown'] != state['sub']:      # nicht für jede Ausgabezeile neu setzen
                state['shown'] = state['sub']
                self.cb.phase(f'Repo · {state["sub"]}')

        ignore = ['--ignore', ','.join(self.plan.hold)] if self.plan.hold else []
        install = [valid_name(n) for n in self.plan.install]
        # Neue Pakete immer mit vollständigem Systemupdate (-Syu) – Arch unterstützt keine Teil-Updates
        extra = ['--needed', *install] if install else []
        if self.plan.dry_run:
            out: list[str] = []
            rc = await self._stream(['pacman', '-Sup', '--dbpath', str(config.CHECKUP_DB), '--logfile', '/dev/null',
                                     '--print-format', '%n %v', *(['--needed'] if install else []), *ignore, *install],
                                    on_line=out.append)
            self._log(_(':: Probelauf: {n} Repo-Pakete würden aktualisiert', n=len(out))
                      + (_(', {n} zurückgehalten', n=len(self.plan.hold)) if self.plan.hold else ''))
        else:
            rc = await self._stream(['pacman', '-Syu', '--noconfirm', '--noprogressbar', *ignore, *extra],
                                    sudo=True, on_line=parse)
        if rc != 0:
            raise _Abort(_('pacman beendet mit Fehlercode {rc} – siehe Protokoll.', rc=rc))
        self.result.updated.append('Repo')
        self._progress(1.0)

    async def _aur(self) -> None:
        self._enter('AUR')
        n = len(self.plan.aur)
        self._aur_built: dict[str, set[str]] = {}    # pkgbase → dabei installierte Pakete (Split-Pakete nur einmal bauen)
        for i, (name, base) in enumerate(self.plan.aur):
            valid_name(name), valid_name(base)
            self.cb.phase(f'AUR · {name}')
            try:
                if base in self._aur_built:
                    ok = name in self._aur_built[base]
                    if not ok:
                        self._log(_(':: {pkg}: nicht im Build von {base} enthalten', pkg=name, base=base))
                else:
                    ok = await self._aur_one(name, base)
            except _Skip as exc:
                ok = False
                self._log(_(':: {pkg} übersprungen: {reason}', pkg=name, reason=exc))
            (self.result.updated if ok else self.result.skipped).append(name)
            self._progress((i + 1) / n)

    async def _aur_one(self, name: str, base: str) -> bool:
        d = config.AUR_BUILD / base
        config.AUR_BUILD.mkdir(parents=True, exist_ok=True, mode=0o700)
        if (d / '.git').is_dir():
            if await self._stream(['git', '-C', str(d), 'fetch', '--depth', '1', 'origin'], timeout=GIT_TIMEOUT) != 0 or \
               await self._stream(['git', '-C', str(d), 'reset', '--hard', 'FETCH_HEAD'], timeout=GIT_TIMEOUT) != 0:
                raise _Skip(_('git fetch fehlgeschlagen'))
        elif await self._stream(['git', 'clone', '--depth', '1', '--', AUR_GIT.format(base=base), str(d)],
                                timeout=GIT_TIMEOUT) != 0:
            raise _Skip(_('git clone fehlgeschlagen'))

        # Genau das PKGBUILD scannen, das gleich gebaut wird
        if not (d / 'PKGBUILD').is_file():
            raise _Skip(_('kein PKGBUILD im AUR-Repository'))
        text = (d / 'PKGBUILD').read_text(errors='replace')
        files = {'PKGBUILD': text}
        # per install= referenzierte Dateien (wie in der Oberfläche aufgelöst) und – konservativ – jede weitere
        # .install-Datei im Repository; eine referenzierte, aber fehlende Datei zählt als leer
        for fname in aur_scanner.install_names(text, base, name):
            fp = d / fname
            files[fname] = fp.read_text(errors='replace') if fp.is_file() and not fp.is_symlink() else ''
        for fp in sorted(d.glob('*.install')):
            if fp.name not in files and fp.is_file() and not fp.is_symlink():
                files[fp.name] = fp.read_text(errors='replace')
        findings = [f for fname, content in files.items() for f in aur_scanner.scan_text(content, fname)]
        # Freigabe gilt nur für genau diese Dateien mit genau diesem Inhalt
        reviewed_hash = self.plan.reviewed.get(name)
        approved = bool(reviewed_hash) and reviewed_hash == aur_scanner.review_hash(files, name)
        if reviewed_hash and not approved:
            self._log(_(':: {pkg}: PKGBUILD hat sich seit der manuellen Prüfung geändert – Freigabe gilt nicht mehr', pkg=name))
        scan = aur_scanner.ScanResult(name, findings)
        self._log(_(':: PKGBUILD-Scan {pkg}: {verdict} (Score {score}, {n} Befunde)', pkg=name, verdict=_(scan.verdict[0]), score=scan.score, n=len(findings)))
        if scan.score >= 40 and not approved:
            worst = sorted(findings, key=lambda f: aur_scanner.SEVERITY_ORDER.index(f.severity))[0]
            if not await self.cb.ask(_('{pkg}: verdächtige Muster', pkg=name),
                                     _('{n} Befunde, u. a. „{title}“ ({where}). Paket trotzdem bauen?', n=len(findings),
                                       title=worst.title, where=f'{worst.file}:{worst.line}'),
                                     _('Trotzdem bauen'), _('Überspringen')):
                raise _Skip(_('vom AUR-Scanner gestoppt'))

        # Abhängigkeiten: nur aus den Repos nachinstallieren, AUR-Abhängigkeiten nicht automatisch
        srcinfo = await asyncio.to_thread(_run_text, ['makepkg', '--printsrcinfo'], d)
        deps = parse_srcinfo_deps(srcinfo)
        missing = (await asyncio.to_thread(_run_text, ['pacman', '-T', '--', *deps], None, (0, 127))).split() if deps else []
        if missing:
            if any(m not in deps for m in missing):
                raise _Skip(_('unerwartete Ausgabe von pacman -T'))
            avail = await asyncio.to_thread(_run_text, ['pacman', '-Sp', '--print-format', '%n', '--', *missing], None, (0, 1))
            if len(avail.split()) < len(missing):
                raise _Skip(_('benötigt Pakete, die nicht in den Repos sind ({pkgs}) – mit {helper} bauen',
                              pkgs=', '.join(missing), helper=self.sys.get('aur_helper') or 'paru'))
            if await self._stream(['pacman', '-S', '--asdeps', '--needed', '--noconfirm', '--', *missing], sudo=True) != 0:
                raise _Skip(_('Abhängigkeiten konnten nicht installiert werden'))

        if self.plan.dry_run:
            self._log(_(':: Probelauf: {pkg} würde jetzt mit makepkg gebaut und installiert', pkg=name))
            return True
        env = env_c() | {'PKGDEST': str(d / 'pkg')}
        if await self._stream(['makepkg', '-f', '--noconfirm', '--cleanbuild'], cwd=d, env=env) != 0:
            raise _Skip(_('makepkg fehlgeschlagen (Quellen, Prüfsummen oder PGP-Schlüssel?)'))
        root = d.resolve()
        pkgs = [p for p in (await asyncio.to_thread(_run_text, ['makepkg', '--packagelist'], d, (0,), env)).split()
                if Path(p).is_file() and Path(p).resolve().is_relative_to(root)]
        installed = set((await asyncio.to_thread(_run_text, ['pacman', '-Qq'], None)).split())
        native = set((await asyncio.to_thread(_run_text, ['pacman', '-Qqn'], None, (0, 1))).split())
        names = {p: (await asyncio.to_thread(_run_text, ['pacman', '-Qqp', '--', p], None)).strip() for p in pkgs}
        wanted = self._select_artifacts(name, base, names, installed, native)
        if await self._stream(['pacman', '-U', '--noconfirm', '--noprogressbar', '--', *wanted.values()], sudo=True) != 0:
            raise _Skip(_('Installation fehlgeschlagen'))
        self._aur_built[base] = set(wanted)
        return True

    def _select_artifacts(self, name: str, base: str, names: dict[str, str], installed: set[str],
                          native: set[str]) -> dict[str, str]:
        """{Paketname: Datei} der gebauten Pakete, die installiert werden – nur aus einer expliziten Erlaubnisliste.

        Erlaubt sind die Pakete des Plans mit derselben pkgbase; Neuinstallationen nur das gewünschte Paket,
        Updates nur bereits installierte. Ein Artefakt, das ein installiertes Repo-Paket ersetzen würde, oder
        mehrere Dateien für denselben Paketnamen stoppen das Paket ganz (das PKGBUILD baut etwas anderes als
        angekündigt). Andere zusätzliche Artefakte (z. B. -debug) werden nur protokolliert und nicht installiert."""
        allowed = {n for n, b in self.plan.aur if b == base}
        if name in self.plan.install_aur:
            allowed = {name}
        else:
            allowed &= installed
        seen: dict[str, str] = {}
        for path, pkg in names.items():
            if not pkg or pkg in seen:
                raise _Skip(_('mehrdeutige Paketdateien für {pkg}', pkg=pkg or '?'))
            seen[pkg] = path
            if pkg in native and pkg not in allowed:
                raise _Skip(_('PKGBUILD baut {pkg}, das aus den Repos installiert ist – nicht installiert', pkg=pkg))
        wanted = {pkg: path for pkg, path in seen.items() if pkg in allowed}
        for pkg in sorted(set(seen) - set(wanted)):
            self._log(_(':: {pkg}: nicht angefordert, wird nicht installiert', pkg=pkg))
        if not wanted:
            raise _Skip(_('kein passendes Paket gebaut'))
        return wanted

    async def _flatpak(self) -> None:
        self._enter('Flatpak')
        ids = [valid_flatpak(x) for x in self.plan.flatpak]
        new = [valid_flatpak(x) for x in self.plan.flatpak_install]
        if ids:
            if self.plan.dry_run:
                self._log(_(':: Probelauf: {cmd}', cmd='flatpak update ' + ' '.join(ids)))
            else:
                rc = await self._stream(['flatpak', 'update', '-y', '--noninteractive', *ids])
                if rc != 0:
                    self.result.errors.append(_('flatpak update beendet mit {rc}', rc=rc))
        for i, ref in enumerate(new):
            cmd = _flatpak_cmd('install', self.plan.flatpak_user) + ['flathub', ref]
            if self.plan.dry_run:
                self._log(_(':: Probelauf: {cmd}', cmd=' '.join(cmd)))
            else:
                rc = await self._stream(cmd)
                if rc != 0:
                    self.result.errors.append(_('flatpak install beendet mit {rc}', rc=rc))
                    self.result.ok = False
                    self.result.skipped.append(ref)
                    continue
            self.result.updated.append(ref)
            self._progress((i + 1) / len(new))
        self.result.updated.append('Flatpak')
        self._progress(1.0)

    async def _remove(self) -> None:
        self._enter('Entfernen')
        names = [valid_name(n) for n in self.plan.remove]
        refs = [valid_flatpak(x) for x in self.plan.flatpak_remove]
        if names:
            flags = '-Rns' if self.plan.remove_configs else '-Rs'
            if self.plan.dry_run:                 # Vorschau stammt aus der Vorabprüfung (_preflight)
                gone = self._removal[0] if self._removal else []
                rc = 0
                self._log(_(':: Probelauf: {n} Pakete würden entfernt: {pkgs}', n=len(gone), pkgs=', '.join(gone)))
            else:
                rc = await self._stream(['pacman', flags, '--noconfirm', '--noprogressbar', *names], sudo=True)
            if rc != 0:
                raise _Abort(_('pacman beendet mit Fehlercode {rc} – siehe Protokoll.', rc=rc))
            self.result.updated.append('Entfernen')
        for i, ref in enumerate(refs):
            cmd = _flatpak_cmd('uninstall', self.plan.flatpak_remove_user)
            if self.plan.flatpak_remove_data:
                cmd.append('--delete-data')
            cmd.append(ref)
            if self.plan.dry_run:
                self._log(_(':: Probelauf: {cmd}', cmd=' '.join(cmd)))
            else:
                rc = await self._stream(cmd)
                if rc != 0:
                    self.result.errors.append(_('flatpak uninstall beendet mit {rc}', rc=rc))
                    self.result.ok = False
                    self.result.skipped.append(ref)
                    continue
            self.result.updated.append(ref)
            self._progress((i + 1) / len(refs))
        self._progress(1.0)

    async def _cleanup(self) -> None:
        """Verwaiste Pakete (nur nach Rückfrage mit Liste) und Paket-Cache. Fehler hier brechen nichts ab."""
        self._enter('Aufräumen')
        total = int(self.plan.remove_orphans) + int(self.plan.clean_cache)
        done = 0
        if self.plan.remove_orphans:
            orphans = await asyncio.to_thread(pacman_src.orphans)   # nach dem Update neu ermitteln
            if not orphans:
                self._log(_(':: Keine verwaisten Pakete'))
            elif self.plan.dry_run:
                self._log(_(':: Probelauf: {n} verwaiste Pakete würden zur Auswahl gestellt: {pkgs}',
                            n=len(orphans), pkgs=', '.join(orphans)))
            else:
                listing = ', '.join(orphans[:25]) + (' …' if len(orphans) > 25 else '')
                if await self.cb.ask(_('{n} verwaiste Pakete entfernen?', n=len(orphans)),
                                     _('Diese Pakete wurden als Abhängigkeit installiert und werden von keinem Paket '
                                       'mehr benötigt: {pkgs}. Sie werden mit pacman -Rns samt Konfigurationsdateien '
                                       'entfernt.', pkgs=listing),
                                     _('Entfernen'), _('Behalten')):
                    rc = await self._stream(['pacman', '-Rns', '--noconfirm', '--noprogressbar', *orphans], sudo=True)
                    if rc == 0:
                        self.result.removed = orphans
                    else:
                        self.result.errors.append(_('Verwaiste Pakete nicht entfernt (pacman {rc})', rc=rc))
                else:
                    self._log(_(':: Verwaiste Pakete behalten'))
            done += 1
            self._progress(done / total)
        if self.plan.clean_cache:
            keep = str(max(0, min(int(self.plan.cache_keep), 9)))
            if self.plan.dry_run:
                rcs = [await self._stream(['paccache', '-dk', keep]), await self._stream(['paccache', '-duk0'])]
            else:
                # alte Versionen installierter Pakete, dann alle Versionen deinstallierter Pakete
                rcs = [await self._stream(['paccache', '-rk', keep], sudo=True),
                       await self._stream(['paccache', '-ruk0'], sudo=True)]
            if any(rcs):
                self.result.errors.append(_('Paket-Cache nicht vollständig aufgeräumt (paccache {rc})', rc=max(rcs)))
            else:
                self.result.cache_cleaned = not self.plan.dry_run
            done += 1
            self._progress(done / total)

    async def _finish(self) -> None:
        self._enter('Abschluss')
        if 'Snapshot' in self._phases and self.result.snapshot:
            await self._snapshot('post')
        after = set(await asyncio.to_thread(pacman_src.pacnew_files))
        self.result.pacnew = sorted(set(self.result.pacnew) | (after - self._pacnew_before))
        kernel = os.uname().release
        self.result.reboot = not self.plan.dry_run and not Path(f'/usr/lib/modules/{kernel}').exists()
        self._log(_(':: Fertig') + (_(' – Neustart erforderlich (Kernel ersetzt)') if self.result.reboot else ''))
        self._progress(1.0)


def _flatpak_cmd(verb: str, user: bool) -> list[str]:
    """flatpak install/uninstall ohne Rückfragen. `--noninteractive` unterdrückt auch den Polkit-Dialog; das darf nur
    für die Benutzerinstallation (keine Autorisierung nötig) gelten, sonst scheitern systemweite Vorgänge, wo Polkit
    nachfragen müsste (Mitglieder von „wheel“ werden meist ohne Abfrage zugelassen). stdin ist /dev/null."""
    return ['flatpak', verb, '-y', *(['--noninteractive'] if user else []), '--user' if user else '--system']


_RE_TOTAL = re.compile(r'^Packages \((\d+)\)')
_RE_CHECK = re.compile(r'checking (keyring|package integrity|for file conflicts|available disk space)')
_RE_DL = re.compile(r'download(ing|ed)')
_RE_STEP = re.compile(r'^\(\s*(\d+)/(\d+)\) (upgrading|installing|reinstalling|downgrading|removing)')
_RE_PACNEW = re.compile(r'warning: (\S+) installed as (\S+\.pacnew)')


def parse_srcinfo_deps(srcinfo: str) -> list[str]:
    """depends/makedepends/checkdepends (auch _x86_64) aus `makepkg --printsrcinfo`, je Zeile ein Eintrag.

    .SRCINFO kennt keine Alternativen – jede Zeile ist eine Abhängigkeit, optional mit Versionsbedingung
    (`foo>=1.2`), die pacman -T/-S direkt verstehen. Alles andere (z. B. etwas, das mit „-“ beginnt und als
    Option gelesen würde) bricht das Paket ab, statt an sudo pacman durchgereicht zu werden."""
    deps: list[str] = []
    for m in re.finditer(r'^\s*(?:make|check)?depends(?:_x86_64)? = (.*)$', srcinfo, re.M):
        dep = m.group(1).strip()
        if not dep:
            continue                              # leere Überschreibung in einem Split-Paket
        if not DEP_RE.fullmatch(dep) or len(dep) > 256:
            raise _Skip(_('ungültige Abhängigkeit im PKGBUILD: {dep}', dep=repr(dep[:80])))
        if dep not in deps:
            deps.append(dep)
    return sorted(deps)


def _free_bytes(path: Path) -> int | None:
    """Freier Platz auf dem Dateisystem von `path` (oder dem nächsten vorhandenen Elternverzeichnis)."""
    for p in (path, *path.parents):
        if p.exists():
            try:
                return shutil.disk_usage(p).free
            except OSError:
                return None
    return None


class _Abort(Exception):
    pass


class _Skip(Exception):
    pass


def _run_text(cmd: list[str], cwd: Path | None, ok: tuple[int, ...] = (0,), env: dict | None = None) -> str:
    import subprocess
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=300, env=env or env_c(),
                       stdin=subprocess.DEVNULL)
    if p.returncode not in ok:
        raise _Skip(_('{cmd} beendet mit {rc}', cmd=f'{cmd[0]} {cmd[1] if len(cmd) > 1 else ""}'.strip(), rc=p.returncode))
    return p.stdout
