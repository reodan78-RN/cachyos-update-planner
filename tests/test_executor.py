"""Der Update-Lauf mit gefälschtem Prozessstart: es wird nichts am System ausgeführt."""

import asyncio
import os

import pytest

from update_planner import config, executor

SECRET = 'S3cr3t-pw!'  # noqa: S105 – Testwert


class FakeProc:
    def __init__(self, lines, rc=0):
        self._lines = [l.encode() + b'\n' for l in lines]
        self.returncode = rc
        self.stdin = FakeStdin()
        self.stdout = self._iter()

    async def _iter(self):
        for l in self._lines:
            yield l

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
