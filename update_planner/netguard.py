"""Schutz des lokalen Webservers (127.0.0.1) gegen Zugriffe, die nicht vom eigenen Benutzer/Browser-Fenster stammen.

Der Server steuert ein Programm, das mit sudo Pakete installiert. Ohne Prüfung könnte
* jede Webseite im Browser eine Socket.IO-Verbindung zu http://127.0.0.1:<port> öffnen (NiceGUI erlaubt jeden
  Origin) und die Oberfläche fernsteuern (Cross-Site-WebSocket-Hijacking),
* eine Seite per DNS-Rebinding mit beliebigem Host-Header zugreifen,
* ein anderer Benutzer desselben Rechners die Oberfläche öffnen.
Geprüft wird bei jeder HTTP-Anfrage und jedem WebSocket-Verbindungsaufbau:
1. `Host` ist 127.0.0.1, localhost oder [::1],
2. ein vorhandener `Origin` hat genau denselben Host:Port (gleicher Ursprung),
3. das verbindende Socket gehört einem Prozess mit derselben Benutzerkennung (aus /proc/net/tcp*).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

ALLOWED_HOSTS = frozenset({'127.0.0.1', 'localhost', '[::1]', '::1'})


def host_name(host_header: str) -> str:
    """Hostname ohne Port: '127.0.0.1:8765' → '127.0.0.1', '[::1]:8765' → '[::1]'."""
    host = host_header.strip().lower()
    if host.startswith('['):
        return host.split(']', 1)[0] + ']'
    return host.rsplit(':', 1)[0] if host.count(':') == 1 else host


def origin_allowed(origin: str | None, host_header: str) -> bool:
    """Ohne Origin (z. B. Navigation, curl) erlaubt; sonst muss Host:Port übereinstimmen (nie 'null')."""
    if origin is None:
        return True
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    return parts.scheme in ('http', 'https') and parts.netloc.lower() == host_header.strip().lower()


def peer_uid(client: tuple[str, int] | None, server: tuple[str, int] | None,
             tables: tuple[str, ...] = ('/proc/net/tcp', '/proc/net/tcp6')) -> int | None:
    """Benutzerkennung des Prozesses, dem das verbindende (Client-)Socket gehört; None, wenn nicht ermittelbar."""
    if not client or not server:
        return None
    cport, sport = client[1], server[1]
    for table in tables:
        try:
            with open(table) as f:
                next(f, None)
                for line in f:
                    cols = line.split()
                    if len(cols) < 8:
                        continue
                    if int(cols[1].rsplit(':', 1)[1], 16) == cport and int(cols[2].rsplit(':', 1)[1], 16) == sport:
                        return int(cols[7])
        except (OSError, ValueError, IndexError):
            continue
    return None


class LocalOnlyMiddleware:
    """Reine ASGI-Middleware (gilt auch für WebSockets und die eingehängte Socket.IO-App)."""

    def __init__(self, app, uid: int | None = None, lookup: Callable = peer_uid):
        self.app, self.uid, self.lookup = app, os.getuid() if uid is None else uid, lookup

    def reason(self, scope) -> str | None:
        headers = {k.decode('latin1').lower(): v.decode('latin1') for k, v in scope.get('headers', [])}
        host = headers.get('host', '')
        if host_name(host) not in ALLOWED_HOSTS:
            return 'Host'
        if not origin_allowed(headers.get('origin'), host):
            return 'Origin'
        uid = self.lookup(scope.get('client'), scope.get('server'))
        if uid != self.uid:                       # auch „nicht ermittelbar“ wird abgelehnt (fail closed)
            return 'Benutzer'
        return None

    async def __call__(self, scope, receive, send):
        if scope['type'] in ('http', 'websocket'):
            why = self.reason(scope)
            if why:
                log.warning('Zugriff abgelehnt (%s): client=%s', why, scope.get('client'))
                if scope['type'] == 'websocket':
                    await send({'type': 'websocket.close', 'code': 1008})
                else:
                    body = b'Forbidden'
                    await send({'type': 'http.response.start', 'status': 403,
                                'headers': [(b'content-type', b'text/plain'), (b'content-length', b'9')]})
                    await send({'type': 'http.response.body', 'body': body})
                return
        await self.app(scope, receive, send)
