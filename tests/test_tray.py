import json

from update_planner import tray


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

    import pytest
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
