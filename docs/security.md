# Security

The planner reads a lot of untrusted input (AUR build scripts, RSS feeds, a security database) and can run
commands as root. This document describes how that is contained.

## Privileges

- The planner refuses to start as root and builds AUR packages only as the normal user.
- Only these commands run via `sudo`:
  - `pacman -Syu --noconfirm --noprogressbar [--ignore …]` – the repo update
  - `pacman -S --asdeps --needed --noconfirm …` – missing *repo* dependencies of an AUR package
  - `pacman -U --noconfirm …` – install AUR packages that were just built
  - `pacman -Rns --noconfirm …` – remove orphaned packages, only after you confirmed the displayed list
  - `paccache -rk<N>` and `paccache -ruk0` – clean the package cache, only when selected
  - `snapper -c root create …` – only when Snapper is configured and `snap-pac` is not installed
- Flatpak updates run as the user (Flatpak's own polkit rules apply).
- No command is run through a shell. Package names and Flatpak IDs are validated against strict patterns before
  they are passed to any command.

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

- HTTPS only, to `aur.archlinux.org`, `security.archlinux.org`, `archlinux.org` and `cachyos.org`; package
  downloads are done by pacman, makepkg and flatpak themselves.
- Every download has a timeout and a size limit (8 MiB); responses are cached in `~/.cache/update-planner/http`.
- Links from news feeds are only shown when they use `https://` (no `javascript:` links).
- RSS feeds that contain a DTD or entity declarations are rejected (protection against entity expansion).
- Advisory IDs from the tracker are validated before they become part of a link.

## Local web server

- The interface is served by NiceGUI on `127.0.0.1` only (a free port in window mode, 8765 in browser mode).
- Other users on the same machine could open the interface while it runs, but cannot install anything without
  your sudo password.
- The session secret is random per installation (`~/.config/update-planner/secret`, mode `0600`).
- HTML is only rendered for content the planner builds itself; PKGBUILD content is escaped before display.

## Files

| Path | Contents | Mode |
|---|---|---|
| `~/.config/update-planner/secret` | session secret | `0600` |
| `~/.cache/update-planner/logs/update-*.log` | log of every update run (no password) | `0600` |
| `~/.cache/update-planner/checkup-db` | private copy of the pacman sync database | user |
| `~/.cache/update-planner/aur/` | AUR build directories | `0700` |
| `~/.cache/update-planner/http/` | cached HTTPS responses | `0700` |
