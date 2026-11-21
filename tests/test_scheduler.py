"""Geplantes Update: Zeitberechnung, Persistenz ohne Geheimnisse, Claim/Freigabe, Standardauswahl."""

import asyncio
import json
import os
import time
from datetime import date, datetime
from datetime import time as dtime

import pytest

from update_planner import catalog as md
from update_planner import config, scheduler
from update_planner import state as st


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'CONFIG', tmp_path / 'cfg')
    monkeypatch.setattr(config, 'CACHE', tmp_path / 'cache')
    monkeypatch.setattr(config, 'HTTP_CACHE', tmp_path / 'cache' / 'http')
    monkeypatch.setattr(config, 'AUR_BUILD', tmp_path / 'cache' / 'aur')
    scheduler.cancel()
    scheduler._loaded = False
    yield
    scheduler.cancel()


NOW = datetime(2026, 11, 21, 17, 30).timestamp()


def test_compute_relative_and_now():
    assert scheduler.compute_at('now', now=NOW) is None
    assert scheduler.compute_at('1h', now=NOW) == NOW + 3600
    assert scheduler.compute_at('2h', now=NOW) == NOW + 7200


def test_compute_clock_time_and_past_rejected():
    at = scheduler.compute_at('time', day=date(2026, 11, 21), clock=dtime(23, 15), now=NOW)
    assert datetime.fromtimestamp(at) == datetime(2026, 11, 21, 23, 15)
    with pytest.raises(ValueError):
        scheduler.compute_at('time', day=date(2026, 11, 21), clock=dtime(17, 0), now=NOW)       # Vergangenheit
    with pytest.raises(ValueError):
        scheduler.compute_at('time', day=date(2026, 11, 21), clock=dtime(17, 30), now=NOW)      # genau jetzt
    with pytest.raises(ValueError):
        scheduler.compute_at('time', day=None, clock=dtime(23, 0), now=NOW)
    next_day = scheduler.compute_at('time', day=date(2026, 11, 22), clock=dtime(1, 0), now=NOW)
    assert next_day > NOW
    with pytest.raises(ValueError):
        scheduler.compute_at('tomorrow')


def test_format_shows_date_only_for_other_days():
    at = datetime(2026, 11, 21, 18, 30).timestamp()
    assert scheduler.format_at(at, now=NOW) == '18:30'
    assert scheduler.format_at(at + 86400, now=NOW) == '22.11. 18:30'


def test_persistence_without_secrets_and_restore():
    opts = {'snapshot': True, 'aur': True, 'flatpak': False, 'hold': 100, 'dry_run': False, 'at': 123.0,
            'password': 'S3cr3t'}                      # selbst ein versehentliches Feld würde nicht „at“ doppeln
    sch = scheduler.schedule(NOW + 600, {k: v for k, v in opts.items() if k != 'password'},
                             {'b', 'a'}, {'repo', 'aur'}, {'x'})
    path = config.CONFIG / 'schedule.json'
    assert oct(os.stat(path).st_mode & 0o777) == '0o600'
    data = json.loads(path.read_text())
    assert 'at' not in data['opts'] and 'S3cr3t' not in path.read_text() and 'password' not in path.read_text().lower()
    assert data['deselected'] == ['a', 'b'] and data['sources'] == ['aur', 'repo']
    # neuer Prozess: aus der Datei wiederherstellen
    scheduler._current, scheduler._loaded = None, False
    again = scheduler.current()
    assert again == sch


def test_corrupt_file_is_ignored():
    config.CONFIG.mkdir(parents=True)
    (config.CONFIG / 'schedule.json').write_text('{not json')
    scheduler._loaded = False
    assert scheduler.current() is None and not scheduler.due()


def test_due_by_wall_clock_and_snooze():
    scheduler.schedule(NOW + 60, {}, [], ['repo'], [])
    assert not scheduler.due(now=NOW) and scheduler.due(now=NOW + 60)
    scheduler.snooze(900, now=NOW + 61)
    assert not scheduler.due(now=NOW + 61 + 899) and scheduler.due(now=NOW + 61 + 900)
    scheduler._current, scheduler._loaded = None, False                  # Snooze ist auch gespeichert
    assert scheduler.current().at == NOW + 61 + 900


def test_overdue_plan_is_due_after_restart():
    scheduler.schedule(time.time() - 3 * 3600, {}, [], ['repo'], [])
    scheduler._current, scheduler._loaded = None, False
    assert scheduler.due()


def test_claim_and_release():
    scheduler.schedule(NOW, {}, [], ['repo'], [])
    assert scheduler.claim('a') and scheduler.claim('a')              # idempotent für dasselbe Fenster
    assert not scheduler.claim('b')                                   # zweites Fenster bekommt ihn nicht
    scheduler.release('b')                                            # fremde Freigabe wirkt nicht
    assert not scheduler.claim('b')
    scheduler.release('a')
    assert scheduler.claim('b')


def test_cancel_removes_file():
    scheduler.schedule(NOW, {}, [], ['repo'], [])
    assert (config.CONFIG / 'schedule.json').exists()
    scheduler.cancel()
    assert not (config.CONFIG / 'schedule.json').exists() and scheduler.current() is None


def test_deselected_logic_new_updates_are_included(monkeypatch):
    """Gespeichert werden abgewählte Updates; was später neu erscheint, ist per Standardauswahl dabei."""
    ups = [dict(name=n, source='repo', risk=10) for n in ('a', 'b', 'c')]
    monkeypatch.setattr(md, 'UPDATES', ups)
    monkeypatch.setattr(md, 'SCANS', {})
    s = st.State(sources={'repo'})
    s.selected = {'a', 'c'}                                           # „b“ wurde abgewählt
    deselected = {u['name'] for u in s.visible_updates()} - s.selected
    assert deselected == {'b'}
    scheduler.schedule(NOW, {}, deselected, s.sources, [])
    # später: „d“ kommt dazu, „c“ ist weg
    monkeypatch.setattr(md, 'UPDATES', [dict(name=n, source='repo', risk=10) for n in ('a', 'b', 'd')])
    sel = st.default_selection() - set(scheduler.current().deselected)
    assert sel == {'a', 'd'}


# ------------------------------------------------------------------ Planung: Auswahl, Fehlerfälle, Lauf zum Zeitpunkt

def test_selected_extra_survives_default_selection(monkeypatch):
    """Bewusst zusätzlich gewählte Updates (Risiko ≥ 80) fallen zum Zeitpunkt nicht aus der Auswahl heraus."""
    ups = [dict(name='a', source='repo', risk=10), dict(name='big', source='repo', risk=85)]
    monkeypatch.setattr(md, 'UPDATES', ups)
    monkeypatch.setattr(md, 'SCANS', {})
    assert 'big' not in st.default_selection()                         # Standardauswahl schließt es aus
    scheduler.schedule(NOW, {}, [], ['repo'], [], selected_extra={'big'})
    scheduler._current, scheduler._loaded = None, False                # aus der Datei lesen
    sch = scheduler.current()
    assert sch.selected_extra == ['big']
    assert (st.default_selection() - set(sch.deselected)) | set(sch.selected_extra) == {'a', 'big'}


def test_failed_write_keeps_previous_plan(monkeypatch):
    scheduler.schedule(NOW, {}, [], ['repo'], [])

    real = scheduler._write

    def boom(sch):
        if sch is not None:                                           # Löschen (Aufräumen nach dem Test) bleibt möglich
            raise OSError('disk full')
        real(sch)
    monkeypatch.setattr(scheduler, '_write', boom)
    with pytest.raises(OSError):
        scheduler.schedule(NOW + 99, {}, [], ['repo'], [])
    assert scheduler.current().at == NOW                              # kein halb übernommener Plan im Speicher


@pytest.fixture
def dialogs_env(monkeypatch):
    """dialogs ohne echte Oberfläche: Meldungen und Dialoge ersetzen."""
    from update_planner import dialogs
    monkeypatch.setattr(dialogs.ui, 'notify', lambda *a, **k: None)
    monkeypatch.setattr(dialogs, '_REFRESH_POLL', 0.01)
    monkeypatch.setattr(md, 'SYSTEM', {})
    monkeypatch.setitem(md.STATUS, 'state', 'ready')
    return dialogs


def test_execute_resets_busy_when_on_start_fails(dialogs_env):
    from update_planner import executor

    async def fake_progress(plan, password, notes):
        return executor.Result()
    dialogs_env.progress_dialog = fake_progress
    state = st.State()
    plan = executor.Plan(repo=False, snapshot=False, flatpak_install=['org.a.A'], dry_run=True)

    def boom():
        raise OSError('read-only config')
    with pytest.raises(OSError):
        asyncio.run(dialogs_env._execute(state, plan, [], on_start=boom))
    assert state.busy is False                                         # nicht für immer „Update läuft“
    started = []
    assert asyncio.run(dialogs_env._execute(state, plan, [], on_start=lambda: started.append(1))) is True
    assert started == [1] and state.busy is False


def test_run_scheduled_waits_for_running_refresh_and_keeps_window_state(dialogs_env, monkeypatch):
    ups = [dict(id=n, name=n, source='repo', old='1', new='2', size=1.0, repo='extra', reasons=[], risk=r)
           for n, r in (('a', 10), ('b', 10), ('big', 85))]
    monkeypatch.setattr(md, 'UPDATES', ups)
    monkeypatch.setattr(md, 'SCANS', {})
    monkeypatch.setattr(md, 'GROUPS', [])
    opts = {'snapshot': False, 'aur': True, 'flatpak': True, 'hold': 100, 'dry_run': True, 'sync': False,
            'orphans': False, 'cache': False, 'cache_keep': 2}
    scheduler.schedule(NOW, opts, deselected={'b'}, sources={'repo'}, reviewed={'r2'}, selected_extra={'big'})
    state = st.State(sources={'repo', 'aur', 'flatpak'}, selected={'a'}, reviewed={'r1'})
    seen = {}

    async def due(sch):
        return 'start'

    async def refresh_in_progress():
        """Wie md.refresh(), wenn schon eine Prüfung läuft: kehrt sofort zurück, der Stand ist noch „loading“."""
        md.STATUS['state'] = 'loading'
        asyncio.get_running_loop().call_later(0.1, lambda: md.STATUS.update(state='ready'))

    async def fake_execute(state_, plan, notes, on_start=None):
        seen['plan'] = plan
        return True
    dialogs_env.due_dialog, dialogs_env._execute = due, fake_execute
    monkeypatch.setattr(md, 'refresh', refresh_in_progress)
    asyncio.run(dialogs_env.run_scheduled(state))
    plan = seen['plan']                                                # lief weiter, statt „nicht geladen“ zu melden
    assert scheduler.current().at == NOW                               # und hat den Plan nicht verschoben
    assert plan.hold == ['b'] and 'big' not in plan.hold              # abgewählt → zurückgehalten, zusätzlich gewählt → dabei
    assert state.sources == {'repo', 'aur', 'flatpak'} and state.selected == {'a'} and state.reviewed == {'r1'}


def test_start_install_keeps_selection_after_dry_run(dialogs_env, monkeypatch):
    state = st.State()
    items = [dict(name='org.a.A', source='flatpak', origin='', flags=[], base='org.a.A', sec={})]
    ran = []

    async def fake_execute(state_, plan, notes, on_start=None):
        ran.append(plan)
        return True
    dialogs_env._execute = fake_execute
    for dry, expected in ((True, False), (False, True)):
        async def dialog(items_, dry=dry):
            return {'snapshot': False, 'dry_run': dry, 'flatpak_user': True}
        dialogs_env.install_dialog = dialog
        assert asyncio.run(dialogs_env.start_install(state, items)) is expected
    assert [p.dry_run for p in ran] == [True, False] and ran[0].flatpak_install == ['org.a.A']
