import json

from update_planner import tray


def test_signature_and_news():
    found = {'repo': [('linux', '7.0-1'), ('git', '2.1-1')], 'flatpak': [('org.x.App', '1.2')]}
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
