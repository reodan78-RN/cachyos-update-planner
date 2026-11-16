"""Systemzustand: Kernel, Dateisystem, Snapshots, Speicherplatz, ausstehender Neustart."""

from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path

from ..sysutil import CommandError, have, run


def info() -> dict:
    kernel = platform.release()
    try:
        fs = run(['findmnt', '-no', 'FSTYPE', '/'], timeout=10).strip()
    except (CommandError, OSError):
        fs = '?'
    snapper_root = False
    if have('snapper'):
        try:
            snapper_root = 'root' in run(['snapper', '--csvout', 'list-configs'], timeout=10)
        except (CommandError, OSError):
            pass
    snap_pac = Path('/usr/share/libalpm/hooks/05-snap-pac-pre.hook').exists()
    os_name = 'Linux'
    try:
        for line in Path('/etc/os-release').read_text().splitlines():
            if line.startswith('PRETTY_NAME='):
                os_name = line.split('=', 1)[1].strip('"')
    except OSError:
        pass
    root_free = shutil.disk_usage('/').free / 1024 ** 3
    cache_free = shutil.disk_usage('/var/cache/pacman').free / 1024 ** 3 if os.path.isdir('/var/cache/pacman') else root_free
    return dict(
        os=os_name, kernel=kernel, fs=fs, snapper=snapper_root, snap_pac=snap_pac,
        limine_snapper=have('limine-snapper-sync'), free_gib=min(root_free, cache_free),
        reboot_pending=not Path(f'/usr/lib/modules/{kernel}').exists(),
        aur_helper=next((h for h in ('paru', 'yay') if have(h)), None),
    )
