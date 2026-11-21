import asyncio
import json

import pytest

from update_planner import analysis, catalog, i18n
from update_planner.sources import discover, flatpak, news, pacman, security
from update_planner.sysutil import valid_flatpak, valid_name, vercmp, version_parts

PACMAN_SI = """Repository      : core
Name            : glibc
Version         : 2.44-3
Depends On      : linux-api-headers>=4.10  tzdata
                  filesystem
Provides        : None
Download Size   : 10.60 MiB

Repository      : extra
Name            : glibc
Version         : 2.44-1
Download Size   : 1.00 MiB
"""


def test_parse_info_blocks_and_continuation():
    blocks = pacman.parse_info(PACMAN_SI)
    assert len(blocks) == 2
    assert blocks[0]['Depends On'].split() == ['linux-api-headers>=4.10', 'tzdata', 'filesystem']
    assert pacman._mib(blocks[0]['Download Size']) == pytest.approx(10.6)
    assert pacman._list('None') == []


def test_split_dep():
    assert analysis.split_dep('foo>=1.2') == ('foo', '>=', '1.2')
    assert analysis.split_dep('libbar.so=2-64') == ('libbar.so', '=', '2-64')
    assert analysis.split_dep('sh') == ('sh', None, None)


def test_vercmp_and_version_parts():
    assert vercmp('1.10-1', '1.9-1') > 0
    assert vercmp('1:1.0-1', '2.0-1') > 0
    assert version_parts('1:6.18.0-1') == (1, [6, 18, 0])


def test_version_reasons():
    assert analysis.version_reasons('6.17.7-1', '7.0.0-1')[0][1] == 20
    assert analysis.version_reasons('6.17.7-1', '6.18.0-1')[0][1] == 8
    assert analysis.version_reasons('6.17.7-1', '6.17.7-2')[0][0].startswith('Nur Neubau')


@pytest.mark.parametrize('bad', ['', 'a;rm -rf /', '../etc', '-rf', 'a b', 'x' * 200, '$(id)'])
def test_invalid_package_names_rejected(bad):
    with pytest.raises(ValueError):
        valid_name(bad)


def test_valid_names_accepted():
    for ok in ('glibc', 'lib32-mesa', 'python-yt-dlp-git', 'gtk+', 'xorg-server-xvfb', 'r8168-dkms'):
        assert valid_name(ok) == ok
    assert valid_flatpak('org.freedesktop.Platform//24.08')
    with pytest.raises(ValueError):
        valid_flatpak('org.x; rm')


def test_security_matching():
    groups = [
        dict(name='AVG-1', packages=['a'], status='Vulnerable', severity='High', type='arbitrary-code-execution',
             affected='1.0-1', fixed='1.2-1', issues=['CVE-1']),
        dict(name='AVG-2', packages=['b'], status='Vulnerable', severity='Low', type='dos', affected='1.0-1',
             fixed='1.1-1', issues=[]),
        dict(name='AVG-3', packages=['c'], status='Vulnerable', severity='Medium', type='x', affected='1.0-1',
             fixed=None, issues=[]),
        dict(name='javascript:alert(1)', packages=['a'], status='Vulnerable', severity='High', fixed=None),
    ]
    rel = security.relevant(groups, {'a': '1.0-1', 'b': '1.1-1', 'c': '1.0-1'}, {'a': '1.2-1'})
    by = {r['id']: r for r in rel}
    assert by['AVG-1']['status'] == 'behoben durch Update' and by['AVG-1']['severity'] == 'hoch'
    assert 'AVG-2' not in by                     # bereits behoben installiert
    assert by['AVG-3']['status'] == 'offen – kein Fix'
    assert len(rel) == 2                         # ungültige ID verworfen


def test_news_manual_regex():
    assert news.MANUAL.search('Mkinitcpio >=42 requires manual intervention')
    assert not news.MANUAL.search('CachyOS August 2026 Release')


def test_flatpak_size_parse():
    assert catalog._parse_size('192.5\xa0kB') == pytest.approx(192.5 / 1024)
    assert catalog._parse_size('1,5 GB') == pytest.approx(1536)
    assert catalog._parse_size('') == 0


def test_vercmp_matches_libalpm_rules():
    # Erwartungswerte mit /usr/bin/vercmp (pacman) ermittelt
    cases = [('1.0', '1.0', 0), ('1.0a', '1.0', -1), ('1.0', '1.0.a', -1), ('1.0rc1', '1.0', -1),
             ('1.0-2', '1.0-10', -1), ('1.0', '1.0-5', 0), ('2:1.0', '1:9.9', 1), (':1.0', '0:1.0', 0),
             ('1.00', '1.0', 0), ('1..0', '1.0', 1), ('1.0.1', '1.0a', 1), ('r123.abc', 'r124.abc', -1),
             ('6.17.10-1', '6.17.7-1', 1), ('1.0~rc1', '1.0', 1), ('1_0', '1.0', 0)]
    for a, b, want in cases:
        assert vercmp(a, b) == want, (a, b)
        assert vercmp(b, a) == -want, (b, a)


def test_last_upgrade_reads_from_end(tmp_path):
    from update_planner.sources import pacman
    log = tmp_path / 'pacman.log'
    filler = '[2020-01-01T00:00:00+0100] [ALPM] upgraded foo (1-1 -> 2-1)\n' * 20000   # > 1 MB, mehrere Blöcke
    log.write_text('[2024-05-01T10:00:00+0200] [PACMAN] starting full system upgrade\n' + filler
                   + '[2026-11-10T08:12:00+0100] [PACMAN] starting full system upgrade\n' + filler)
    assert pacman.last_upgrade(log) == '2026-11-10T08:12:00'
    log.write_text(filler)
    assert pacman.last_upgrade(log) is None
    assert pacman.last_upgrade(tmp_path / 'fehlt.log') is None


def test_paccache_summary_parse():
    from update_planner.sources import pacman
    m = pacman.PACCACHE_RE.search('==> finished dry run: 1061 candidates (disk space saved: 6.8 GiB)')
    assert m and m.group(1) == '1061' and m.group(2) == '6.8' and m.group(3) == 'GiB'
    assert pacman.PACCACHE_RE.search('==> no candidate packages found for pruning') is None


def test_vulnerability_index_uses_highest_severity():
    catalog.load_demo()
    catalog.CVES.append(dict(id='AVG-1', package='glibc', severity='kritisch', status='offen – kein Fix'))
    catalog._publish(dict(packages=catalog.PACKAGES, updates=catalog.UPDATES, conflicts=catalog.CONFLICTS,
                          cves=catalog.CVES, news=catalog.NEWS, scans=catalog.SCANS, aur_meta=catalog.AUR_META,
                          groups=catalog.GROUPS, system=catalog.SYSTEM))
    v = catalog.vulnerability('glibc')
    assert v['severity'] == 'kritisch' and v['count'] == 2
    assert catalog.vulnerability('mesa') is None


# ------------------------------------------------------------------ neue Pakete finden (1.3.0)

PACMAN_SL = """cachyos-v3 bash 5.3.20-2 [installed]
core glibc 2.44-1 [installed: 2.43-2]
extra htop 3.4.1-1
cachyos-extra-v3 htop 3.4.1-1.1
extra nginx 1.29.3-1
"""

PACMAN_SS = """extra/nginx 1.29.3-1
    Lightweight HTTP server
    and IMAP/POP3 proxy server
cachyos-v3/htop 3.4.1-1.1 [installed]
    Interactive process viewer
"""


def test_parse_sl_first_repo_wins_and_installed_flag():
    sl = discover.parse_sl(PACMAN_SL)
    assert sl['bash'] == ('cachyos-v3', '5.3.20-2', True)
    assert sl['glibc'][2] is True
    assert sl['htop'] == ('extra', '3.4.1-1', False)               # erste Fundstelle
    assert discover.origin_label('cachyos-extra-v3') == 'CachyOS' and discover.origin_label('extra') == 'Arch'


def test_parse_ss_joins_description_lines():
    found = discover.parse_ss(PACMAN_SS)
    assert [f['name'] for f in found] == ['nginx', 'htop']
    assert found[0]['desc'] == 'Lightweight HTTP server and IMAP/POP3 proxy server'
    assert found[1]['installed'] and not found[0]['installed']


def test_pkgstats_pages_merge():
    pages = [{'packagePopularities': [{'name': 'acl', 'popularity': 100}, {'name': 'x', 'popularity': 1.5}]},
             {'packagePopularities': [{'name': 'htop', 'popularity': 44.25}]}]
    assert discover.parse_pkgstats(pages) == {'acl': 100.0, 'x': 1.5, 'htop': 44.25}


def test_parse_aur_search_sorted_with_flags():
    data = {'results': [
        dict(Name='low', Version='1', Description='d', Popularity=0.1, NumVotes=2, Maintainer=None, OutOfDate=1700000000, PackageBase='low'),
        dict(Name='yay', Version='12', Description='helper', Popularity=18.6, NumVotes=2000, Maintainer='x', OutOfDate=None, PackageBase='yay'),
        dict(Name='--evil', Version='1', Popularity=99, NumVotes=1, Maintainer='x'),             # Name wie eine Option
    ]}
    out = discover.parse_aur_search(data)
    assert [c['name'] for c in out] == ['yay', 'low']
    assert out[0]['flags'] == [] and out[0]['pop_label'].startswith('18.60')
    assert len(out[1]['flags']) == 3                                  # verwaist, veraltet, wenige Stimmen


def test_parse_flathub_skips_installed_and_sorts():
    data = {'hits': [
        {'app_id': 'org.a.A', 'name': 'A', 'summary': 'sa', 'installs_last_month': 10},
        {'app_id': 'org.b.B', 'name': 'B', 'summary': '', 'installs_last_month': 500},
        {'app_id': 'org.c.C', 'name': 'C', 'installs_last_month': 9999},
        {'app_id': 'bad id; x', 'name': 'X', 'installs_last_month': 1},
    ]}
    out = discover.parse_flathub(data, skip={'org.c.C'})
    assert [c['name'] for c in out] == ['org.b.B', 'org.a.A']
    assert out[1]['desc'] == 'A – sa'


@pytest.mark.parametrize('term', ['a;b', 'x|y', '$(id)', '..*', 'a' * 70, 'a\\b'])
def test_search_terms_are_restricted(term):
    with pytest.raises(ValueError):
        discover.terms(term)


def test_search_terms_split_and_allow_package_chars():
    assert discover.terms('  c++  python-foo_bar 1.2 ') == ['c++', 'python-foo_bar', '1.2']
    assert discover.terms('') == []


ADVISORIES = [
    {'name': 'AVG-1', 'packages': ['nginx'], 'status': 'Vulnerable', 'severity': 'High', 'type': 'denial-of-service',
     'affected': '1.29.0-1', 'fixed': None, 'issues': ['CVE-2026-0001']},
    {'name': 'AVG-2', 'packages': ['htop'], 'status': 'Fixed', 'severity': 'Critical', 'type': 'arbitrary-code-execution',
     'affected': '3.0-1', 'fixed': '3.4.0-1', 'issues': ['CVE-2026-0002']},          # in 3.4.1 bereits behoben
]


def test_candidate_security_uses_available_version():
    cands = [discover.candidate('nginx', 'repo', repo='extra', version='1.29.3-1'),
             discover.candidate('htop', 'repo', repo='extra', version='3.4.1-1'),
             discover.candidate('old', 'repo', repo='extra', version='1-1'),
             discover.candidate('yay', 'aur', repo='aur'), discover.candidate('org.a.A', 'flatpak')]
    discover.annotate_security(cands, ADVISORIES)
    nginx, htop, _old, yay, flat = (c['sec'] for c in cands)
    assert nginx['severity'] == 'hoch' and nginx['count'] == 1 and nginx['items'][0]['id'] == 'AVG-1'
    assert htop['count'] == 0 and not htop['severity']                # behoben in der verfügbaren Version
    assert yay['note'] and flat['note'] and not yay['items']          # AUR/Flatpak: kein Tracker, Hinweis
    cands[0]['version'] = '1.28.0-1'                                  # älter als betroffene Version → kein Treffer
    discover.annotate_security(cands[:1], ADVISORIES)
    assert cands[0]['sec']['count'] == 0


def _fake_run(calls, out=''):
    def fake(cmd, **kw):
        calls.append(list(cmd))
        return out
    return fake


def test_repo_search_words_never_become_pacman_options(monkeypatch):
    calls = []
    monkeypatch.setattr(discover, 'run', _fake_run(calls))

    async def no_pop():
        return {}
    monkeypatch.setattr(discover, 'popularity', no_pop)
    asyncio.run(discover.repo('--version c++'))
    cmd = calls[0]
    assert cmd[:2] == ['pacman', '-Ss'] and cmd[-3:] == ['--', '--version', 'c\\+\\+']   # nach „--“ nur noch Suchwörter


def test_aur_search_matches_all_words_and_hides_installed(monkeypatch):
    urls = []
    data = {'results': [
        dict(Name='yay', Version='1', Description='AUR helper', Popularity=9, NumVotes=100, Maintainer='x'),
        dict(Name='yay-bin', Version='1', Description='AUR helper (binary)', Popularity=3, NumVotes=50, Maintainer='x'),
        dict(Name='paru', Version='1', Description='AUR helper in Rust', Popularity=8, NumVotes=90, Maintainer='x'),
    ]}

    async def fake_fetch(url, **kw):
        urls.append(url)
        return data

    async def installed():
        return {'yay'}
    monkeypatch.setattr(discover, 'fetch_json', fake_fetch)
    monkeypatch.setattr(discover, '_installed_names', installed)
    out = asyncio.run(discover.aur('yay helper'))
    assert [c['name'] for c in out] == ['yay-bin']                   # „paru“ passt nicht zu „yay“, „yay“ ist installiert
    assert '/search/helper?' in urls[0]                              # gesucht wird nach dem längsten Wort
    assert asyncio.run(discover.aur('a b')) == [] and len(urls) == 1   # zu kurz: kein Netzzugriff


def test_popularity_is_cached_in_memory_and_survives_failures(monkeypatch):
    calls = []
    page = json.dumps({'packagePopularities': [{'name': 'htop', 'popularity': 44.0}]}).encode()

    async def fake_fetch(url, **kw):
        calls.append(url)
        return page
    monkeypatch.setattr(discover, 'fetch', fake_fetch)
    assert asyncio.run(discover.popularity()) == {'htop': 44.0}
    assert asyncio.run(discover.popularity()) == {'htop': 44.0}
    assert len(calls) == discover.PKGSTATS_PAGES                      # zweiter Aufruf aus dem Speicher
    # abgelaufen + Netz weg: letzter Stand, und nicht bei jeder Suche erneut versuchen
    discover._POP = (discover._POP[0] - discover.POP_TTL - 1, discover._POP[1])

    async def failing(url, **kw):
        calls.append(url)
        raise OSError('offline')
    monkeypatch.setattr(discover, 'fetch', failing)
    n = len(calls)
    assert asyncio.run(discover.popularity()) == {'htop': 44.0}
    assert asyncio.run(discover.popularity()) == {'htop': 44.0}
    assert 0 < len(calls) - n <= discover.PKGSTATS_PAGES


def test_flatpak_remote_scopes():
    text = 'flathub\tsystem\nGeForceNOW\tuser\nflathub\tuser\nold\tsystem,disabled\nnoopts\n'
    assert flatpak.parse_remote_scopes(text, 'flathub') == {'user', 'system'}
    assert flatpak.parse_remote_scopes(text, 'GeForceNOW') == {'user'}
    assert flatpak.parse_remote_scopes(text, 'old') == set()          # deaktivierter Remote zählt nicht
    assert flatpak.parse_remote_scopes(text, 'missing') == set() and flatpak.parse_remote_scopes('', 'flathub') == set()


@pytest.mark.parametrize('ref', ['--user', '-y', '--reinstall', 'org.gimp.GIMP\n', '', 'a b', 'org.a;b'])
def test_valid_flatpak_rejects_options_and_garbage(ref):
    with pytest.raises(ValueError):
        valid_flatpak(ref)


def test_valid_flatpak_accepts_real_ids():
    assert valid_flatpak('org.gimp.GIMP') == 'org.gimp.GIMP'
    assert valid_flatpak('org.freedesktop.Platform//24.08') == 'org.freedesktop.Platform//24.08'
    assert valid_flatpak('io.github._x-y.App_1')


def test_flathub_ids_starting_with_dash_are_dropped():
    out = discover.parse_flathub({'hits': [{'app_id': '--user', 'installs_last_month': 5},
                                           {'app_id': 'org.ok.App', 'installs_last_month': 1}]})
    assert [c['name'] for c in out] == ['org.ok.App']


def test_thousands_separator_follows_language():
    i18n.set_language('de')
    assert i18n.thousands(53308) == '53.308' and i18n.thousands(7) == '7'
    assert discover.parse_flathub({'hits': [{'app_id': 'org.a.A', 'installs_last_month': 1234567}]})[0]['pop_label'] \
        == '1.234.567 Inst./Monat'
    i18n.set_language('en')
    assert i18n.thousands(53308) == '53,308'
    assert discover.parse_flathub({'hits': [{'app_id': 'org.a.A', 'installs_last_month': 1234567}]})[0]['pop_label'] \
        == '1,234,567 installs/month'


def test_flathub_needs_flatpak(monkeypatch):
    monkeypatch.setattr(discover, 'have', lambda tool: False)
    with pytest.raises(RuntimeError):
        asyncio.run(discover.flathub(''))


def test_leaf_packages_exclude_dependencies_and_providers():
    S, L = pacman.SEP, pacman.LIST
    rows = [
        ('firefox', L.join(['glibc', 'libfoo.so', 'gtk3']), ''),            # Programm: nichts hängt davon ab
        ('glibc', '', ''),                                                  # von firefox benötigt
        ('libfoo', '', L.join(['libfoo.so=1-64'])),                         # über Soname-Provides benötigt
        ('gtk3', '', ''),
        ('jack2', '', L.join(['jack=1.9'])),                                # Provider eines virtuellen Pakets
        ('ardour', 'jack', ''),                                             # hängt von „jack“ ab → jack2 ist Abhängigkeit
        ('htop', '', ''),
    ]
    text = '\n'.join(S.join(r) for r in rows) + '\n'
    assert pacman.parse_leaves(text) == {'firefox', 'ardour', 'htop'}
    assert pacman.parse_leaves('') == set() and pacman.parse_leaves('kaputt\n') == set()


def test_parse_removal_lists_packages_and_blockers():
    names, blockers = pacman.parse_removal("btop\nlibfoo\nbtop\n")
    assert names == ['btop', 'libfoo'] and blockers == []
    text = ("checking dependencies...\n:: removing glibc breaks dependency 'glibc' required by zstd\n"
            ":: removing glibc breaks dependency 'glibc' required by zlib\n:: removing glibc breaks dependency 'x' required by zstd\n")
    names, blockers = pacman.parse_removal(text)
    assert names == [] and blockers == ['zstd', 'zlib']                       # ohne Dubletten, „checking …“ ist kein Paket


def test_removal_preview_uses_validated_names(monkeypatch):
    calls = []
    monkeypatch.setattr(pacman, 'run', lambda cmd, **kw: calls.append(list(cmd)) or 'btop\n')
    assert pacman.removal_preview(['btop']) == (['btop'], [])
    assert calls[0] == ['pacman', '-Rsp', '--print-format', '%n', 'btop']
    with pytest.raises(ValueError):
        pacman.removal_preview(['-Rdd'])
