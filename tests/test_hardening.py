import asyncio
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

from update_planner import config, sysutil


def test_storage_secret_is_random_and_private(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'CONFIG', tmp_path / 'cfg')
    monkeypatch.setattr(config, 'CACHE', tmp_path / 'cache')
    monkeypatch.setattr(config, 'HTTP_CACHE', tmp_path / 'cache/http')
    monkeypatch.setattr(config, 'AUR_BUILD', tmp_path / 'cache/aur')
    s1 = config.storage_secret()
    assert len(s1) >= 32 and s1 == config.storage_secret()
    assert oct(os.stat(tmp_path / 'cfg/secret').st_mode & 0o777) == '0o600'
    assert oct(os.stat(tmp_path / 'cache').st_mode & 0o777) == '0o700'


def test_run_uses_c_locale_and_no_shell():
    out = sysutil.run(['sh', '-c', 'echo $LC_ALL'])
    assert out.strip() == 'C'
    with pytest.raises(sysutil.CommandError):
        sysutil.run(['false'])


def test_fetch_rejects_oversized(monkeypatch, tmp_path):
    import httpx
    monkeypatch.setattr(config, 'HTTP_CACHE', tmp_path)
    monkeypatch.setattr(config, 'CACHE', tmp_path)
    monkeypatch.setattr(config, 'AUR_BUILD', tmp_path / 'aur')
    monkeypatch.setattr(config, 'CONFIG', tmp_path / 'cfg')
    monkeypatch.setattr(config, 'MAX_DOWNLOAD', 10)
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b'x' * 100))

    async def go():
        async with httpx.AsyncClient(transport=transport) as c:
            await sysutil.fetch('https://example.org/big', client=c, ttl=0)
    with pytest.raises(sysutil.CommandError):
        asyncio.run(go())


def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def test_server_smoke_demo_localhost_only(tmp_path):
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith('PYTEST')}  # sonst startet NiceGUI im Testmodus
    env |= {'XDG_CACHE_HOME': str(tmp_path / 'cache'), 'XDG_CONFIG_HOME': str(tmp_path / 'cfg')}
    proc = subprocess.Popen([sys.executable, '-m', 'update_planner.app', '--browser', '--demo', '--port', str(port)],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(60):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=2) as r:
                    body = r.read().decode()
                break
            except OSError:
                time.sleep(0.5)
        else:
            pytest.fail('Server startet nicht')
        assert 'CachyOS Update Planner' in body
        for path in ('/updates', '/packages', '/conflicts', '/security', '/aur-scan'):
            with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=5) as r:
                assert r.status == 200
        # nur an localhost gebunden
        listen = [l.split()[3] for l in subprocess.run(['ss', '-ltnH'], capture_output=True, text=True).stdout.splitlines()
                  if l.split()[3].endswith(f':{port}')]
        assert listen == [f'127.0.0.1:{port}']
    finally:
        proc.terminate()
        proc.wait(10)
