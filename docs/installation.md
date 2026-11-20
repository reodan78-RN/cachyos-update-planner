# Installation & KDE integration

The installer sets the Update Planner up as a small desktop program for the current user: its own Python
environment, a command, an icon, a menu entry and – optionally – autostart. Everything runs as a normal
user; root is only requested (via your sudo password) when an update actually runs.

The window behaves like any other program: it opens on the **virtual desktop you are currently on**.

## 1. Requirements

| Package | Needed for | Required |
|---|---|---|
| `pacman-contrib` | `checkupdates` – checks updates with a private copy of the sync database | yes |
| `expac` | fast reading of installed packages | yes |
| `git` | fetching AUR packages | yes |
| `python-gobject`, `webkit2gtk-4.1` | the app window (GTK/WebKit) | yes |
| `base-devel` | building AUR packages (`makepkg`) | for AUR |
| `flatpak` | Flatpak updates | optional |
| `snapper`, `snap-pac` | Btrfs snapshots before updates | recommended |
| `ttf-fira-sans` | the dashboard typeface (Noto Sans is used otherwise) | recommended |

```bash
sudo pacman -S --needed pacman-contrib expac git python-gobject webkit2gtk-4.1 base-devel
```

Your user must be allowed to use `sudo` (group `wheel`). Python 3.12 or newer is required.

## 2. Install

From the project folder:

```bash
packaging/install-desktop.sh
```

The script

1. checks the requirements (it never installs system packages on its own),
2. creates a Python environment in `~/.local/share/cachyos-update-planner/venv`
   (with access to the system's GTK bindings),
3. links the command `update-planner` into `~/.local/bin`,
4. installs the icon in eight sizes into `~/.local/share/icons/hicolor/` and refreshes the icon cache,
5. adds the launcher `update-planner.desktop` to the application menu (category *System*),
6. removes the desktop-2 window rule that version 1.0.0 installed, if present,
7. runs a self-test.

| Command | Effect |
|---|---|
| `packaging/install-desktop.sh` | install or update |
| `packaging/install-desktop.sh --autostart` | additionally start the planner when you log in |
| `packaging/install-desktop.sh --remove` | remove everything again |

Running the installer again updates the installation in place.

Messages of the installer follow the system language (German or English).

## 3. Starting

- **Application menu →** *System* → *CachyOS Update Planner*
- **KRunner** (Alt+Space) → type “Update Planner”
- **Terminal:** `update-planner`
- **Browser instead of a window:** `update-planner --browser`, then open `http://127.0.0.1:8765`

**Pin to the taskbar:** start the planner → right-click its taskbar entry → *Pin to Task Manager*. Because the
window's app ID and the launcher name match (`update-planner`), the running window belongs to the pinned icon.

> KDE Plasma does not bring newly started windows to the front when another window has focus. If you start
> the planner from a terminal, the window may open behind the terminal.

## 4. Autostart

```bash
packaging/install-desktop.sh --autostart
```

This creates `~/.config/autostart/update-planner.desktop`. At login the planner loads the system state in the
background (repos, AUR, Flathub, security tracker, news). It **never installs anything by itself** – every update
needs your confirmation and your password.

Disable it by deleting that file or under **System Settings → Autostart**.

## 5. Updating and uninstalling

```bash
git pull                                 # get the new version
packaging/install-desktop.sh             # update the installation
packaging/install-desktop.sh --remove    # remove program, menu entry, icon and autostart
```

Removing keeps logs (`~/.cache/update-planner/logs`) and settings (`~/.config/update-planner`).

### Upgrading from 1.0.0

Version 1.0.0 created a second virtual desktop and a KWin rule that moved the planner there. 1.1.0 opens on the
current desktop instead. The installer removes the old rule automatically. A virtual desktop that 1.0.0 created
is **not** removed automatically – remove it under *System Settings → Virtual Desktops* if you do not need it.

## 6. Troubleshooting

| Problem | Solution |
|---|---|
| Window does not appear | It may be behind the active window – check the taskbar. |
| Taskbar shows an old or generic icon | Log out and back in (KWin keeps icons in memory). |
| “Could not load the system state” | Offline? Try `checkupdates` in a terminal. Cached data is used where possible. |
| “Wrong password” | Use your own user's sudo password, not root's. The dialog cancels after three attempts. |
| “pacman database is locked” | Is paru/pacman/Discover running? Close it first. |
| AUR package skipped: “needs packages that are not in the repos” | AUR dependencies are deliberately not built automatically – use `paru -S package`. |
| Wrong language | Start with `--lang de` or `--lang en`, or set `UPDATE_PLANNER_LANG`. |
| Only the browser mode works | `python-gobject` / `webkit2gtk-4.1` missing – install them and rerun the installer. |

Verbose log on start: `update-planner -v`. Update logs: `~/.cache/update-planner/logs/update-*.log`.

## Appendix: integrating your own Python apps the same way

What works for the planner works for any NiceGUI / pywebview app on KDE Plasma (Wayland):

1. **App ID = name of the `.desktop` file.** On Wayland the window reports an app ID (here `update-planner`).
   KDE only matches window, icon and taskbar entry when the launcher is called exactly `<app-id>.desktop`; also
   set `StartupWMClass=<app-id>`. Find the ID via right-click on the title bar → *More Actions* →
   *Configure Special Window Settings* → *Window class*.
2. **Force the GTK backend** of pywebview (`gui='gtk'`) and create the virtual environment with
   `--system-site-packages`, so the system's `python-gobject` and WebKit are used.
3. **Install icons as PNG in fixed sizes** into `~/.local/share/icons/hicolor/<N>x<N>/apps/`, then run
   `gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor`. Without `-t` the command silently fails in user
   folders that have no `index.theme`, and a stale cache hides new icons.
4. **Pick a free port** instead of a fixed one, and terminate the window child process when the server stops.
