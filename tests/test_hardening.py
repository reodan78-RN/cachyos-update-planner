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


# ------------------------------------------------------------------ netguard: lokaler Webserver

from update_planner import netguard  # noqa: E402


def _scope(host='127.0.0.1:8765', origin=None, kind='http', client=('127.0.0.1', 50000)):
    headers = [(b'host', host.encode())] + ([(b'origin', origin.encode())] if origin else [])
    return {'type': kind, 'headers': headers, 'client': client, 'server': ('127.0.0.1', 8765)}


async def _call(mw, scope):
    sent, reached = [], []

    async def app(scope_, receive, send):
        reached.append(True)

    async def send(msg):
        sent.append(msg)
    await netguard.LocalOnlyMiddleware(app, uid=1000, lookup=mw)(scope, None, send)
    return sent, bool(reached)


@pytest.mark.parametrize('kwargs, allowed', [
    ({}, True),
    ({'host': 'localhost:8765', 'origin': 'http://localhost:8765'}, True),
    ({'host': '[::1]:8765', 'origin': 'http://[::1]:8765'}, True),
    ({'host': 'evil.example:8765'}, False),                              # DNS-Rebinding
    ({'host': '127.0.0.1.evil.example'}, False),
    ({'origin': 'https://evil.example'}, False),                         # fremde Webseite → Socket.IO
    ({'origin': 'http://127.0.0.1:9999'}, False),                        # anderer lokaler Dienst/Port
    ({'origin': 'null'}, False),
    ({'kind': 'websocket', 'origin': 'https://evil.example'}, False),
    ({'kind': 'websocket'}, True),
])
def test_netguard_host_and_origin(kwargs, allowed):
    sent, reached = asyncio.run(_call(lambda c, s: 1000, _scope(**kwargs)))
    assert reached is allowed
    if not allowed:
        assert sent[0]['type'] in ('http.response.start', 'websocket.close')
        assert sent[0].get('status', 403) == 403 or sent[0].get('code') == 1008


def test_netguard_rejects_other_users_and_unknown_peers():
    assert asyncio.run(_call(lambda c, s: 1001, _scope()))[1] is False        # anderer Benutzer
    assert asyncio.run(_call(lambda c, s: None, _scope()))[1] is False        # nicht ermittelbar → ablehnen
    assert asyncio.run(_call(lambda c, s: 1000, _scope(kind='lifespan')))[1] is True   # Lifespan unberührt


def test_peer_uid_reads_proc_table(tmp_path):
    table = tmp_path / 'tcp'
    table.write_text('  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n'
                     '   0: 0100007F:C350 0100007F:2231 01 00000000:00000000 00:00000000 00000000  1001        0 1 1 0\n'
                     '   1: 0100007F:2231 0100007F:C350 01 00000000:00000000 00:00000000 00000000  1000        0 2 1 0\n')
    # Client-Socket 127.0.0.1:50000 (0xC350) → Server 8753 (0x2231): gehört Benutzer 1001
    assert netguard.peer_uid(('127.0.0.1', 50000), ('127.0.0.1', 8753), (str(table),)) == 1001
    assert netguard.peer_uid(('127.0.0.1', 1), ('127.0.0.1', 8753), (str(table),)) is None
    assert netguard.peer_uid(None, None) is None


def test_https_client_refuses_redirect_to_http():
    import httpx

    def handler(request):
        if request.url.scheme == 'https':
            return httpx.Response(302, headers={'location': 'http://example.org/x'})
        return httpx.Response(200, content=b'ok')

    async def go():
        client = sysutil.https_client()
        client._transport = httpx.MockTransport(handler)
        async with client:
            await client.get('https://example.org/')
    with pytest.raises(sysutil.CommandError):
        asyncio.run(go())
