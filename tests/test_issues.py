"""Bekannte Probleme: Parser der drei Quellen, Zuordnung zu Updates, Gewichtung mit Deckel, Ausfall einzelner Quellen."""

import asyncio
import json
import time
from datetime import UTC, datetime

import httpx
import pytest

from update_planner import analysis
from update_planner.sources import issues, pacman
from update_planner.sources.issues import Report

NOW = time.time()
DAY = 86400

FORUM_XML = b'''<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title type="html">Pacman &amp; Package Upgrade Issues</title>
  <entry>
    <title type="html">kicad and recent upgrade to mesa 1:26.3.1-1</title>
    <link rel="alternate" href="https://bbs.archlinux.org/viewtopic.php?id=1&amp;action=new"/>
    <updated>2026-11-20T10:00:00Z</updated>
  </entry>
  <entry>
    <title type="html">[SOLVED] pandoc 3.11 broken &amp;amp; fixed</title>
    <link rel="alternate" href="javascript:alert(1)"/>
    <updated>2026-11-21T10:00:00Z</updated>
  </entry>
</feed>'''


def test_parse_forum_titles_links_and_solved():
    r = issues.parse_forum(FORUM_XML)
    assert [x.title for x in r] == ['kicad and recent upgrade to mesa 1:26.3.1-1', '[SOLVED] pandoc 3.11 broken & fixed']
    assert r[0].url.startswith('https://bbs.archlinux.org/') and r[0].source == 'Arch-Forum' and not r[0].tracker
    assert r[1].url == '' and r[1].solved                            # kein javascript:-Link in der Oberfläche
    assert r[0].date == datetime(2026, 11, 20, 10, tzinfo=UTC).timestamp()


def test_parse_forum_rejects_dtd():
    with pytest.raises(ValueError):
        issues.parse_forum(b'<!DOCTYPE x [<!ENTITY a "aaaa">]><feed/>')


def test_parse_github_keeps_only_problem_reports():
    data = [
        dict(title='[Bug]: ayugram-desktop 7.2.9-3 fails to start', html_url='https://github.com/CachyOS/x/issues/1',
             created_at='2026-11-24T08:00:00Z', labels=[{'name': 'bug'}]),
        dict(title='nginx: new version 1.30.8-1', html_url='https://github.com/x', created_at='2026-11-24T08:00:00Z',
             labels=[{'name': 'out-of-date'}]),
        dict(title='[Update]: xdman have a new version', html_url='https://github.com/x', labels=[]),
        dict(title='[FEATURE] Include efistub', html_url='https://github.com/x', labels=[{'name': 'enhancement'}]),
        dict(title='Some PR', html_url='https://github.com/x', labels=[], pull_request={}),
        dict(title='linux-cachyos 7.3 VRR broken', html_url='https://evil.example/x', created_at='bad', labels=[]),
        'garbage',
    ]
    r = issues.parse_github(data)
    assert [x.title for x in r] == ['[Bug]: ayugram-desktop 7.2.9-3 fails to start', 'linux-cachyos 7.3 VRR broken']
    assert all(x.tracker and x.source == 'CachyOS' for x in r)
    assert r[1].url == '' and r[1].date == 0.0                        # fremder Host, unlesbares Datum
    with pytest.raises(issues.CommandError):
        issues.parse_github({'message': 'API rate limit exceeded'})


def test_parse_gitlab_only_bugs_and_bound_to_pkgbase():
    data = [dict(title='New version: 3.15.2', labels=['package::out-of-date'], web_url='https://gitlab.archlinux.org/a'),
            dict(title='Crash on start', labels=['scope::bug', 'severity::3-medium'], created_at='2026-11-24T08:00:00Z',
                 web_url='https://gitlab.archlinux.org/b')]
    r = issues.parse_gitlab(data, 'python')
    assert [(x.title, x.package, x.source) for x in r] == [('Crash on start', 'python', 'Arch GitLab')]


@pytest.mark.parametrize('title, name, hit', [
    ('kicad and recent upgrade to mesa 1:26.3.1-1', 'mesa', True),
    ('[SOLVED] mesa-utils glxinfo broken', 'mesa', False),            # längerer Name
    ('[nvidia-utils] ship systemd preset', 'nvidia-utils', True),
    ('nvidia-utils 615 breaks', 'nvidia', False),
    ('linux-firmware: new version', 'linux-firmware', True),
    ('nvidia 615.71.09-1 seems to break on Wayland', 'wayland', False),  # allgemeiner Name ohne Version
    ('wayland 1.25 breaks xwayland', 'wayland', True),
    ('Listing every package which has a dependency', 'which', False),
    ('linux 7.3.5 does not boot', 'linux', True),
    ('linux-cachyos 7.3 does not boot', 'linux', False),
    ('KWin 6.5.3 panel freeze', 'kwin', True),                         # Groß-/Kleinschreibung egal
])
def test_mentions(title, name, hit):
    assert analysis.mentions(title, name) is hit


def test_mentions_version():
    assert analysis.mentions_version('upgrade to mesa 1:26.3.1-1', '1:26.3.1-1')
    assert analysis.mentions_version('mesa 26.3.1 regression', '1:26.3.1-2')        # ohne Epoch/pkgrel
    assert not analysis.mentions_version('mesa 26.3.10 regression', '1:26.3.1-1')
    assert not analysis.mentions_version('mesa 126.3.1', '26.3.1-1')


def _r(title, age_days, tracker=False, solved=False, package=''):
    return Report('CachyOS' if tracker else 'Arch-Forum', title, 'https://bbs.archlinux.org/x', NOW - age_days * DAY,
                  tracker, solved, package)


def test_issue_risk_weights_and_relevance():
    built = NOW - 5 * DAY
    reports = [
        _r('kicad and recent upgrade to mesa 1:26.3.1-1', 30),              # alt, aber nennt die neue Version
        _r('[Bug] mesa crashes kwin', 2, tracker=True),                     # nach dem Bau gemeldet
        _r('[SOLVED] mesa black screen', 1, solved=True),                   # gelöst: nur Hinweis
        _r('mesa stutter in games', 40),                                    # älter als der Bau, ohne Version: weg
        _r('kwin 6.5 panel', 1),                                            # anderes Paket
    ]
    reasons, found = analysis.issue_risk('mesa', 'mesa', '1:26.3.1-1', built, reports, NOW)
    assert [f['points'] for f in found] == [15, 10, 0]
    assert found[1]['exact'] and not found[0]['exact']
    assert reasons == [('Bekannte Probleme (3): [Bug] mesa crashes kwin', 20)]       # 25 → Deckel 20


def test_issue_risk_cap_and_gitlab_binding():
    reports = [_r(f'Bug {i}', 1, tracker=True, package='python') for i in range(4)]
    reasons, found = analysis.issue_risk('python', 'python', '3.15.2-1', NOW - 3 * DAY, reports, NOW)
    assert len(found) == 4 and reasons[0][1] == analysis.ISSUE_CAP
    # GitLab-Issues gehören zur pkgbase – nicht zu einem Paket, das zufällig im Titel steht
    assert analysis.issue_risk('python-foo', 'python-foo', '1-1', NOW - DAY, reports, NOW)[1] == []
    # Split-Paket: Meldung nennt die pkgbase
    split = [_r('linux-cachyos 7.3.5 VRR broken', 1, tracker=True)]
    assert analysis.issue_risk('linux-cachyos-headers', 'linux-cachyos', '7.3.5-2', NOW - DAY, split, NOW)[1]


def test_generic_names_need_the_new_version():
    # echter Fall: ein Kernel-Bug nennt „GCC 7.3“ – kein Problem von gcc 16 (auch nicht über die pkgbase)
    kernel_bug = [_r('[BUG] sched_ext: Failed to register kfunc sets (-22); six znver4 GCC 7.3 kernels', 1, tracker=True)]
    new = '16.2.1+r501+g4c5507b1d4e9-1'
    assert analysis.issue_risk('gcc', 'gcc', new, NOW - 2 * DAY, kernel_bug, NOW) == ([], [])
    assert analysis.issue_risk('libgcc', 'gcc', new, NOW - 2 * DAY, kernel_bug, NOW) == ([], [])
    gcc_bug = [_r('gcc 16.2.1 miscompiles znver4 code', 1, tracker=True)]
    assert analysis.issue_risk('libgcc', 'gcc', new, NOW - 2 * DAY, gcc_bug, NOW)[1]
    assert analysis.issue_risk('foo', 'foo', '1.0-1', NOW - 5 * 3600, [], NOW) == ([], [])   # kein Frische-Zuschlag


def test_issue_risk_unknown_build_date():
    # ohne Baudatum: nur Meldungen der letzten RECENT_DAYS Tage
    old, new = _r('foobar broken', analysis.RECENT_DAYS + 1), _r('foobar broken again', 2)
    assert [f['title'] for f in analysis.issue_risk('foobar', 'foobar', '1.0-1', None, [old, new], NOW)[1]] == \
        ['foobar broken again']


def test_score_with_issues_stays_bounded():
    reasons = analysis.version_reasons('1.0-1', '2.0-1') + [('Bekannte Probleme (9): x', analysis.ISSUE_CAP)]
    assert analysis.score(reasons) == 5 + 20 + 20


def test_build_time_parses_pacman_c_locale():
    assert pacman.build_time('Thu Oct 22 14:33:26 2026') > 0
    assert pacman.build_time('Do 22 Okt 2026') == 0.0


def test_collect_survives_failing_sources(monkeypatch):
    calls = []

    async def fake_fetch(url, ttl=0, client=None):
        calls.append(url)
        if 'bbs.archlinux.org' in url:
            raise httpx.ConnectError('down')
        if 'linux-cachyos/issues' in url:
            return b'{"message": "API rate limit exceeded"}'
        if 'CachyOS-PKGBUILDS' in url:
            return json.dumps([dict(title='[Bug] foo 1.2 crash', html_url='https://github.com/x',
                                    created_at='2026-11-24T08:00:00Z', labels=[{'name': 'bug'}])]).encode()
        if 'packages%2Fmissing' in url:
            raise httpx.HTTPStatusError('404', request=httpx.Request('GET', url), response=httpx.Response(404))
        return json.dumps([dict(title='Crash', labels=['scope::bug'], web_url='https://gitlab.archlinux.org/x',
                                created_at='2026-11-24T08:00:00Z')]).encode()
    monkeypatch.setattr(issues, 'fetch', fake_fetch)
    warned = []
    out = asyncio.run(issues.collect(['systemd', 'missing', 'systemd'], warn=warned.append))
    assert sorted(warned) == ['Arch-Forum', 'CachyOS (GitHub)']
    assert sorted((r.source, r.package) for r in out) == [('Arch GitLab', 'systemd'), ('CachyOS', '')]
    assert sum('gitlab' in c for c in calls) == 2                         # doppelte pkgbase nur einmal


def test_collect_limits_gitlab_and_validates_names(monkeypatch):
    seen = []

    async def fake_fetch(url, ttl=0, client=None):
        seen.append(url)
        return b'[]'
    monkeypatch.setattr(issues, 'fetch', fake_fetch)
    bases = [f'pkg{i}' for i in range(issues.MAX_GITLAB + 20)] + ['glibc', '../evil']
    asyncio.run(issues.collect(bases))
    gitlab = [u for u in seen if 'gitlab' in u]
    assert len(gitlab) == issues.MAX_GITLAB
    assert any('%2Fglibc/' in u for u in gitlab)                          # Kernkomponenten zuerst
    assert not any('evil' in u for u in seen)                             # ungültiger Name wird nie abgefragt
