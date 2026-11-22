"""Tray-Symbol: prüft im Hintergrund auf Updates, meldet neue per Popup und öffnet den Planer.

Eigener, schlanker Prozess (`update-planner-tray`, ohne NiceGUI/WebKit). Die Logik (Prüfen, Zusammenfassen,
Merken bereits gemeldeter Updates) ist von der Qt-Oberfläche getrennt und ohne Qt testbar.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import __version__, config, i18n
from .i18n import _
from .sources import aur
from .sysutil import CommandError, env_c, have, run

log = logging.getLogger('update_planner.tray')

TRAY_DB = config.CACHE / 'tray-db'          # eigene Kopie, damit sie sich nicht mit der Prüfung im Fenster beißt
STATE_FILE = config.CACHE / 'tray.json'     # welche Updates schon gemeldet wurden
LOCK_FILE = config.CACHE / 'tray.lock'
ICON = Path(__file__).parent / 'static' / 'icon.png'
DEFAULT_INTERVAL = 60                       # Minuten
FIRST_CHECK_DELAY = 30                      # Sekunden nach dem Login: Netzwerk ist dann meist da
PACMAN_LOG = Path('/var/log/pacman.log')
UPDATE_RE = re.compile(r'^(\S+) (\S+) -> (\S+)$')


# ---------- Logik (ohne Qt) ----------

def repo_updates() -> list[tuple[str, str]]:
    """(Paket, neue Version) laut `checkupdates` – synchronisiert eine private DB, das System bleibt unberührt."""
    config.ensure_dirs()
    env = env_c() | {'CHECKUPDATES_DB': str(TRAY_DB)}
    out = run(['checkupdates', '--nocolor'], timeout=300, env=env, ok_codes=(0, 2))   # 2 = keine Updates
    found = []
    for line in out.splitlines():
        m = UPDATE_RE.match(line.strip())
        if m:
            found.append((m.group(1), m.group(3)))
    return found


def flatpak_updates() -> list[tuple[str, str]]:
    if not have('flatpak'):
        return []
    try:
        out = run(['flatpak', 'remote-ls', '--updates', '--columns=application,version,branch'], timeout=120)
    except (CommandError, OSError):
        return []
    found = []
    for line in out.splitlines():
        f = line.split('\t')
        if f and f[0]:
            found.append((f[0], (f[1] if len(f) > 1 and f[1] else f[2] if len(f) > 2 else '').strip()))
    return found


def aur_updates() -> list[tuple[str, str]]:
    """Fremdpakete (`pacman -Qm`) mit neuerer Version im AUR (wie im Planer; VCS-Pakete ausgenommen)."""
    try:
        out = run(['pacman', '-Qm'], timeout=30, ok_codes=(0, 1))
        foreign = dict(line.split(None, 1) for line in out.splitlines() if len(line.split(None, 1)) == 2)
        if not foreign:
            return []
        found, _missing = aur.updates(foreign, asyncio.run(aur.info(list(foreign), ttl=600)))
    except Exception as exc:  # noqa: BLE001 – AUR nicht erreichbar: Repo/Flatpak trotzdem melden
        log.warning('AUR-Prüfung fehlgeschlagen: %s', exc)
        return []
    return [(u['name'], u['new']) for u in found]


def check() -> dict[str, list[tuple[str, str]]]:
    """Fragt Repo, AUR und Flatpak ab – dieselben Quellen wie der Planer. Fällt `checkupdates` aus, wird die
    Ausnahme weitergereicht (Stand beibehalten)."""
    return {'repo': repo_updates(), 'aur': aur_updates(), 'flatpak': flatpak_updates()}


def signature(found: dict[str, list[tuple[str, str]]]) -> set[str]:
    """Eindeutige Kennung je Update – eine neue Version desselben Pakets gilt als neues Update."""
    return {f'{src}:{name}={ver}' for src, items in found.items() for name, ver in items}


def total(found: dict[str, list[tuple[str, str]]]) -> int:
    return sum(len(v) for v in found.values())


def summary(found: dict[str, list[tuple[str, str]]], limit: int = 5) -> tuple[str, str]:
    """(Titel, Text) für Popup und Tooltip."""
    n = total(found)
    if not n:
        return _('Keine Updates'), _('Das System ist aktuell.')
    title = _('{n} Update verfügbar', n=n) if n == 1 else _('{n} Updates verfügbar', n=n)
    parts = []
    if found.get('repo'):
        parts.append(_('Repo: {n}', n=len(found['repo'])))
    if found.get('aur'):
        parts.append(_('AUR: {n}', n=len(found['aur'])))
    if found.get('flatpak'):
        parts.append(_('Flatpak: {n}', n=len(found['flatpak'])))
    names = [name for items in found.values() for name, _v in items]
    shown = ', '.join(names[:limit]) + (' …' if len(names) > limit else '')
    return title, ' · '.join(parts) + '\n' + shown


def load_notified(path: Path = STATE_FILE) -> set[str]:
    try:
        data = json.loads(path.read_text())
        return {x for x in data.get('notified', []) if isinstance(x, str)}
    except (OSError, ValueError, AttributeError):
        return set()


def save_notified(sig: set[str], path: Path = STATE_FILE) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump({'notified': sorted(sig)}, f)
        tmp.replace(path)
    except OSError as exc:
        log.warning('Zustand nicht gespeichert: %s', exc)


def news(current: set[str], notified: set[str]) -> set[str]:
    """Updates, die noch nicht gemeldet wurden. Gemeldet wird nur, wenn es wirklich Neues gibt."""
    return current - notified


def planner_command() -> list[str]:
    """Aufruf des Planer-Fensters: `update-planner` neben diesem Programm, sonst über den Interpreter."""
    own = Path(sys.argv[0]).resolve().parent / 'update-planner'
    if own.exists():
        return [str(own)]
    found = shutil.which('update-planner')
    return [found] if found else [sys.executable, '-m', 'update_planner.app']


def lock_instance(path: Path = LOCK_FILE):
    """Nur ein Tray-Prozess je Benutzer; gibt das offene Lock zurück (muss gehalten werden) oder None."""
    config.ensure_dirs()
    fh = open(path, 'w')
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


# ---------- Qt-Oberfläche ----------

def run_tray(interval: int, first_delay: int) -> int:
    try:
        from PySide6.QtCore import QFileSystemWatcher, QObject, QThread, QTimer, Signal
        from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
        from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
    except ImportError:
        print(_('Das Tray-Symbol braucht PySide6 (sudo pacman -S pyside6).'), file=sys.stderr)
        return 1

    class Worker(QThread):
        done = Signal(object)
        failed = Signal(str)

        def run(self) -> None:
            try:
                self.done.emit(check())
            except Exception as exc:  # noqa: BLE001 – Netz/Sperre: Stand beibehalten, nächstes Intervall
                self.failed.emit(str(exc))

    class Tray(QObject):
        def __init__(self, app: QApplication) -> None:
            super().__init__()
            self.app = app
            self.found: dict[str, list[tuple[str, str]]] | None = None
            self.notified = load_notified()
            self.worker: Worker | None = None
            self.child: subprocess.Popen | None = None
            self.base = QPixmap(str(ICON))

            self.tray = QSystemTrayIcon(QIcon(self.base), app)
            menu = QMenu()
            self.status = QAction(_('Prüfe auf Updates …'))
            self.status.setEnabled(False)
            menu.addAction(self.status)
            menu.addSeparator()
            open_act = QAction(_('Update Planner öffnen'))
            open_act.triggered.connect(self.open_planner)
            menu.addAction(open_act)
            self.check_act = QAction(_('Jetzt prüfen'))
            self.check_act.triggered.connect(lambda: self.start_check(manual=True))
            menu.addAction(self.check_act)
            menu.addSeparator()
            quit_act = QAction(_('Beenden'))
            quit_act.triggered.connect(app.quit)
            menu.addAction(quit_act)
            self.menu = menu                      # Referenz halten, sonst räumt Python das Menü ab
            self.tray.setContextMenu(menu)
            self.tray.setToolTip('CachyOS Update Planner')
            self.tray.activated.connect(self.on_activated)
            self.tray.messageClicked.connect(self.open_planner)
            self.tray.show()

            self.timer = QTimer(self)
            self.timer.timeout.connect(self.start_check)
            self.timer.start(interval * 60 * 1000)
            QTimer.singleShot(first_delay * 1000, self.start_check)

            # Nach einem Update (pacman.log ändert sich) sofort neu zählen
            self.debounce = QTimer(self)
            self.debounce.setSingleShot(True)
            self.debounce.timeout.connect(self.start_check)
            self.watcher = QFileSystemWatcher([str(PACMAN_LOG)] if PACMAN_LOG.exists() else [], self)
            self.watcher.fileChanged.connect(self.on_log_changed)
            # Hat der Planer seine Datenbank neu synchronisiert, ist er aktueller als wir → neu zählen
            sync = config.CHECKUP_DB / 'sync'
            if sync.is_dir():
                self.watcher.addPath(str(sync))
                self.watcher.directoryChanged.connect(lambda _p: self.debounce.start(20_000))
            self.menu.aboutToShow.connect(self.refresh_if_stale)
            self.last_check = 0.0

        def refresh_if_stale(self) -> None:
            if time.monotonic() - self.last_check > 300:    # Menü geöffnet und Stand älter als 5 Minuten
                self.start_check()

        def on_log_changed(self, path: str) -> None:
            if path not in self.watcher.files() and Path(path).exists():
                self.watcher.addPath(path)         # Rotation ersetzt die Datei
            self.debounce.start(20_000)

        def on_activated(self, reason) -> None:
            if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
                self.open_planner()

        def open_planner(self) -> None:
            if self.child and self.child.poll() is None:
                return                             # läuft schon (Fokus setzen kann die App unter Wayland nicht)
            try:
                self.child = subprocess.Popen(planner_command(), stdin=subprocess.DEVNULL, start_new_session=True)
                QTimer.singleShot(5000, self.watch_child)
            except OSError as exc:
                self.tray.showMessage('CachyOS Update Planner', _('Start fehlgeschlagen: {err}', err=exc),
                                      QSystemTrayIcon.MessageIcon.Warning, 8000)

        def watch_child(self) -> None:
            if self.child and self.child.poll() is not None:
                self.child = None
                self.start_check()                  # Fenster wurde geschlossen (evtl. nach einem Update)
            elif self.child:
                QTimer.singleShot(5000, self.watch_child)

        def start_check(self, manual: bool = False) -> None:
            if self.worker and self.worker.isRunning():
                return
            self.check_act.setEnabled(False)
            self.status.setText(_('Prüfe auf Updates …'))
            self.worker = Worker()
            self.worker.done.connect(lambda found: self.on_result(found, manual))
            self.worker.failed.connect(lambda err: self.on_failed(err, manual))
            self.worker.finished.connect(lambda: self.check_act.setEnabled(True))
            self.worker.start()

        def on_failed(self, err: str, manual: bool) -> None:
            log.warning('Prüfung fehlgeschlagen: %s', err)
            self.status.setText(_('Prüfung fehlgeschlagen'))
            if manual:
                self.tray.showMessage('CachyOS Update Planner', _('Prüfung fehlgeschlagen: {err}', err=err[:200]),
                                      QSystemTrayIcon.MessageIcon.Warning, 8000)

        def on_result(self, found: dict, manual: bool) -> None:
            self.found = found
            self.last_check = time.monotonic()
            n = total(found)
            title, text = summary(found)
            self.status.setText(title)
            self.tray.setToolTip(f'CachyOS Update Planner\n{title}')
            self.tray.setIcon(QIcon(self.badge(n)))
            sig = signature(found)
            fresh = news(sig, self.notified)
            if fresh:
                self.tray.showMessage(title, text, QSystemTrayIcon.MessageIcon.Information, 15000)
            elif manual:
                self.tray.showMessage(title, text, QSystemTrayIcon.MessageIcon.Information, 6000)
            self.notified = sig                    # Erledigte Updates vergessen, damit spätere neue Versionen wieder melden
            save_notified(sig)

        def badge(self, n: int) -> QPixmap:
            """Symbol mit roter Zahl, wenn Updates anstehen."""
            pm = QPixmap(self.base)
            if n <= 0 or pm.isNull():
                return pm
            size = pm.width()
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            d = int(size * 0.55)
            x, y = size - d, 0
            p.setBrush(QColor('#e5484d'))
            p.setPen(QColor('#ffffff'))
            p.drawEllipse(x, y, d, d)
            font = QFont()
            font.setBold(True)
            font.setPixelSize(int(d * (0.6 if n < 100 else 0.42)))
            p.setFont(font)
            p.drawText(x, y, d, d, 0x84, str(n) if n < 100 else '99+')    # AlignCenter
            p.end()
            return pm

    app = QApplication(sys.argv[:1])
    app.setApplicationName('update-planner-tray')
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.warning('Kein Systemabschnitt (StatusNotifier) gefunden – warte, bis er erscheint')
    tray = Tray(app)                                  # noqa: F841 – hält die Objekte am Leben
    return app.exec()


def main() -> None:
    parser = argparse.ArgumentParser(prog='update-planner-tray', description=_('Tray-Symbol für den Update Planner'))
    parser.add_argument('--interval', type=int, default=DEFAULT_INTERVAL, metavar=_('MINUTEN'),
                        help=_('Prüfintervall in Minuten (Standard: {n})', n=DEFAULT_INTERVAL))
    parser.add_argument('--delay', type=int, default=FIRST_CHECK_DELAY, metavar=_('SEKUNDEN'),
                        help=_('Wartezeit bis zur ersten Prüfung (Standard: {n})', n=FIRST_CHECK_DELAY))
    parser.add_argument('--lang', choices=i18n.SUPPORTED, help=_('Sprache erzwingen (Standard: Systemsprache)'))
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    parser.add_argument('-v', '--verbose', action='store_true', help=_('ausführliches Protokoll'))
    args = parser.parse_args()
    i18n.set_language(args.lang)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    if os.geteuid() == 0:
        sys.exit(_('update-planner darf nicht als root gestartet werden (sudo wird bei Bedarf abgefragt).'))
    lock = lock_instance()
    if lock is None:
        sys.exit(_('Das Tray-Symbol läuft bereits.'))
    sys.exit(run_tray(max(5, args.interval), max(0, args.delay)))


if __name__ == '__main__':
    main()
