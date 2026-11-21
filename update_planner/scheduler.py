"""Geplantes Update: Zeitpunkt wählen, speichern, Fälligkeit erkennen.

Es wird nichts Geheimes gespeichert – insbesondere kein Passwort. Zum geplanten Zeitpunkt fragt die
Oberfläche (die dafür offen sein muss) das sudo-Passwort ab, wie bei einem manuell gestarteten Update.
Gespeichert werden nur die Optionen und die *abgewählten* Updates: Was bis dahin neu hinzukommt, ist
über die Standardauswahl automatisch dabei.

Fälligkeit wird per Wanduhr (time.time()) geprüft, damit ein Suspend den Plan nicht verschiebt.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from datetime import time as dtime

from . import config
from .i18n import _

log = logging.getLogger(__name__)

SNOOZE = 15 * 60            # „Später“: so lange warten
CHOICES = ('now', '1h', '2h', 'time')


@dataclass
class Schedule:
    at: float                                   # Unix-Zeit
    opts: dict = field(default_factory=dict)
    deselected: list[str] = field(default_factory=list)
    selected_extra: list[str] = field(default_factory=list)   # bewusst zusätzlich gewählt (nicht in der Standardauswahl)
    sources: list[str] = field(default_factory=list)
    reviewed: dict[str, str] = field(default_factory=dict)   # {Paket: Hash des geprüften PKGBUILD}
    created: float = 0.0


_current: Schedule | None = None
_loaded = False
_claimed_by: str | None = None
_notified: float | None = None                  # `at`, für das schon eine Desktop-Meldung kam


def _path():
    return config.CONFIG / 'schedule.json'


def compute_at(choice: str, *, day: date | None = None, clock: dtime | None = None, now: float | None = None) -> float | None:
    """Zeitpunkt (Unix-Zeit) für die Auswahl; None = sofort. Vergangenheit → ValueError."""
    now = time.time() if now is None else now
    if choice == 'now':
        return None
    if choice == '1h':
        return now + 3600
    if choice == '2h':
        return now + 7200
    if choice == 'time':
        if day is None or clock is None:
            raise ValueError(_('Bitte Datum und Uhrzeit angeben'))
        at = datetime.combine(day, clock).timestamp()
        if at <= now:
            raise ValueError(_('Der Zeitpunkt liegt in der Vergangenheit'))
        return at
    raise ValueError(f'unknown choice {choice!r}')


def format_at(at: float, now: float | None = None) -> str:
    dt = datetime.fromtimestamp(at)
    today = datetime.fromtimestamp(time.time() if now is None else now).date()
    return dt.strftime('%H:%M') if dt.date() == today else dt.strftime('%d.%m. %H:%M')


def _write(sch: Schedule | None) -> None:
    path = _path()
    if sch is None:
        path.unlink(missing_ok=True)
        return
    config.ensure_dirs()
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(asdict(sch), f)
    tmp.replace(path)


def load() -> Schedule | None:
    """Gespeicherten Plan lesen (einmal pro Prozess); fehlerhafte Dateien werden ignoriert."""
    global _current, _loaded
    if not _loaded:
        _loaded = True
        try:
            data = json.loads(_path().read_text())
            _current = Schedule(at=float(data['at']), opts=dict(data.get('opts', {})),
                                deselected=[str(x) for x in data.get('deselected', [])],
                                selected_extra=[str(x) for x in data.get('selected_extra', [])],
                                sources=[str(x) for x in data.get('sources', [])],
                                reviewed={str(k): str(v) for k, v in data.get('reviewed', {}).items()}
                                if isinstance(data.get('reviewed'), dict) else {},   # alte Liste ohne Hash → verworfen
                                created=float(data.get('created', 0)))
        except (OSError, ValueError, KeyError, TypeError):
            _current = None
    return _current


def current() -> Schedule | None:
    return load()


def schedule(at: float, opts: dict, deselected, sources, reviewed, selected_extra=()) -> Schedule:
    """Plan speichern. Schlägt das Schreiben fehl (OSError), bleibt der bisherige Zustand unverändert.

    Standardauswahl zum Zeitpunkt = `default_selection() − deselected ∪ selected_extra`: Neu hinzugekommene Updates
    sind dabei, abgewählte nicht, und was bewusst zusätzlich gewählt wurde (z. B. Risiko ≥ 80, in der Standardauswahl
    ausgeschlossen) geht nicht verloren."""
    global _current, _loaded, _claimed_by, _notified
    opts = {k: v for k, v in opts.items() if k != 'at'}
    sch = Schedule(at=at, opts=opts, deselected=sorted(deselected), selected_extra=sorted(selected_extra),
                   sources=sorted(sources), reviewed=dict(reviewed), created=time.time())
    _write(sch)
    _current, _loaded, _claimed_by, _notified = sch, True, None, None
    return sch


def cancel() -> None:
    global _current, _loaded, _claimed_by, _notified
    _current, _loaded, _claimed_by, _notified = None, True, None, None
    _write(None)


def snooze(seconds: float = SNOOZE, now: float | None = None) -> None:
    sch = load()
    if sch:
        sch.at = (time.time() if now is None else now) + seconds
        _write(sch)


def due(now: float | None = None) -> bool:
    sch = load()
    return bool(sch) and (time.time() if now is None else now) >= sch.at


def claim(client_id: str) -> bool:
    """Ein Fenster „beansprucht“ den fälligen Plan, damit nicht mehrere Fenster gleichzeitig fragen."""
    global _claimed_by
    if _claimed_by is None or _claimed_by == client_id:
        _claimed_by = client_id
        return True
    return False


def release(client_id: str) -> None:
    global _claimed_by
    if _claimed_by == client_id:
        _claimed_by = None


def claimed() -> bool:
    return _claimed_by is not None


def notify_due() -> None:
    """Desktop-Benachrichtigung (einmal je Plan), ohne Shell; fehlt notify-send, passiert nichts."""
    global _notified
    sch = load()
    exe = shutil.which('notify-send')
    if not sch or _notified == sch.at or not exe:
        return
    _notified = sch.at
    try:
        subprocess.Popen([exe, '-a', 'CachyOS Update Planner', '-i', 'system-software-update',
                          _('Geplantes Update ist fällig'), _('Zum Starten das Fenster des Update Planners öffnen.')],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        log.info('notify-send fehlgeschlagen: %s', exc)
