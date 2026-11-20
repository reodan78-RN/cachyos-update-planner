import pytest

from update_planner import analysis, catalog
from update_planner.sources import news, pacman, security
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
