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
import math
import os
import re
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
MAX_AHEAD = 31 * 86400      # weiter in der Zukunft wird nichts geplant (und keine so gespeicherte Datei angenommen)
CLOCK_SKEW = 300            # Toleranz für `created` in der Zukunft (Uhr verstellt)
MAX_LIST = 5000             # höchstens so viele Einträge je Liste in der Datei
# Erlaubte Optionen in der Datei und ihre Typen – genau das, was der Dialog speichert (dialogs.context_dialog)
OPTS: dict[str, type | tuple[type, ...]] = {
    'snapshot': bool, 'aur': bool, 'flatpak': bool, 'dry_run': bool, 'sync': bool, 'orphans': bool, 'cache': bool,
    'hold': (int, float), 'cache_keep': int,
}
NAME_RE = r'[a-zA-Z0-9@_+][a-zA-Z0-9@._+/-]*'     # Paketnamen und Flatpak-IDs


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
        if at > now + MAX_AHEAD:
            raise ValueError(_('Höchstens {days} Tage im Voraus planbar', days=MAX_AHEAD // 86400))
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


def _names(value) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_LIST:
        raise ValueError('list')
    if not all(isinstance(x, str) and len(x) <= 255 and re.fullmatch(NAME_RE, x) for x in value):
        raise ValueError('name')
    return list(value)


def parse(data, now: float | None = None) -> Schedule:
    """Gespeicherten Plan streng prüfen; ValueError bei allem, was der Dialog so nicht geschrieben hätte.

    Die Datei ist 0600, aber ein anderer Prozess desselben Benutzers könnte sie ändern. Deshalb: nur bekannte
    Optionen mit passendem Typ und Wertebereich, endliche Zeitstempel, `created` nicht in der Zukunft und `at`
    höchstens MAX_AHEAD nach `created` bzw. jetzt. Ausgeführt wird ohnehin nur nach sudo-Passwort im Fenster."""
    now = time.time() if now is None else now
    if not isinstance(data, dict):
        raise ValueError('schedule')
    at, created = data.get('at'), data.get('created', 0)
    if isinstance(at, bool) or isinstance(created, bool) or not isinstance(at, int | float) \
            or not isinstance(created, int | float):
        raise ValueError('time type')
    at, created = float(at), float(created)
    if not (math.isfinite(at) and math.isfinite(created)) or at <= 0 or created < 0:
        raise ValueError('time value')
    if created > now + CLOCK_SKEW or at > now + MAX_AHEAD or (created and at > created + MAX_AHEAD):
        raise ValueError('time range')
    opts = data.get('opts', {})
    if not isinstance(opts, dict) or set(opts) - set(OPTS):
        raise ValueError('opts keys')
    for k, v in opts.items():
        if isinstance(v, bool) and OPTS[k] is not bool or not isinstance(v, OPTS[k]):
            raise ValueError(f'opts {k}')
    if 'hold' in opts and not 0 <= opts['hold'] <= 100 or 'cache_keep' in opts and not 0 <= opts['cache_keep'] <= 9:
        raise ValueError('opts range')
    sources = data.get('sources', [])
    if not isinstance(sources, list) or not set(sources) <= {'repo', 'aur', 'flatpak'}:
        raise ValueError('sources')
    reviewed = data.get('reviewed', {})
    if not isinstance(reviewed, dict):
        reviewed = {}                            # alte Liste ohne Hash → verworfen
    if len(reviewed) > MAX_LIST or not all(isinstance(k, str) and re.fullmatch(NAME_RE, k) and isinstance(v, str)
                                           and len(v) <= 128 for k, v in reviewed.items()):
        raise ValueError('reviewed')
    return Schedule(at=at, opts=dict(opts), deselected=_names(data.get('deselected', [])),
                    selected_extra=_names(data.get('selected_extra', [])), sources=list(sources),
                    reviewed=dict(reviewed), created=created)


def load() -> Schedule | None:
    """Gespeicherten Plan lesen (einmal pro Prozess); fehlerhafte oder unplausible Dateien werden ignoriert."""
    global _current, _loaded
    if not _loaded:
        _loaded = True
        try:
            _current = parse(json.loads(_path().read_text()))
        except FileNotFoundError:
            _current = None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning('Geplantes Update verworfen (Datei ungültig: %s)', exc)
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
