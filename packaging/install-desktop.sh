#!/usr/bin/env bash
# Installs the CachyOS Update Planner as a KDE mini program for the current user (no root needed).
#
#   packaging/install-desktop.sh                     install / update
#   packaging/install-desktop.sh --autostart         also start on login
#   packaging/install-desktop.sh --remove            remove everything again
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APPDIR="$DATA/cachyos-update-planner"
VENV="$APPDIR/venv"
APPS="$DATA/applications"
HICOLOR="$DATA/icons/hicolor"
SIZES=(16 24 32 48 64 128 256 512)
BIN="$HOME/.local/bin"

# Messages follow the system language (German or English)
msg() { local l="${LANGUAGE:-${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}}"; [[ "$l" == de* ]] && echo "$1" || echo "$2"; }

[[ $EUID -ne 0 ]] || { msg "Bitte als normaler Benutzer ausführen (nicht mit sudo)." "Run as a normal user (not with sudo)."; exit 1; }

autostart=0 remove=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --autostart) autostart=1; shift ;;
        --remove) remove=1; shift ;;
        -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
        *) msg "Unbekannte Option: $1" "Unknown option: $1"; exit 2 ;;
    esac
done

remove_icons() {
    rm -f "$HICOLOR/scalable/apps/update-planner.svg"  # placeholder icon of older versions
    for s in "${SIZES[@]}"; do rm -f "$HICOLOR/${s}x${s}/apps/update-planner.png"; done
}

if (( remove )); then
    bash "$SRC/packaging/kde-integration.sh" remove || true
    rm -rf "$APPDIR"
    rm -f "$BIN/update-planner" "$APPS/update-planner.desktop"
    remove_icons
    gtk-update-icon-cache -q -f -t "$HICOLOR" 2>/dev/null || true
    update-desktop-database "$APPS" 2>/dev/null || true
    msg "Entfernt. (Cache/Protokolle bleiben unter ~/.cache/update-planner, Einstellungen unter ~/.config/update-planner)" \
        "Removed. (Cache/logs stay in ~/.cache/update-planner, settings in ~/.config/update-planner)"
    exit 0
fi

# ---- System requirements (only checked – nothing is installed without your consent)
required=(pacman-contrib expac git python-gobject webkit2gtk-4.1)
optional=(base-devel flatpak snapper ttf-fira-sans)
missing=()
for p in "${required[@]}"; do pacman -Q "$p" >/dev/null 2>&1 || missing+=("$p"); done
if (( ${#missing[@]} )); then
    msg "Fehlende Pakete: ${missing[*]}" "Missing packages: ${missing[*]}"
    echo "  sudo pacman -S --needed ${missing[*]}"
    exit 1
fi
for p in "${optional[@]}"; do
    pacman -Q "$p" >/dev/null 2>&1 || msg "Hinweis: optional fehlt $p (AUR-Builds/Flatpak/Snapshots eingeschränkt)" "Note: optional $p missing (AUR builds/Flatpak/snapshots limited)"
done

echo "→ venv: $VENV"
python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q --upgrade "$SRC"

mkdir -p "$APPS" "$BIN"
ln -sf "$VENV/bin/update-planner" "$BIN/update-planner"
remove_icons
for s in "${SIZES[@]}"; do
    install -Dm644 "$SRC/packaging/icons/update-planner-$s.png" "$HICOLOR/${s}x${s}/apps/update-planner.png"
done
# File name must equal the Wayland app ID "update-planner", otherwise KDE cannot match the window
sed "s|@EXEC@|$VENV/bin/update-planner|" "$SRC/packaging/update-planner.desktop" > "$APPS/update-planner.desktop"
update-desktop-database "$APPS" 2>/dev/null || true
gtk-update-icon-cache -q -f -t "$HICOLOR" 2>/dev/null || true
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true

args=(install)
(( autostart )) && args+=(--autostart)
bash "$SRC/packaging/kde-integration.sh" "${args[@]}"   # also removes the window rule from 1.0.0

# ---- Self-test
"$VENV/bin/update-planner" --version >/dev/null
"$VENV/bin/python" -c "import gi, webview, nicegui, httpx" 2>/dev/null \
    || { msg "Selbsttest fehlgeschlagen: Python-Module fehlen" "Self-test failed: Python modules missing"; exit 1; }
v=$("$VENV/bin/update-planner" --version)
msg "Installiert ($v). Start über das Anwendungsmenü oder: update-planner" "Installed ($v). Start from the application menu or run: update-planner"
