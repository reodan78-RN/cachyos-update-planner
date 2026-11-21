# Security

The planner reads a lot of untrusted input (AUR build scripts, RSS feeds, a security database) and can run
commands as root. This document describes how that is contained.

## Privileges

- The planner refuses to start as root and builds AUR packages only as the normal user.
- Only these commands run via `sudo`:
  - `pacman -Syu --noconfirm --noprogressbar [--ignore …]` – the repo update
  - `pacman -Syu --noconfirm --noprogressbar --needed <packages>` – install new repo packages together with a
    full system update (names validated; never a partial upgrade)
  - `pacman -S --asdeps --needed --noconfirm …` – missing *repo* dependencies of an AUR package
  - `pacman -U --noconfirm …` – install AUR packages that were just built
  - `pacman -Rns --noconfirm …` – remove orphaned packages, only after you confirmed the displayed list
  - `pacman -Rs[n] --noconfirm --noprogressbar <package>` – remove software you chose on the Packages page, after a
    read-only preview (`pacman -Rsp`); kernel, glibc, systemd, pacman, drivers and similar are refused
  - `paccache -rk<N>` and `paccache -ruk0` – clean the package cache, only when selected
  - `snapper -c root create …` – only when Snapper is configured and `snap-pac` is not installed
- Flatpak updates, `flatpak install` and `flatpak uninstall` run as the user, never through `sudo`.

### Flatpak and authorization

- A **user** installation (`--user`) needs no authorization at all.
- A **system-wide** installation or removal is authorized by Flatpak through polkit, not by the planner. Flatpak's
  rule `org.freedesktop.Flatpak.rules` allows members of the group `wheel` in an active local session without a
  prompt; anyone else gets the desktop's authentication dialog. For this the planner does **not** pass
  `--noninteractive` to system-wide operations (it would suppress that dialog); stdin is `/dev/null`, so nothing can
  block on a question.
- Installing an app also installs the runtimes it needs (`Installing runtime/…` in the log). It does not update
  other apps; `flatpak update` is only run for updates you selected on the Updates page.
- New AUR packages go through the same steps as AUR updates (clone, scan the exact PKGBUILD, build as the user,
  `pacman -U`); only the package that was asked for is installed, not every split package that was built.
- No command is run through a shell. Package names and Flatpak IDs are validated against strict patterns before
  they are passed to any command.

## Scheduled updates

- A schedule stores the time, the dialog options, the *deselected* updates, the source filter and the packages you
  reviewed in `~/.config/update-planner/schedule.json` (mode `0600`). **No password and no other secret** is stored.
- When the plan is due, the planner (it must be running) shows a desktop notification (`notify-send`, without a
  shell) and a dialog. The sudo password is asked for **only then**, exactly as for a manual update, and is held in
  memory for that run only. There is no background daemon, no systemd timer and no cached sudo ticket.
- Only one window handles a due plan at a time. If the window is closed, the plan stays due for the next window.

## The sudo password

- Asked for in the password dialog and verified immediately with `sudo -v`.
- Kept only in memory for the duration of one update run, passed **only via stdin** to `sudo -S -k`, and
  discarded afterwards. It never appears in a command line, a log line or a file.
- The input field is cleared after use; after three failed attempts the dialog closes.
- Tests (`tests/test_executor.py`) assert that the password is never in arguments, the live log or the log file.

## AUR builds

- The AUR scanner runs during loading and **again on the exact PKGBUILD that is about to be built**, right after
  cloning. A score of 40 or more asks for confirmation before building.
- AUR dependencies that are not in the official repos are not built automatically.
- Build directories live in `~/.cache/update-planner/aur` with mode `0700`.

The scanner is a heuristic. It finds common malware patterns (download-and-execute, base64-encoded payloads,
reverse shells, persistence, SUID bits, raw IP or paste-site sources, disabled checksums, maintainer takeovers),
but it cannot prove a package safe.

## Network

- HTTPS only, to `aur.archlinux.org`, `security.archlinux.org`, `archlinux.org`, `cachyos.org`,
  `pkgstats.archlinux.de` (package popularity for the install page) and `flathub.org` (Flathub search and
  popular list); package downloads are done by pacman, makepkg and flatpak themselves.
- Search terms are restricted to `[\w.+-]` (up to five words) before they reach `pacman -Ss`, the AUR RPC or
  Flathub; regex characters of the term are escaped and the words follow a `--`, so a term such as `--version`
  is searched for and never read as a pacman option.
- Flatpak IDs must start with a letter, digit or underscore (never `-`) before they are passed to `flatpak`.
- All HTTP clients come from `sysutil.https_client()`: TLS verification, at most 3 redirects, and a redirect to
  `http://` is refused.
- Every download has a timeout and a size limit (8 MiB); responses are cached in `~/.cache/update-planner/http`.
- Links from news feeds are only shown when they use `https://` (no `javascript:` links).
- RSS feeds that contain a DTD or entity declarations are rejected (protection against entity expansion).
- Advisory IDs from the tracker are validated before they become part of a link.

## Local web server

- The interface is served by NiceGUI on `127.0.0.1` only (a free port in window mode, 8765 in browser mode).
- `netguard.py` checks every HTTP request and WebSocket handshake: the `Host` header must be `127.0.0.1`,
  `localhost` or `[::1]` (DNS rebinding), an `Origin` header must equal the `Host` (a web page in your browser
  cannot open a connection to the planner – NiceGUI itself would accept any origin), and the connecting socket must
  belong to a process of **your own user** (read from `/proc/net/tcp*`; unknown peers are refused). Other users on
  the machine therefore cannot use the interface; installing still needs your sudo password.
- The session secret is random per installation (`~/.config/update-planner/secret`, mode `0600`).
- HTML is only rendered for content the planner builds itself; PKGBUILD content is escaped before display.

## Files

| Path | Contents | Mode |
|---|---|---|
| `~/.config/update-planner/secret` | session secret | `0600` |
| `~/.config/update-planner/schedule.json` | scheduled update (time and options, no password) | `0600` |
| `~/.cache/update-planner/logs/update-*.log` | log of every update run (no password) | `0600` |
| `~/.cache/update-planner/checkup-db` | private copy of the pacman sync database | user |
| `~/.cache/update-planner/aur/` | AUR build directories | `0700` |
| `~/.cache/update-planner/http/` | cached HTTPS responses | `0700` |
