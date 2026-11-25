# Changelog

## 1.6.0 – 2026-11-25

- **Known issues for pending updates:** reports from the Arch forum (*Pacman & Package Upgrade Issues*), CachyOS'
  package repositories on GitHub and Arch GitLab (`scope::bug`) are matched to pending updates. A warning icon on the
  Updates page lists them with links; the overview shows how many updates are affected. They raise the risk score by
  up to 20 points (tracker bug +15, forum thread +10, solved 0) – only for reports that name the new version or
  appeared after it was built. Short or common package names only count when the new version is named.
- `api.github.com`/`github.com` added to the trusted hosts (CachyOS issue lists).

## 1.5.0 – 2026-11-22

Security review follow-up.

- **AUR scanner:** function context is tracked properly (braces in strings, `${…}`, comments and here-docs no longer
  shift it; one-line and `function name {` functions; `package_*()` split functions count as `package`). Top-level
  commands and `$(…)` in assignments are checked like build code, `.install` files are checked as install code on
  every line, and an unrecognisable structure applies the context rules to the whole file (new info finding
  `AUR000`). `.install` files referenced as `$pkgname.install` are now fetched and scanned too.
- **Review approvals** hash the package name and each *(file name, content)* pair. Existing approvals no longer match
  and are asked for again once.
- **Preflight** before any change: required programs, free space, repo packages to install exist, removal would not
  break dependencies (previously only checked in a dry run).
- **AUR install:** only packages named in the plan are installed; artefacts that would replace an installed repo
  package are refused; dependencies from `.SRCINFO` are validated and passed after `--`; split packages build once.
- **Processes:** every child is terminated and reaped (SIGTERM → SIGKILL) on timeout, error or cancellation,
  including the `sudo -v` password check; `git` has a 10-minute limit; `\r\n` split across read chunks no longer
  yields an empty line.
- **Network/cache:** requests and redirects only go to the known hosts; cache files are `0600`, never followed through
  symlinks, size-checked; stale cache fallbacks are shown as a warning with their age.
- **Schedule file** is validated strictly (types, ranges, names, at most 31 days ahead) and ignored if implausible;
  scheduling more than 31 days ahead is refused.
- CVE detail lookups only swallow network/format errors; programming errors are logged and raised. Remote AUR and
  tracker data are type-checked.
- Log files no longer collide when two runs start in the same millisecond.
- **CI:** GitHub Actions runs `ruff check` and the tests on Python 3.12–3.14 for every push and pull request, with a
  coverage gate (88 %) on the security-critical modules.

## 1.4.1 – 2026-11-22

- **Tray icon:** the count now matches the planner – AUR updates are included, and the tray re-checks when the
  planner synchronised its database, when the planner window is closed and when the tray menu is opened with
  data older than 5 minutes (before, it could show an hour-old number).

## 1.4.0 – 2026-11-22

- **Tray icon:** new program `update-planner-tray` (lightweight Qt StatusNotifier, no web view). It starts on login,
  checks for repo and Flatpak updates (first check 30 s after start, then hourly, and right after `pacman.log`
  changes), shows the number as a red badge on the icon and in the tooltip, and pops up a notification when
  *new* updates appeared (already announced updates are remembered in `~/.cache/update-planner/tray.json`).
  Click on the icon or the popup opens the planner; the menu offers *Check now* and *Quit*. Needs `pyside6`.
- `packaging/install-desktop.sh --autostart` now starts the **tray icon** on login (it replaces the old window
  autostart entry); the tray icon is opened from the application menu as "CachyOS Update Planner (Tray)" too.

## 1.3.4 – 2026-11-21

- **Security:** *Mark as reviewed* in the AUR scanner is now bound to a SHA-256 of the reviewed `PKGBUILD`/`.install`.
  A changed file invalidates the approval (also in scheduled plans, which now store the hash); the scanner asks again
  before building. Name-only approvals from older plans are discarded.
- Log files get a millisecond suffix, so two runs in the same second can no longer collide.

## 1.3.3 – 2026-11-21

- **Safer removal:** a package must be ticked with the checkbox in the Packages list first; the **Remove selected
  package …** button sits above the dependency graph and is only enabled for a ticked package (never for system
  components or Flatpak runtimes). A confirmation dialog ("Really remove …?") shows pacman's preview before anything happens.

## 1.3.2 – 2026-11-21

- **Remove software:** the Packages page has a **Remove …** button. The dialog previews what `pacman -Rsp` would
  remove (and blocks when other packages depend on it), refuses system components, offers snapshot, deleting
  configuration files and a dry run. Flatpak apps are removed in their own scope, optionally with their data.
- **Clearer wording:** installations and removals no longer say "Update" (dialog titles, log); the menu entry is now
  *Search and install packages* (*Pakete suchen und installieren*).
- **Flatpak authorization:** system-wide Flatpak installs/removals no longer use `--noninteractive`, which suppressed the
  polkit dialog; the install dialog explains that Flatpak needs no sudo password.

## 1.3.1 – 2026-11-21

- **Install page:** the start list of repo packages (no search term) now shows only packages that no other repo
  package depends on, so it lists software instead of libraries (htop, vlc, gparted … instead of python-wheel, zlib …).

## 1.3.0 – 2026-11-21

- **Install new packages:** new page *Packages → Install* (`/packages/install`). Search the CachyOS/Arch repos,
  the AUR and Flathub by name or keyword, sorted by popularity (pkgstats for repos, AUR popularity, Flathub installs
  per month). Without a search term the top 50 repo and Flatpak packages are listed; installed packages are hidden.
  Multi-select, a details panel (description, dependencies, open vulnerabilities, AUR flags) and the button
  *Install (n)*.
- **Security hints for candidates:** repo packages are checked against the Arch Security Tracker for the
  *available* version; AUR packages show *orphaned*, *flagged out of date* and *few votes* and can be checked with
  the AUR scanner; Flatpak has no tracker data.
- **Install dialog and run:** repo packages are installed with `pacman -Syu --needed <packages>` (pending updates
  come along – Arch has no partial upgrades), AUR packages are built by the existing, re-scanning AUR step, Flatpak
  apps with `flatpak install --user|--system flathub <id>`. Snapshot and dry run as for updates.
- **Schedule an update:** the *Plan update* dialog has a *Time* choice: now, in 1 hour, in 2 hours or a date and
  time. The plan is stored without secrets in `~/.config/update-planner/schedule.json`; when it is due the planner
  shows a notification and a dialog (*Start*, *Later (15 min)*, *Discard*) and only then asks for the sudo
  password. Updates that appeared in the meantime are included; deselected ones stay out. A chip in the system bar
  shows the plan and cancels it.
- **Overview:** the tile is now *Vulnerabilities in installed pkgs* and shows how many of them an update closes.
- **Security hardening:** the local web server now rejects foreign `Host`/`Origin` headers and connections from other
  users (cross-site WebSocket hijacking, DNS rebinding); all HTTP clients refuse redirects to `http://`; search terms
  and Flatpak IDs can no longer become command options.
- Submenu under *Packages* in the navigation (*Installed*, *Install*).

## 1.2.0 – 2026-11-20

- **Dashboard design modelled on Splunk:** dark system bar and horizontal app navigation instead of the side
  panel, flat panels with 1-px borders, single values colored by thresholds, dense zebra tables with colored
  risk cells, sentence-case buttons, no glass, blur, shadows or gradients. Dark theme by default; the light
  theme follows Splunk Enterprise. Typeface Fira Sans (falls back to Noto Sans).
- **Overview:** new chart of package changes per day over 60 days from `pacman.log`, risk distribution,
  riskiest updates as a table, system panel.
- **Security page rebuilt:** package in large type; tracker fields Status, Severity, Fixed, Affected,
  Installed, Update and Vector; CVE links; description from the tracker's CVE pages (cached, rate-limit aware);
  filter by severity and search.
- **Clean up after updates:** the update dialog offers to remove orphaned packages (the list is shown again
  after the update and nothing is removed without confirmation) and to clean the package cache with `paccache`
  (keep 1–3 versions, remove uninstalled packages), with the current cache size and expected savings. New phase
  *Clean up*; the dry run lists orphans and runs `paccache` read-only.
- **Package overview marks vulnerable packages:** new *Security* column with the highest severity and the number
  of tracker entries, *Affected only* filter, severity-colored rings in the dependency graph.
- **New logo** with transparent background.
- Source colors and charts use the Splunk series palette.

## 1.1.0 – 2026-11-16

- **Opens on the current virtual desktop.** 1.0.0 created a second virtual desktop and a KWin rule that moved
  the window there; the rule is gone and the installer removes it from existing installations.
- **Follows the system language.** All texts in the interface, dialogs, update log, scanner findings, risk
  reasons and installer messages are translated (German and English; other languages fall back to English).
  `--lang de|en` and `UPDATE_PLANNER_LANG` force a language. Quasar's built-in texts follow as well.
- Fixed: the reboot hint in the update dialog depended on German text; it now uses the package rules.
- Demo news are in English, like the real feeds.
- Documentation rewritten in English: installation, user guide, architecture, security.
- 53 tests, including translation completeness and a check for untranslated UI texts.

## 1.0.0 – 2026-11-16

From mockup to a working application.

- Real data sources: installed packages (expac), repo updates via `checkupdates` with a private database copy,
  AUR RPC, Flatpak, Arch Linux Security Tracker, Arch and CachyOS news, system state
- Analysis: risk score from version jump, core components, reboot, `.pacnew`, news and security fixes;
  "update together" groups from versioned dependencies, sonames and pkgbase; AUR rebuilds
- Real update: `sudo pacman -Syu` with `--ignore`, AUR builds with `makepkg` (scanned right before building),
  `flatpak update`, Snapper/snap-pac detection, progress from pacman's output, dry run, logs
- AUR scanner: checks any AUR package before installing, maintainer change detected from the PKGBUILD
- Security: password only via stdin, random session secret, HTTPS-only feed links, DTD-free RSS parsing,
  size limits, refuses to run as root
- 40 tests

## 0.1.0

Clickable mockup with demo data (now available as `update-planner --demo`).
