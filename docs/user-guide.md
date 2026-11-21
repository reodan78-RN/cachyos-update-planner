# User guide

## Layout

The planner is laid out like a monitoring dashboard:

- **System bar** (top, dark): logo, the source filter (Repo, AUR, Flatpak), the time of the last check,
  *Check for updates* and the theme switch (dark → light → system). Dark is the default.
- **App navigation** (below): Overview, Packages (menu: *Installed*, *Install*), Updates, Conflicts, Security,
  AUR Scanner.
- **Scheduled update:** when an update is scheduled, a chip *Update scheduled HH:MM* appears in the system bar;
  the × cancels the plan.
- **Dashboards:** each page starts with a title and a short description, followed by single values, charts,
  tables and flat panels. Colors carry meaning only: green, yellow, orange and red follow thresholds and severities.

The planner loads the system state in the background when it starts; the pages show a loading panel until it is
ready. *Check for updates* reloads everything.

## Overview

- **Single values:** installed packages, pending updates with download size, the highest risk score, known
  vulnerabilities in installed packages (how many are high or critical, and how many an update closes) and the
  time since the last full system update. Each value is colored by threshold.
- **Package changes per day:** upgraded, installed and removed packages over the last 60 days, read from
  `/var/log/pacman.log`.
- **Risk of pending updates:** how many updates are low, medium or high risk.
- **Riskiest updates**, a **system** panel (distribution, kernel, file system, snapshots, free space, unmerged
  `.pacnew` files, pending reboot) and the latest **Arch and CachyOS news**; announcements that require manual
  intervention are marked.

## Packages

All installed packages with origin, version and number of dependencies. Click a package to see its dependency
graph (two levels down plus the packages that depend on it, trimmed for large trees). Orange rings mark packages
with an update, red rings packages involved in a potential incompatibility. Mouse wheel zooms, dragging moves nodes.

The **Security** column marks every package listed in the Arch Linux Security Tracker with its highest severity
(critical, high, medium, low), plus the number of entries when there is more than one. Sort by this column or tick
*Affected only*. In the graph, affected packages get a solid ring in the severity color; a dashed red ring marks a
potential incompatibility and a dotted green ring an available update.

## Install new packages

*Packages → Install* searches for software you do not have yet.

- **Source:** CachyOS/Arch repos, AUR or Flatpak (Flathub). The repo list shows where a package comes from
  (*CachyOS* for the `cachyos*` repos, otherwise *Arch*).
- **Search:** by name or keyword (also in descriptions); several words must all match. Without a term the 50
  most popular repo and Flatpak packages are shown. Installed packages are hidden in every source. The AUR needs at
  least two characters (it has no full list); the AUR is searched for the longest word and the other words are
  matched locally.
- **Popularity:** repo packages show the pkgstats share of reporting systems, AUR packages the AUR popularity and
  votes, Flatpak apps the installs of the last month. Sorting works within one source.
- **Security:** for repo packages the planner checks the *available* version against the Arch Security Tracker and
  shows the highest severity (click the package for IDs and links). The tracker hardly covers the AUR, so AUR
  packages show flags instead (orphaned, flagged out of date, few votes) and **Check in AUR scanner** scans the
  PKGBUILD right away. Flatpak has no tracker data.
- **Install (n):** tick packages (also across searches) and press the button. The dialog lists them with their
  security badges, offers a snapshot, a dry run and *Flatpak for me only*, then asks for the password if needed.

Repo packages are installed with `pacman -Syu --needed`, so **pending updates are installed with them** – Arch does
not support installing into an outdated system. AUR packages are built as your user after the PKGBUILD was scanned
again; AUR dependencies that are not in the repos are not built automatically. Flatpak apps install as the user
(*Flatpak for me only*) or system-wide, in which case the desktop asks for authentication. The planner checks
where the `flathub` remote is set up: if it exists only system-wide (or only for the user), that scope is used and
the switch is locked; without a `flathub` remote the install button stays disabled. After a **dry run** your
selection stays, so you can install for real right away; after a real installation the list is refreshed.

## Updates

All pending updates with old → new version, size and risk score. Point at the colored risk value to see how the score
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

Single values count the affected packages per severity. Filter by severity or search for a package or CVE.

Each entry shows:

- the **package** in large type with the installed version, and on the right what the planner concludes:
  *fixed by update* (with a button to select the update), *fixed upstream – update not available* or
  *open – no fix*;
- the advisory group (AVG, linked to the tracker) and the type of vulnerability;
- the tracker's fields: **Status** (Vulnerable, Fixed, Unknown, Testing), **Severity** (Critical, High, Medium,
  Low, Unknown), **Fixed** (the fixed version or Unknown), **Affected**, **Installed**, the pending **Update**
  and the attack **Vector**;
- the CVE numbers, each linked to its tracker page;
- the **description** of the first CVE that has one.

Descriptions are fetched from the tracker's CVE pages and cached for seven days. The tracker limits the request
rate, so the first load can take a few seconds longer.

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
   **time** (now, in 1 hour, in 2 hours, at a date and time – see below), snapshot, include AUR, include Flatpak,
   **dry run**, and a risk limit above which updates are held back.
   Under *Clean up after the update* you can choose to
   - **remove orphaned packages** – after the update the planner determines them again and shows the list;
     they are only removed with `pacman -Rns` when you confirm;
   - **clean the package cache** – `paccache` keeps 1, 2 or 3 versions of each installed package and removes all
     cached versions of packages that are no longer installed. The dialog shows the cache size and how much would
     be freed.

   The dialog also opens when there are no updates but something to clean up.
2. The **password dialog** asks for your sudo password and checks it immediately. A dry run needs no password.
3. The **progress dialog** shows overall and phase progress, the phases (Preparation, Snapshot, Repo, AUR,
   Flatpak, Clean up, Finish) and the live log. If the AUR scanner finds suspicious patterns in a package right before
   building it, you are asked whether to build or skip it.
4. At the end you see what succeeded or was skipped, new `.pacnew` files, whether a reboot is required, and the
   path of the log file. The system state is reloaded automatically.

### Scheduling an update

Pick *In 1 hour*, *In 2 hours* or *Time of day* (date and time; the past is rejected) in the context dialog. The
planner saves the plan (no password) and shows a chip in the system bar; only one plan exists at a time, a new
one replaces the old after confirmation. The planner has to be running at that time (a minimized window is
fine, a suspended computer is not – the plan is simply due when it wakes up).

When the time comes you get a desktop notification and the dialog **Scheduled update is due**:
*Start* reloads the system state, applies your selection (updates you had deselected stay out, newly appeared
ones are included) and asks for the sudo password; *Later (15 min)* postpones; *Discard* deletes the plan. A plan
that became due while the planner was closed is offered when you start it next.

### Dry run

The dry run shows what would happen without changing anything: it lists the repo packages pacman would update
(honouring held packages), clones and scans AUR packages and checks their dependencies, and lists the Flatpak
updates, lists orphaned packages and runs `paccache` in dry-run mode. No `sudo` command is run.

## Languages

The interface follows the system language: `LANGUAGE`, `LC_ALL`, `LC_MESSAGES`, `LANG`, then the KDE setting.
German and English are included; every other language falls back to English. Force a language with
`update-planner --lang en` or the environment variable `UPDATE_PLANNER_LANG=en`.

Package names, versions, advisories and news titles are shown as delivered by their sources (usually English).
