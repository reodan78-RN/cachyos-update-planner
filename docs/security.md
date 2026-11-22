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

## Tray icon

- The tray process only reads: `checkupdates` against its own private database (`~/.cache/update-planner/tray-db`)
  and `flatpak remote-ls --updates`. It never uses `sudo` and has no network server; the planner window is started
  as a separate process on click. State: `~/.cache/update-planner/tray.json` (mode `0600`, names and versions only).

## Scheduled updates

- A schedule stores the time, the dialog options, the *deselected* and the deliberately added updates, the source
  filter and your manual AUR reviews (package name plus the SHA-256 of the reviewed files, see below) in
  `~/.config/update-planner/schedule.json` (mode `0600`). **No password and no other secret** is stored.
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

- **Manual review is bound to the content.** *Mark as reviewed* in the AUR scanner stores a SHA-256 over the
  package name and every reviewed file as a *(file name, content)* pair: the `PKGBUILD` and each `.install` file it
  references via `install=` (`$pkgname`/`$pkgbase` resolved, also inside `package_*()`). An approval covers exactly
  these files with exactly this content – a changed byte, a renamed, added or removed file, or another package name
  gives a different hash. Before building, the executor hashes the cloned files again (plus, conservatively, any other
  `*.install` file in the repository); if the hash differs (new version, maintainer takeover, tampering) it logs that
  the approval no longer applies and asks again whenever the score is 40 or more. The same check decides whether the
  update dialog still lists a package as flagged. Approvals from 1.4.x and earlier (content-only hash) and plans saved
  by 1.3.2 and earlier (names only) no longer match and are asked for again.

- The AUR scanner runs during loading and **again on the exact PKGBUILD that is about to be built**, right after
  cloning. A score of 40 or more asks for confirmation before building.
- AUR dependencies that are not in the official repos are not built automatically. Dependencies from
  `.SRCINFO` must look like `name` or `name<op>version`; anything else (e.g. a value starting with `-` that pacman
  would read as an option) stops the package, and all package lists follow a `--`.
- **Only planned packages are installed.** Of the built package files, only those named in the plan for this
  `pkgbase` are passed to `pacman -U` (a new install: only the requested package; an update: only packages that are
  installed). A build that produces a package already installed from the repos, or two files for one name, is
  stopped; other extra artefacts (e.g. `-debug`) are logged and left alone. Split packages are built once per base.
- `git clone`/`fetch` have a 10-minute limit; every child process is terminated (SIGTERM, then SIGKILL after 5 s)
  and reaped on timeout, error or cancellation – also the `sudo -v` password check.
- Build directories live in `~/.cache/update-planner/aur` with mode `0700`.

The scanner is a heuristic. It finds common malware patterns (download-and-execute, base64-encoded payloads,
reverse shells, persistence, SUID bits, raw IP or paste-site sources, disabled checksums, maintainer takeovers),
but it cannot prove a package safe.

Some rules only apply in a context (network access in `prepare`/`build`/`package`, in install scriptlets). The
scanner tracks functions line by line and ignores braces inside quotes, `${…}` expansions, comments and here-docs.
It errs on the side of reporting: split functions `package_*()` count as `package`, top-level commands (which run as
soon as makepkg reads the PKGBUILD, including `$(…)` in assignments) count like build code, every line of an
`.install` file counts as install code, and if the structure cannot be recognised with certainty (unbalanced braces,
an unterminated string, a function body that is not `{ … }`) the context rules apply to the whole file and an
informational finding *Structure not recognised unambiguously* is shown.

## Before anything changes (preflight)

Before the snapshot and before any `sudo` command, the run checks everything that can be checked without
changing the system, and aborts without having touched anything otherwise: the pacman database lock, all package
names and Flatpak IDs, the required programs (`pacman`, `sudo`, `git`, `makepkg`, `paccache`, `flatpak` as
needed), free space (2 GiB on `/` and in `/var/cache/pacman/pkg` for repo/AUR runs, 1 GiB for AUR builds), that
packages to install exist in the sync databases, and that a removal would not break dependencies. A failing snapshot
also stops the run before pacman starts. This does not make the run a transaction – a download can still fail
halfway, and repo, AUR and Flatpak are separate steps – the snapshot is the way back.

## Network

- HTTPS only, to `aur.archlinux.org`, `security.archlinux.org`, `archlinux.org`, `cachyos.org`,
  `pkgstats.archlinux.de` (package popularity for the install page) and `flathub.org` (Flathub search and
  popular list); package downloads are done by pacman, makepkg and flatpak themselves.
- Search terms are restricted to `[\w.+-]` (up to five words) before they reach `pacman -Ss`, the AUR RPC or
  Flathub; regex characters of the term are escaped and the words follow a `--`, so a term such as `--version`
  is searched for and never read as a pacman option.
- Flatpak IDs must start with a letter, digit or underscore (never `-`) before they are passed to `flatpak`.
- All HTTP clients come from `sysutil.https_client()`: TLS verification, at most 3 redirects, and **every request –
  including each redirect – must be HTTPS to one of the hosts above** (`config.TRUSTED_DOMAINS`, with subdomains);
  anything else is refused before a connection is made.
- Every download has a timeout and a size limit (8 MiB); responses are cached in `~/.cache/update-planner/http`
  (directory `0700`, files `0600` written atomically via a unique temp file). Cache reads do not follow symlinks and
  only accept your own regular files up to 8 MiB.
- If a source is unreachable, the last cached copy is used **and reported**: the overview shows a warning with the
  age of the security data, or the hosts whose data came from the cache, instead of presenting it as current.
- Links from news feeds are only shown when they use `https://` (no `javascript:` links).
- RSS feeds that contain a DTD or entity declarations are rejected (protection against entity expansion).
- Advisory IDs from the tracker are validated before they become part of a link.

## Local web server

- The interface is served by NiceGUI on `127.0.0.1` only (a free port in window mode, 8765 in browser mode).
- `netguard.py` checks every HTTP request and WebSocket handshake: the `Host` header must be `127.0.0.1`,
  `localhost` or `[::1]` (DNS rebinding), an `Origin` header must equal the `Host` (a web page in your browser
  cannot open a connection to the planner – NiceGUI itself would accept any origin), and the connecting socket must
  belong to a process of **your own user** (read from `/proc/net/tcp*`; unknown peers are refused). Other users on
  the machine therefore cannot use the interface; installing still needs your sudo password. Refusals are logged
  with the reason only (no headers or socket data); "user unknown" points at a client in another network namespace
  (container, sandboxed browser) – use the app window.
- A scheduled plan (`~/.config/update-planner/schedule.json`, `0600`) is checked strictly when read: only the
  options the dialog writes, with matching types and ranges, valid package names, finite times, and at most 31 days
  ahead. Anything else is discarded, not acted on. It cannot be protected against other processes of your own user
  (nothing can), but a due plan only ever opens the password dialog – it never runs without your sudo password.
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
