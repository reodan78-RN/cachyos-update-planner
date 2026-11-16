# User guide

## Layout

- **Header:** source filter chips (Repo, AUR, Flatpak), *Check for updates*, and the theme switch
  (system → dark → light).
- **Left side:** navigation and a system panel (distribution, kernel, file system, snapshot tool, last system
  update, free space, pending reboot).

The planner loads the system state in the background when it starts; the pages show a loading card until it is
ready. *Check for updates* reloads everything.

## Overview

Key figures (installed packages, pending updates and download size, average risk, vulnerabilities), the six
riskiest updates and the latest Arch and CachyOS news. News that require manual intervention and were published
after your last full system update are highlighted.

## Packages

All installed packages with origin, version and number of dependencies. Click a package to see its dependency
graph (two levels down plus the packages that depend on it, trimmed for large trees). Orange rings mark packages
with an update, red rings packages involved in a potential incompatibility. Mouse wheel zooms, dragging moves nodes.

## Updates

All pending updates with old → new version, size and risk score. Hover over the risk bar to see how the score
was calculated. Select and deselect updates with the checkboxes or the presets *Low risk only*,
*Security updates* and *All*.

Deselected **repo** packages are held back with `pacman --ignore`. Arch does not support partial upgrades, so the
page warns you, and packages that only work together are held back as a group.

### How the risk score works

| Factor | Points |
|---|---|
| Epoch change / new major version / new minor version / patch / rebuild only | +15 / +20 / +8 / +3 / +1 |
| Core system component (kernel, glibc, systemd, pacman, mesa, bootloader, …) | +15 |
| Reboot required | +10 |
| Locally modified configuration file → `.pacnew` expected | +8 |
| Recent news with manual intervention mentions the package | +25 |
| Fixes a known vulnerability | −10 each |
| AUR: built from source / orphaned / flagged out of date / VCS package | +10 / +15 / +5 / +5 |
| AUR scanner score | +60 % of the scan score |
| Flatpak app / Flatpak runtime | +3 / +11 |

Every update starts at 5; the result is capped to 0–100. 0–29 is low, 30–59 medium, 60+ high.

## Conflicts

Cards for everything that can break an update, sorted by severity, each with an explanation and a recommendation:
packages that must be updated together, AUR packages that need a rebuild, a Python minor update, expected or
unmerged `.pacnew` files, foreign packages that are no longer in the AUR, unused Flatpak runtimes, low disk space,
a locked pacman database and a pending reboot. *Hold all back* and *Update together* change your selection.

## Security

Advisories from the Arch Linux Security Tracker that affect installed package versions, with severity, CVE
numbers and status: *fixed by update*, *fixed upstream – update not available* or *open – no fix*. A button
selects the fixing update.

## AUR scanner

Static analysis of `PKGBUILD` and `.install` files, plus AUR metadata (orphaned, few votes, new package,
maintainer change, new download domain). Each finding shows the rule, file and line, whether the line is new in
this update, and advice. The tabs show the full files with marked lines and the diff against the last built
version (taken from the paru, yay or planner cache).

- **Check AUR package:** enter any package name to fetch and scan it *before* installing it.
- **Own PKGBUILD:** paste a PKGBUILD and scan it locally.
- **Verdicts:** Harmless (< 15), Suspicious (15–39), Review manually (40–69), Dangerous (≥ 70).

The scanner matches text patterns. It helps you decide where to look; it does not replace reading the PKGBUILD.

## Running an update

1. **Plan update** (overview) or **Update selected** (updates page) opens the **context dialog**: number of
   packages, download size, highest risk, reboot hint, warnings from the AUR scanner and from news. Options:
   snapshot, include AUR, include Flatpak, **dry run**, and a risk limit above which updates are held back.
2. The **password dialog** asks for your sudo password and checks it immediately. A dry run needs no password.
3. The **progress dialog** shows overall and phase progress, the phases (Preparation, Snapshot, Repo, AUR,
   Flatpak, Finish) and the live log. If the AUR scanner finds suspicious patterns in a package right before
   building it, you are asked whether to build or skip it.
4. At the end you see what succeeded or was skipped, new `.pacnew` files, whether a reboot is required, and the
   path of the log file. The system state is reloaded automatically.

### Dry run

The dry run shows what would happen without changing anything: it lists the repo packages pacman would update
(honouring held packages), clones and scans AUR packages and checks their dependencies, and lists the Flatpak
updates. No `sudo` command is run.

## Languages

The interface follows the system language: `LANGUAGE`, `LC_ALL`, `LC_MESSAGES`, `LANG`, then the KDE setting.
German and English are included; every other language falls back to English. Force a language with
`update-planner --lang en` or the environment variable `UPDATE_PLANNER_LANG=en`.

Package names, versions, advisories and news titles are shown as delivered by their sources (usually English).
