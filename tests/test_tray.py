import json
import os
from pathlib import Path

import pytest

from update_planner import __version__, tray


def test_signature_and_news():
    found = {'repo': [('linux', '7.0-1'), ('git', '2.1-1')], 'aur': [], 'flatpak': [('org.x.App', '1.2')]}
    sig = tray.signature(found)
    assert sig == {'repo:linux=7.0-1', 'repo:git=2.1-1', 'flatpak:org.x.App=1.2'}
    assert tray.total(found) == 3
    assert tray.news(sig, sig) == set()                       # nichts Neues → kein Popup
    newer = tray.signature({'repo': [('linux', '7.0-2')], 'flatpak': []})
    assert tray.news(newer, sig) == {'repo:linux=7.0-2'}      # neue Version desselben Pakets meldet wieder


def test_summary():
    assert tray.summary({'repo': [], 'flatpak': []})[0] == 'Keine Updates'
    title, text = tray.summary({'repo': [('a', '1')] * 7, 'flatpak': [('f', '2')]})
    assert title == '8 Updates verfügbar'
    assert 'Repo: 7' in text and 'Flatpak: 1' in text and text.endswith('…')
    assert tray.summary({'repo': [('a', '1')], 'flatpak': []})[0] == '1 Update verfügbar'


def test_repo_updates_parses_checkupdates(monkeypatch, tmp_path):
    monkeypatch.setattr(tray.config, 'ensure_dirs', lambda: None)
    seen = {}

    def fake_run(cmd, **kw):
        seen['cmd'], seen['env'] = cmd, kw['env']
        return 'linux 6.1-1 -> 7.0-1\nweird line\nfoo 1:2-1 -> 1:3-1\n'
    monkeypatch.setattr(tray, 'run', fake_run)
    assert tray.repo_updates() == [('linux', '7.0-1'), ('foo', '1:3-1')]
    assert seen['env']['CHECKUPDATES_DB'] == str(tray.TRAY_DB)
    assert tray.TRAY_DB != tray.config.CHECKUP_DB             # eigene DB, kein Konflikt mit dem Fenster


def test_notified_roundtrip(tmp_path):
    path = tmp_path / 'sub' / 'tray.json'
    assert tray.load_notified(path) == set()
    tray.save_notified({'repo:a=1'}, path)
    assert tray.load_notified(path) == {'repo:a=1'}
    assert oct(path.stat().st_mode & 0o777) == '0o600'
    path.write_text('kaputt')
    assert tray.load_notified(path) == set()
    path.write_text(json.dumps({'notified': ['x', 5]}))
    assert tray.load_notified(path) == {'x'}


def test_single_instance(tmp_path, monkeypatch):
    monkeypatch.setattr(tray.config, 'ensure_dirs', lambda: None)
    path = tmp_path / 'tray.lock'
    first = tray.lock_instance(path)
    assert first is not None
    assert tray.lock_instance(path) is None
    first.close()
    assert tray.lock_instance(path) is not None


def test_aur_updates(monkeypatch):
    monkeypatch.setattr(tray, 'run', lambda *a, **k: 'foo 1.0-1\nbar-git r5-1\n')

    async def fake_info(names, ttl=0):
        return {'foo': {'Name': 'foo', 'Version': '1.1-1'}, 'bar-git': {'Name': 'bar-git', 'Version': 'r9-1'}}
    monkeypatch.setattr(tray.aur, 'info', fake_info)
    assert tray.aur_updates() == [('foo', '1.1-1')]            # VCS-Paket wird nicht verglichen


def test_summary_counts_aur():
    title, text = tray.summary({'repo': [('a', '1')], 'aur': [('b', '2')], 'flatpak': []})
    assert title == '2 Updates verfügbar' and 'AUR: 1' in text


def test_menu_keeps_all_actions(monkeypatch):
    """Regression: QActions ohne Parent wurden abgeräumt – 'Öffnen' und 'Beenden' fehlten im Menü."""
    import gc

    pytest.importorskip('PySide6')
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    captured = {}
    orig_set = QSystemTrayIcon.setContextMenu

    def grab(self, menu):
        captured['menu'] = menu
        return orig_set(self, menu)

    def fake_exec():
        gc.collect()                                      # hätte die parentlosen Actions entfernt
        captured['texts'] = [a.text() for a in captured['menu'].actions() if not a.isSeparator()]
        captured['quit'] = next(a for a in captured['menu'].actions() if a.text() == tray._('Beenden'))
        return 0

    monkeypatch.setattr(QSystemTrayIcon, 'setContextMenu', grab)
    monkeypatch.setattr(QApplication, 'exec', staticmethod(fake_exec))
    monkeypatch.setattr(tray, 'load_notified', lambda: set())
    assert tray.run_tray(60, 3600) == 0
    texts = captured['texts']
    assert len(texts) == 4
    assert tray._('Update Planner öffnen') in texts
    assert tray._('Jetzt prüfen') in texts
    assert tray._('Beenden') in texts


@pytest.fixture
def status_env(monkeypatch, tmp_path):
    """Prüfstand, Sperre und pacman.log liegen im Testverzeichnis; gezählt werden die echten Abfragen."""
    monkeypatch.setattr(tray.config, 'ensure_dirs', lambda: None)
    monkeypatch.setattr(tray.config, 'CHECKUP_DB', tmp_path / 'checkup-db')
    monkeypatch.setattr(tray, 'STATUS_FILE', tmp_path / 'status.json')
    monkeypatch.setattr(tray, 'CHECK_LOCK', tmp_path / 'check.lock')
    monkeypatch.setattr(tray, 'PACMAN_LOG', tmp_path / 'pacman.log')
    calls = []
    monkeypatch.setattr(tray, 'repo_updates', lambda: calls.append(1) or [('linux', '7.0-1')])
    monkeypatch.setattr(tray, 'aur_updates', lambda: [])
    monkeypatch.setattr(tray, 'flatpak_updates', lambda: [])
    return calls


def test_status_uses_cache_until_stale_or_forced(status_env, tmp_path):
    first = tray.status(3600)
    assert first['repo'] == [('linux', '7.0-1')] and first['checked'] > 0 and 'error' not in first
    assert tray.status(3600) == first and len(status_env) == 1
    assert oct(tray.STATUS_FILE.stat().st_mode & 0o777) == '0o600'
    tray.status(3600, force=True)
    assert len(status_env) == 2
    tray.PACMAN_LOG.write_text('installed')                   # pacman hat seit der Prüfung etwas getan
    later = tray.load_status()['checked'] + 5
    os.utime(tray.PACMAN_LOG, (later, later))
    tray.status(3600)
    assert len(status_env) == 3


def test_status_keeps_old_state_when_check_fails(status_env, monkeypatch):
    old = tray.status(3600)

    def offline():
        raise OSError('offline')
    monkeypatch.setattr(tray, 'repo_updates', offline)
    failed = tray.status(3600, force=True)
    assert failed['error'] == 'offline' and failed['repo'] == old['repo'] and failed['checked'] == old['checked']
    tray.STATUS_FILE.write_text('kaputt')
    assert tray.status(3600) == {'checked': 0.0, 'repo': [], 'aur': [], 'flatpak': [], 'error': 'offline'}


def test_status_command_prints_json_without_tray_lock(status_env, monkeypatch, capsys):
    monkeypatch.setattr(tray.sys, 'argv', ['update-planner-tray', '--status', '--lang', 'de'])
    monkeypatch.setattr(tray.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(tray, 'lock_instance', lambda: pytest.fail('--status braucht keine Einzelinstanz'))
    tray.main()
    assert json.loads(capsys.readouterr().out)['repo'] == [['linux', '7.0-1']]


def test_plasmoid_metadata_matches_release():
    root = Path(__file__).parent.parent
    meta = json.loads((root / 'plasmoid' / 'metadata.json').read_text())
    assert meta['KPlugin']['Version'] == __version__
    assert meta['KPlugin']['Id'] in (root / 'packaging' / 'install-desktop.sh').read_text()
    assert (root / 'plasmoid' / 'contents' / 'ui' / 'main.qml').is_file()
