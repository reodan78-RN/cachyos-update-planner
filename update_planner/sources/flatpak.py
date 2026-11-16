"""Flatpak: installierte Apps/Runtimes und verfügbare Updates (System- und Benutzerinstallation)."""

from __future__ import annotations

from ..sysutil import CommandError, have, run


def _rows(cmd: list[str], cols: int) -> list[list[str]]:
    try:
        out = run(cmd, timeout=120)
    except (CommandError, OSError):
        return []
    rows = []
    for line in out.splitlines():
        f = line.split('\t')
        if len(f) >= cols and f[0]:
            rows.append(f + [''] * (cols - len(f)))
    return rows


def installed() -> list[dict]:
    if not have('flatpak'):
        return []
    apps = _rows(['flatpak', 'list', '--app', '--columns=application,name,version,branch,origin,installation,runtime,size'], 8)
    rts = _rows(['flatpak', 'list', '--runtime', '--columns=application,name,version,branch,origin,installation,size'], 7)
    out = []
    for a in apps:
        rt = a[6].split('/')
        out.append(dict(id=a[0], name=a[1] or a[0], version=a[2] or a[3], branch=a[3], origin=a[4],
                        installation=a[5], runtime=f'{rt[0]}//{rt[2]}' if len(rt) == 3 else '', size=a[7], kind='app'))
    for r in rts:
        out.append(dict(id=f'{r[0]}//{r[3]}', name=r[1] or r[0], version=r[2] or r[3], branch=r[3], origin=r[4],
                        installation=r[5], runtime='', size=r[6], kind='runtime'))
    return out


def updates() -> list[dict]:
    if not have('flatpak'):
        return []
    rows = _rows(['flatpak', 'remote-ls', '--updates', '--columns=application,version,branch,origin,download-size'], 5)
    return [dict(id=r[0], new=r[1] or r[2], branch=r[2], origin=r[3], download=r[4]) for r in rows]


def unused_runtimes(items: list[dict]) -> list[str]:
    used = {i['runtime'] for i in items if i['kind'] == 'app'}
    return [i['id'] for i in items if i['kind'] == 'runtime' and i['id'] not in used
            and not any(x in i['id'] for x in ('.GL.', '.Gtk3theme.', '.KStyle.', '.Locale', '.Debug', 'codecs', 'ffmpeg'))]
