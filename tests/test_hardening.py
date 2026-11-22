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
            await sysutil.fetch('https://aur.archlinux.org/big', client=c, ttl=0)
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

    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.scheme == 'https':
            return httpx.Response(302, headers={'location': 'http://aur.archlinux.org/x'})
        return httpx.Response(200, content=b'ok')

    async def go():
        client = sysutil.https_client()
        client._transport = httpx.MockTransport(handler)
        async with client:
            await client.get('https://aur.archlinux.org/')
    with pytest.raises(sysutil.CommandError):
        asyncio.run(go())
    assert seen == ['https://aur.archlinux.org/']            # die http://-Anfrage wurde gar nicht erst gestellt


# ------------------------------------------------------------------ Review 1.5.0: Hosts, Cache, veraltete Daten

@pytest.mark.parametrize('url, ok', [
    ('https://aur.archlinux.org/rpc', True), ('https://security.archlinux.org/x.json', True),
    ('https://archlinux.org/feeds/news/', True), ('https://pkgstats.archlinux.de/api', True),
    ('https://cachyos.org/rss.xml', True), ('https://flathub.org/api/v2/search', True),
    ('http://aur.archlinux.org/rpc', False), ('https://evil-archlinux.org/', False),
    ('https://archlinux.org.evil.example/', False), ('https://example.org/', False),
])
def test_host_allowlist(url, ok):
    assert sysutil.host_allowed(url) is ok


def test_redirect_to_foreign_https_host_is_refused():
    import httpx
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(302, headers={'location': 'https://attacker.example/x'})

    async def go():
        client = sysutil.https_client()
        client._transport = httpx.MockTransport(handler)
        async with client:
            await client.get('https://aur.archlinux.org/')
    with pytest.raises(sysutil.CommandError):
        asyncio.run(go())
    assert seen == ['aur.archlinux.org']


@pytest.fixture
def http_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'HTTP_CACHE', tmp_path / 'http')
    monkeypatch.setattr(config, 'CACHE', tmp_path)
    monkeypatch.setattr(config, 'AUR_BUILD', tmp_path / 'aur')
    monkeypatch.setattr(config, 'CONFIG', tmp_path / 'cfg')
    sysutil.STALE.clear()
    yield tmp_path / 'http'
    sysutil.STALE.clear()


def _client(handler):
    import httpx
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_cache_files_are_private_and_stale_fallback_is_reported(http_cache):
    import httpx
    url = 'https://security.archlinux.org/issues/all.json'

    async def get(handler, ttl=0):
        async with _client(handler) as c:
            return await sysutil.fetch(url, client=c, ttl=ttl)
    assert asyncio.run(get(lambda r: httpx.Response(200, content=b'[1]'))) == b'[1]'
    files = list(http_cache.iterdir())
    assert len(files) == 1 and oct(files[0].stat().st_mode & 0o777) == '0o600'   # keine Temp-Dateien übrig
    assert url not in sysutil.STALE
    old = time.time() - 7200
    os.utime(files[0], (old, old))
    assert asyncio.run(get(lambda r: httpx.Response(503))) == b'[1]'               # Netzfehler → alter Cache
    assert 7100 < sysutil.STALE[url] < 7300                                        # … mit Alter vermerkt
    asyncio.run(get(lambda r: httpx.Response(200, content=b'[2]')))
    assert url not in sysutil.STALE                                                # wieder aktuell


def test_cache_does_not_follow_symlinks_or_accept_oversized(http_cache, monkeypatch, tmp_path):
    import httpx
    url = 'https://aur.archlinux.org/x'
    http_cache.mkdir(parents=True)
    target = tmp_path / 'elsewhere'
    target.write_bytes(b'planted')
    sysutil._cache_path(url).symlink_to(target)

    async def get():
        async with _client(lambda r: httpx.Response(500)) as c:
            return await sysutil.fetch(url, client=c, ttl=3600)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(get())                                                         # Symlink zählt nicht als Cache
    sysutil._cache_path(url).unlink()
    sysutil._cache_path(url).write_bytes(b'x' * 100)
    monkeypatch.setattr(config, 'MAX_DOWNLOAD', 10)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(get())                                                         # übergroßer Cache wird ignoriert


def test_fetch_refuses_untrusted_host_before_connecting(http_cache):
    import httpx
    seen = []

    async def go():
        async with _client(lambda r: seen.append(r) or httpx.Response(200)) as c:
            await sysutil.fetch('https://example.org/', client=c)
    with pytest.raises(sysutil.CommandError):
        asyncio.run(go())
    assert seen == []


def test_cve_description_errors(monkeypatch):
    import httpx

    from update_planner.sources import security
    items = [dict(cves=['CVE-2024-0001'], description='', vector='')]

    async def net_error(*a, **kw):
        raise httpx.ConnectError('down')
    monkeypatch.setattr(security, 'fetch_json', net_error)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(security.asyncio, 'sleep', lambda s: real_sleep(0))
    asyncio.run(security.describe(items))                                         # Netzfehler: Feld bleibt leer
    assert items[0]['description'] == ''

    async def bug(*a, **kw):
        raise KeyError('programming error')
    monkeypatch.setattr(security, 'fetch_json', bug)
    with pytest.raises(KeyError):
        asyncio.run(security.describe(items))                                     # Programmierfehler wird sichtbar


def test_advisories_reject_unexpected_shape(monkeypatch):
    from update_planner.sources import security

    async def fake(url, **kw):
        return {'not': 'a list'}
    monkeypatch.setattr(security, 'fetch_json', fake)
    with pytest.raises(sysutil.CommandError):
        asyncio.run(security.advisories())
