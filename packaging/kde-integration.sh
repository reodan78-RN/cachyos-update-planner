#!/usr/bin/env bash
# KDE Plasma integration for the Update Planner.
# The window opens like any other program on the current virtual desktop – no window rule is installed.
#
#   kde-integration.sh install [--autostart]   set up autostart on login (optional)
#   kde-integration.sh remove                  remove autostart, clean up rules of older versions
set -euo pipefail

APP_ID="update-planner"
LEGACY_RULE="6f1c3d27-0a4f-4b9a-8e21-7570706c616e"   # window rule installed by version 1.0.0
CFG="${XDG_CONFIG_HOME:-$HOME/.config}"
AUTOSTART="$CFG/autostart/$APP_ID.desktop"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications/$APP_ID.desktop"

# Messages follow the system language (German or English)
msg() { local l="${LANGUAGE:-${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}}"; [[ "$l" == de* ]] && echo "$1" || echo "$2"; }

remove_legacy_rule() {
    command -v kwriteconfig6 >/dev/null || return 0
    [[ -f "$CFG/kwinrulesrc" ]] && grep -q "^\[$LEGACY_RULE\]" "$CFG/kwinrulesrc" || return 0
    local key list=""
    for key in Order rules; do
        list=$(kreadconfig6 --file kwinrulesrc --group General --key "$key" | tr ',' '\n' | grep -vx "$LEGACY_RULE" | paste -sd, - || true)
        kwriteconfig6 --file kwinrulesrc --group General --key "$key" "$list"
    done
    if [[ -n "$list" ]]; then
        kwriteconfig6 --file kwinrulesrc --group General --key count "$(tr ',' '\n' <<<"$list" | grep -c .)"
    else
        kwriteconfig6 --file kwinrulesrc --group General --key count 0
    fi
    for key in Description wmclass wmclassmatch wmclasscomplete types desktops desktopsrule; do
        kwriteconfig6 --file kwinrulesrc --group "$LEGACY_RULE" --key "$key" --delete
    done
    command -v qdbus6 >/dev/null && qdbus6 org.kde.KWin /KWin reconfigure >/dev/null 2>&1 || true
    msg "→ Fensterregel einer älteren Version entfernt" "→ Removed window rule of an older version"
}

install() {
    local autostart=0
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --autostart) autostart=1; shift ;;
            *) msg "Unbekannte Option: $1" "Unknown option: $1"; exit 2 ;;
        esac
    done
    remove_legacy_rule
    if (( autostart )); then
        [[ -f "$APPS" ]] || { msg "Zuerst install-desktop.sh ausführen (Starter fehlt)" "Run install-desktop.sh first (launcher missing)"; exit 1; }
        mkdir -p "$(dirname "$AUTOSTART")"
        { cat "$APPS"; echo "X-KDE-autostart-after=panel"; } > "$AUTOSTART"
        msg "→ Autostart eingerichtet ($AUTOSTART)" "→ Autostart enabled ($AUTOSTART)"
    fi
}

remove() {
    remove_legacy_rule
    if [[ -f "$AUTOSTART" ]]; then
        rm -f "$AUTOSTART"
        msg "→ Autostart entfernt" "→ Autostart removed"
    fi
}

case "${1:-}" in
    install) shift; install "$@" ;;
    remove) remove ;;
    *) msg "Aufruf: $0 install [--autostart] | remove" "Usage: $0 install [--autostart] | remove"; exit 2 ;;
esac
