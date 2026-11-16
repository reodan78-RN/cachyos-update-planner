from update_planner import analysis
from update_planner.sources.pacman import Installed, RepoUpdate


def inst(name, version, provides=(), depends=(), repo='core', base=None):
    return Installed(name, version, 0, base or name, 'explicit', list(depends), list(provides), [], '', repo)


def test_versioned_dependency_links_updates():
    installed = {'a': inst('a', '1.0-1'), 'b': inst('b', '1.0-1'), 'c': inst('c', '1.0-1')}
    updates = [RepoUpdate('a', '1.0-1', '2.0-1', depends=['b>=2.0']), RepoUpdate('b', '1.0-1', '2.0-1'),
               RepoUpdate('c', '1.0-1', '1.1-1')]
    groups, needs = analysis.together_groups(installed, updates)
    assert groups == [['a', 'b']]
    assert needs['a'] == {'b'}


def test_soname_dependency_links_updates():
    installed = {'app': inst('app', '1-1'), 'libx': inst('libx', '1-1', provides=['libx.so=1-64'])}
    updates = [RepoUpdate('app', '1-1', '2-1', depends=['libx.so=2-64']),
               RepoUpdate('libx', '1-1', '2-1', provides=['libx.so=2-64'])]
    groups, _ = analysis.together_groups(installed, updates)
    assert groups == [['app', 'libx']]


def test_split_packages_share_base():
    installed = {'p': inst('p', '1-1', base='p'), 'p-qt': inst('p-qt', '1-1', base='p')}
    updates = [RepoUpdate('p', '1-1', '2-1'), RepoUpdate('p-qt', '1-1', '2-1')]
    assert analysis.together_groups(installed, updates)[0] == [['p', 'p-qt']]


def test_foreign_rebuild_detected():
    installed = {'libx': inst('libx', '1-1', provides=['libx.so=1-64']),
                 'tool': inst('tool', '1-1', depends=['libx.so=1-64'], repo='aur')}
    updates = [RepoUpdate('libx', '1-1', '2-1', provides=['libx.so=2-64'])]
    assert analysis.foreign_rebuilds(installed, updates) == [('tool', 'libx', 'libx.so=1-64')]


def test_repo_risk_reasons():
    u = RepoUpdate('linux-cachyos', '6.17.7-1', '6.18.0-1')
    reasons = analysis.repo_risk(u, backups={'linux-cachyos': ['/etc/x']}, fixes={'linux-cachyos': ['AVG-9']},
                                 manual_news=['linux-cachyos 6.18 requires manual intervention'])
    texts = ' '.join(r for r, _ in reasons)
    assert 'Kernkomponente' in texts and 'Neustart' in texts and '.pacnew' in texts
    assert 'AVG-9' in texts and 'Arch-News' in texts
    assert 0 <= analysis.score(reasons) <= 100
