"""Demodaten für das Mockup – nichts davon wird vom System gelesen."""

from __future__ import annotations

from .aur_samples import AUR_META
from .aur_scanner import scan_all

SOURCES = {
    'repo': {'label': 'Repo', 'icon': 'inventory_2', 'color': '#14b8a6'},
    'aur': {'label': 'AUR', 'icon': 'construction', 'color': '#f59e0b'},
    'flatpak': {'label': 'Flatpak', 'icon': 'widgets', 'color': '#6366f1'},
}

# name, quelle, version, größe (MiB), abhängigkeiten, beschreibung
PACKAGES: list[dict] = [
    dict(name='linux-cachyos', source='repo', version='6.17.7-1', size=148.2, deps=['coreutils', 'kmod', 'initramfs'], desc='CachyOS-Kernel (BORE, LTO)'),
    dict(name='linux-cachyos-nvidia', source='repo', version='580.95.05-3', size=92.1, deps=['linux-cachyos', 'nvidia-utils'], desc='NVIDIA-Module für linux-cachyos'),
    dict(name='nvidia-utils', source='repo', version='580.95.05-1', size=310.4, deps=['libglvnd', 'egl-wayland', 'mesa'], desc='NVIDIA-Treiber Userspace'),
    dict(name='mesa', source='repo', version='1:25.2.6-1', size=64.8, deps=['llvm-libs', 'libdrm', 'wayland', 'glibc'], desc='Open-Source-Grafikstack'),
    dict(name='llvm-libs', source='repo', version='21.1.5-1', size=142.0, deps=['glibc', 'zstd', 'libffi'], desc='LLVM-Laufzeitbibliotheken'),
    dict(name='glibc', source='repo', version='2.42+r33-1', size=48.3, deps=[], desc='GNU C Library'),
    dict(name='systemd', source='repo', version='258.2-1', size=33.5, deps=['glibc', 'openssl', 'kmod', 'util-linux'], desc='System- und Dienstverwaltung'),
    dict(name='openssl', source='repo', version='3.6.0-1', size=11.9, deps=['glibc'], desc='TLS/Krypto-Bibliothek'),
    dict(name='plasma-desktop', source='repo', version='6.5.2-1', size=48.0, deps=['qt6-base', 'kwin', 'systemd'], desc='KDE Plasma Desktop'),
    dict(name='kwin', source='repo', version='6.5.2-1', size=29.7, deps=['qt6-base', 'mesa', 'wayland'], desc='KDE Compositor'),
    dict(name='qt6-base', source='repo', version='6.10.0-2', size=74.4, deps=['glibc', 'openssl', 'mesa'], desc='Qt 6 Basis'),
    dict(name='wayland', source='repo', version='1.24.0-1', size=0.9, deps=['glibc', 'libffi'], desc='Wayland-Protokoll'),
    dict(name='libdrm', source='repo', version='2.4.127-1', size=1.4, deps=['glibc'], desc='DRM-Userspace'),
    dict(name='libglvnd', source='repo', version='1.7.0-3', size=2.6, deps=['glibc'], desc='GL Vendor-neutral Dispatch'),
    dict(name='egl-wayland', source='repo', version='4:1.1.20-1', size=0.2, deps=['wayland'], desc='EGLStream-Wayland'),
    dict(name='kmod', source='repo', version='34.2-1', size=0.4, deps=['zstd', 'openssl'], desc='Kernel-Modul-Werkzeuge'),
    dict(name='zstd', source='repo', version='1.5.7-2', size=1.3, deps=['glibc'], desc='Kompression'),
    dict(name='libffi', source='repo', version='3.5.2-1', size=0.1, deps=['glibc'], desc='Foreign Function Interface'),
    dict(name='util-linux', source='repo', version='2.41.2-1', size=12.6, deps=['glibc'], desc='System-Werkzeuge'),
    dict(name='coreutils', source='repo', version='9.8-2', size=17.1, deps=['glibc', 'openssl'], desc='GNU Coreutils'),
    dict(name='initramfs', source='repo', version='1.0-4', size=0.1, deps=['kmod', 'zstd'], desc='mkinitcpio/dracut-Hook (CachyOS)'),
    dict(name='snapper', source='repo', version='0.12.2-1', size=4.0, deps=['systemd'], desc='Btrfs-Snapshots'),
    dict(name='paru', source='aur', version='2.1.0-2', size=8.4, deps=['glibc', 'openssl'], desc='AUR-Helfer'),
    dict(name='visual-studio-code-bin', source='aur', version='1.106.0-1', size=402.5, deps=['glibc', 'libglvnd'], desc='VS Code (Binärpaket)'),
    dict(name='spotify', source='aur', version='1:1.2.74-1', size=301.2, deps=['openssl', 'zstd'], desc='Musik-Streaming'),
    dict(name='python-yt-dlp-git', source='aur', version='2025.11.12.r4-1', size=18.0, deps=['glibc'], desc='Video-Downloader (Git)'),
    dict(name='nvidia-tweaks', source='aur', version='0.9-1', size=0.1, deps=['nvidia-utils', 'linux-cachyos-nvidia'], desc='NVIDIA-Optimierungen (verwaist)'),
    dict(name='screen-recorder-plus-bin', source='aur', version='3.4.1-1', size=24.0, deps=['qt6-base', 'glibc'], desc='Bildschirmrekorder (Demo-Fall für den AUR-Scanner)'),
    dict(name='org.mozilla.firefox', source='flatpak', version='145.0', size=274.0, deps=['org.freedesktop.Platform//24.08'], desc='Firefox'),
    dict(name='com.valvesoftware.Steam', source='flatpak', version='1.0.0.83', size=96.0, deps=['org.freedesktop.Platform//24.08'], desc='Steam'),
    dict(name='org.libreoffice.LibreOffice', source='flatpak', version='25.8.3', size=612.0, deps=['org.freedesktop.Platform//23.08'], desc='LibreOffice'),
    dict(name='org.freedesktop.Platform//24.08', source='flatpak', version='24.08.27', size=520.0, deps=[], desc='Flatpak-Runtime'),
    dict(name='org.freedesktop.Platform//23.08', source='flatpak', version='23.08.31', size=498.0, deps=[], desc='Flatpak-Runtime (EOL)'),
]

# Anstehende Updates mit Risiko-Faktoren (Punkte addieren sich zum Score, max. 100)
UPDATES: list[dict] = [
    dict(name='linux-cachyos', source='repo', old='6.17.7-1', new='6.18.0-1', size=148.9, reasons=[
        ('Neue Kernel-Hauptversion', 30), ('Kernel – Neustart nötig', 15), ('Out-of-tree-Module betroffen (NVIDIA)', 20)]),
    dict(name='linux-cachyos-nvidia', source='repo', old='580.95.05-3', new='580.95.05-4', size=92.4, reasons=[
        ('Kernel-Modul – nur zusammen mit Kernel', 20), ('Neustart nötig', 10)]),
    dict(name='glibc', source='repo', old='2.42+r33-1', new='2.42+r41-1', size=48.5, reasons=[
        ('Kernbibliothek – alle Programme betroffen', 25), ('Behebt CVE-2025-8058', -10)]),
    dict(name='openssl', source='repo', old='3.6.0-1', new='3.6.0-2', size=12.0, reasons=[
        ('Sicherheitsupdate', -5), ('Dienste müssen neu starten', 10)]),
    dict(name='mesa', source='repo', old='1:25.2.6-1', new='1:25.3.0-1', size=65.9, reasons=[
        ('Neue Minor-Version Grafikstack', 20), ('Abhängig von llvm-libs 21.1.6', 10)]),
    dict(name='llvm-libs', source='repo', old='21.1.5-1', new='21.1.6-1', size=142.3, reasons=[('Patch-Release', 5)]),
    dict(name='systemd', source='repo', old='258.2-1', new='258.3-1', size=33.6, reasons=[
        ('Init-System', 15), ('.pacnew für /etc/systemd/journald.conf erwartet', 10)]),
    dict(name='plasma-desktop', source='repo', old='6.5.2-1', new='6.5.3-1', size=48.1, reasons=[('Bugfix-Release', 5)]),
    dict(name='kwin', source='repo', old='6.5.2-1', new='6.5.3-1', size=29.8, reasons=[('Bugfix-Release', 5), ('Sitzung neu anmelden', 5)]),
    dict(name='paru', source='aur', old='2.1.0-2', new='2.1.0-3', size=8.4, reasons=[('AUR – Build aus Quellcode', 10)]),
    dict(name='visual-studio-code-bin', source='aur', old='1.106.0-1', new='1.106.1-1', size=403.0, reasons=[('AUR – Binärpaket, Fremdquelle', 15)]),
    dict(name='spotify', source='aur', old='1:1.2.74-1', new='1:1.2.76-1', size=302.0, reasons=[('AUR – Fremdquelle', 10), ('PKGBUILD-Checksumme geändert', 15)]),
    dict(name='nvidia-tweaks', source='aur', old='0.9-1', new='0.10-1', size=0.1, reasons=[
        ('AUR – verwaist (kein Maintainer)', 25), ('Gegen alten Kernel getestet', 20)]),
    dict(name='screen-recorder-plus-bin', source='aur', old='3.4.1-1', new='3.4.2-1', size=24.3, reasons=[
        ('AUR – Binärpaket, Fremdquelle', 15)]),
    dict(name='org.mozilla.firefox', source='flatpak', old='145.0', new='145.0.1', size=88.0, reasons=[('Sicherheitsupdate', -5), ('Sandbox (Flatpak)', 5)]),
    dict(name='com.valvesoftware.Steam', source='flatpak', old='1.0.0.83', new='1.0.0.84', size=12.0, reasons=[('Sandbox (Flatpak)', 5)]),
    dict(name='org.freedesktop.Platform//24.08', source='flatpak', old='24.08.27', new='24.08.29', size=140.0, reasons=[
        ('Runtime – mehrere Apps betroffen', 15)]),
]

SCANS = scan_all(AUR_META)  # AUR-Scanner-Ergebnisse je Paket (Demo)

for _u in UPDATES:
    _scan = SCANS.get(_u['name'])
    if _scan and _scan.score:
        _u['reasons'].append((f'AUR-Scan: {_scan.verdict[0]} ({len(_scan.findings)} Befunde)', round(_scan.score * 0.6)))
    _u['risk'] = max(0, min(100, 10 + sum(p for _, p in _u['reasons'])))
    _u['id'] = _u['name']

CONFLICTS: list[dict] = [
    dict(severity='hoch', title='NVIDIA-Modul passt nur zum neuen Kernel',
         packages=['linux-cachyos', 'linux-cachyos-nvidia', 'nvidia-tweaks'],
         text='linux-cachyos 6.18 benötigt linux-cachyos-nvidia 580.95.05-4. Werden beide nicht gemeinsam '
              'aktualisiert, startet die grafische Sitzung nicht. nvidia-tweaks (AUR, verwaist) ist nur gegen 6.17 getestet.',
         advice='Kernel und NVIDIA-Modul zusammen aktualisieren, nvidia-tweaks zurückhalten.'),
    dict(severity='mittel', title='mesa 25.3 erfordert llvm-libs 21.1.6',
         packages=['mesa', 'llvm-libs'],
         text='Teil-Update führt zu „undefined symbol“ in libgallium. Nur zusammen aktualisieren.',
         advice='Beide auswählen (wird automatisch gruppiert).'),
    dict(severity='mittel', title='AUR-Paket gegen alte Bibliothek gebaut',
         packages=['python-yt-dlp-git', 'openssl'],
         text='python-yt-dlp-git wurde gegen openssl 3.6.0-1 gebaut. Nach dem Update ist ein Neubau empfehlenswert.',
         advice='Nach dem Update neu bauen (paru -S --rebuild).'),
    dict(severity='niedrig', title='Flatpak-Runtime 23.08 ist End-of-Life',
         packages=['org.libreoffice.LibreOffice', 'org.freedesktop.Platform//23.08'],
         text='LibreOffice nutzt noch die nicht mehr gepflegte Runtime 23.08 und erhält darüber keine Sicherheitsfixes.',
         advice='Auf neue App-Version warten oder Runtime manuell entfernen, sobald nicht mehr benötigt.'),
]

CVES: list[dict] = [
    dict(id='CVE-2025-8058', package='glibc', severity='hoch', cvss=7.8, status='behoben durch Update',
         title='Double-free in regcomp()', fixed_in='2.42+r41-1'),
    dict(id='CVE-2025-9230', package='openssl', severity='mittel', cvss=5.9, status='behoben durch Update',
         title='Out-of-bounds read bei CMS-Entschlüsselung', fixed_in='3.6.0-2'),
    dict(id='CVE-2025-13015', package='org.mozilla.firefox', severity='kritisch', cvss=9.8, status='behoben durch Update',
         title='Use-after-free in der JavaScript-Engine', fixed_in='145.0.1'),
    dict(id='CVE-2025-40123', package='linux-cachyos', severity='mittel', cvss=5.5, status='behoben durch Update',
         title='Rechteausweitung im io_uring-Subsystem', fixed_in='6.18.0-1'),
    dict(id='CVE-2025-6020', package='org.freedesktop.Platform//23.08', severity='hoch', cvss=7.8, status='offen – kein Fix (EOL)',
         title='Pfad-Traversal in pam_namespace (Runtime)', fixed_in='—'),
]

NEWS: list[dict] = [
    dict(date='2026-11-12', title='systemd 258.3: journald.conf changed – check .pacnew', manual=True),
    dict(date='2026-11-04', title='NVIDIA 580: update kernel and module together', manual=True),
    dict(date='2026-10-28', title='CachyOS November release with kernel 6.18', manual=False),
]


def package(name: str) -> dict | None:
    return next((p for p in PACKAGES if p['name'] == name), None)


def update(name: str) -> dict | None:
    return next((u for u in UPDATES if u['name'] == name), None)


def dependents(name: str) -> list[str]:
    return [p['name'] for p in PACKAGES if name in p['deps']]


def risk_level(score: int) -> tuple[str, str]:
    if score >= 60:
        return 'hoch', 'negative'
    if score >= 30:
        return 'mittel', 'warning'
    return 'niedrig', 'positive'
