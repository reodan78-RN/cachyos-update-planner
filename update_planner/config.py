"""Pfade, Konstanten und das lokale Sitzungsgeheimnis."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from . import __version__

APP_ID = 'update-planner'
USER_AGENT = f'update-planner/{__version__} (+https://aur.archlinux.org)'
HTTP_TIMEOUT = 20.0
MAX_DOWNLOAD = 8 * 1024 * 1024  # Obergrenze für einzelne Downloads (Tracker ~1 MB)

CACHE = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / APP_ID
CONFIG = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / APP_ID
CHECKUP_DB = CACHE / 'checkup-db'
AUR_BUILD = CACHE / 'aur'
HTTP_CACHE = CACHE / 'http'

# Pakete, deren Update besonderes Gewicht hat (Präfix-Treffer mit * am Ende)
CRITICAL = {
    'glibc', 'gcc-libs', 'systemd', 'systemd-libs', 'pacman', 'openssl', 'mesa', 'mkinitcpio', 'dracut',
    'grub', 'limine', 'refind', 'btrfs-progs', 'cryptsetup', 'lvm2', 'util-linux', 'coreutils', 'bash',
    'qt6-base', 'kwin', 'plasma-workspace', 'sddm', 'pipewire', 'wayland', 'xorg-server', 'python',
    'linux*', 'nvidia*', 'lib32-nvidia*', 'amd-ucode', 'intel-ucode', 'linux-firmware*',
}
REBOOT = {'linux*', 'nvidia*', 'amd-ucode', 'intel-ucode', 'glibc', 'systemd', 'linux-firmware*', 'mesa'}


def matches(name: str, patterns: set[str]) -> bool:
    return any(name == p or (p.endswith('*') and name.startswith(p[:-1])) for p in patterns)


def ensure_dirs() -> None:
    for d in (CACHE, CONFIG, HTTP_CACHE, AUR_BUILD):
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o700)


def storage_secret() -> str:
    """Zufälliges Geheimnis pro Installation (statt eines festen Werts im Quellcode)."""
    ensure_dirs()
    path = CONFIG / 'secret'
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(secrets.token_urlsafe(32))
    return path.read_text().strip()
