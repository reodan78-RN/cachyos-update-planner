# Architecture

```text
update_planner/
├── app.py            entry point, header, navigation, system panel, CLI
├── catalog.py        loads the system state in the background; demo mode
├── analysis.py       risk score, "update together" groups, foreign rebuilds
├── aur_scanner.py    static PKGBUILD/.install analysis, diff
├── executor.py       update run (sudo, makepkg, flatpak), dry run, log
├── dialogs.py        context, password, confirmation, progress, install and "update due" dialogs
├── scheduler.py      scheduled update: time calculation, schedule.json, claim/release between windows
├── state.py          per-window state (selection, filters, reviewed packages)
├── theme.py          glass design, light/dark
├── i18n.py           language detection and _() translation function
├── i18n_keys.py      collects all translatable source texts (used by tests)
├── locales/en.py     English catalog (keys are the German source texts)
├── config.py         paths, constants, per-install session secret
├── sysutil.py        safe subprocess calls, vercmp, size-limited HTTPS with cache
├── sources/          read-only data sources
│   ├── pacman.py     expac, checkupdates (private db copy), pacman -Si/-Qii, pacman.log, .pacnew
│   ├── aur.py        AUR RPC v5, PKGBUILD/.install from aur.archlinux.org, paru/yay caches
│   ├── flatpak.py    flatpak list / remote-ls
│   ├── security.py   Arch Linux Security Tracker
│   ├── discover.py   candidates for new packages: pacman -Sl/-Ss, pkgstats, AUR RPC search, Flathub API
│   ├── news.py       Arch and CachyOS RSS
│   └── system.py     kernel, file system, snapper/snap-pac, free space, pending reboot
├── pages/            overview, packages, install, updates, conflicts, security, aur_scan
├── mockdata.py, aur_samples.py   demo data (--demo, tests)
└── static/icon.png
```

## Data flow

1. On startup `catalog.refresh()` runs in the background. It reads installed packages, runs `checkupdates`
   with `CHECKUPDATES_DB=~/.cache/update-planner/checkup-db` (the system database is never touched), reads the new
   versions' dependencies from that private copy with `pacman -Si --dbpath`, checks Flatpak and the AUR, loads the
   security tracker and the news, and scans AUR PKGBUILDs of pending AUR updates.
2. `_assemble()` turns this into the structures the pages use (`PACKAGES`, `UPDATES`, `CONFLICTS`, `CVES`,
   `NEWS`, `SCANS`, `GROUPS`, `SYSTEM`) and increments `VERSION`.
3. Each window polls `VERSION` and redraws when a new state is published. Network failures become warnings;
   the last cached responses are used where possible.

## Conflict analysis

`analysis.together_groups()` builds a graph between pending repo updates: an edge A → B exists when a
dependency of A's **new** version (versioned dependency like `foo>=2` or soname like `libfoo.so=2-64`) is only
satisfied by B's **new** version. Split packages sharing a `pkgbase` are always joined. Connected components with
two or more packages become "update together" groups; holding back any member holds back the whole group.

`foreign_rebuilds()` finds AUR packages whose dependencies are no longer satisfied after the update.

## Update run

`executor.Runner` executes a `Plan` (repo yes/no and held packages, AUR packages, Flatpak IDs, snapshot, dry
run). Phases: Preparation → Snapshot → Repo → AUR → Flatpak → Finish. Progress is derived from pacman's output
(`Packages (n)`, `(i/n) upgrading …`, phase markers). Details on privileges are in [security.md](security.md).

## Installing new packages

`sources/discover.py` returns candidates in one format (`name, source, repo, version, desc, popularity,
pop_label, votes, flags, base, origin, sec`). Repo candidates come from `pacman -Sl/-Ss --dbpath` on the private
checkupdates copy (so they match what `-Syu` will install); popularity from pkgstats (five pages of 10 000, cached
for 24 h); AUR from RPC `search/<term>?by=name-desc`; Flatpak from Flathub's `collection/popular` and `POST /search`.
`annotate_security()` reuses `security.relevant()` with the raw tracker data (`catalog.ADVISORIES`) for the
*available* version of each repo candidate.

`Plan` gained `install` (repo, run as `pacman -Syu --needed`), `install_aur` (subset of `aur` to be installed
rather than updated – the build step then selects exactly the requested package), `flatpak_install` and
`flatpak_user`. `dialogs.start_install()` mirrors `start_update()`.

## Scheduled updates

`scheduler.py` keeps at most one `Schedule` (time, options, deselected updates, sources, reviewed packages) in
memory and in `schedule.json`. The `poll()` timer in `app.root()` compares the wall clock with it; the window that
`claim()`s the plan runs `dialogs.run_scheduled()` (notification, due dialog, reload, selection =
`default_selection() − deselected`, password dialog, progress dialog) and `release()`s on disconnect. Dialogs are
attached to the page layout so a page rebuild after a new system state does not close them.

## Translations

Source texts in the code are German. Every visible text goes through `_()`:

```python
from .i18n import _
ui.label(_('{n} Pakete zurückgehalten', n=3))
```

Internal keys (severities such as `hoch`, statuses such as `behoben durch Update`, verdicts, phase names) stay
German in the data and are translated only when displayed.

### Adding a language

1. Copy `update_planner/locales/en.py` to e.g. `fr.py` and translate the values (keep the `{placeholders}`).
2. Add the code to `SUPPORTED` in `i18n.py` and load the module in `set_language()`.
3. Map the code to a Quasar language in `quasar_language()`.
4. Add the language to `msg()` in the shell scripts if needed.

`tests/test_i18n.py` fails when a source text has no translation, a translation is orphaned, or placeholders
differ; `tests/test_i18n_ui.py` fails when a UI element receives an untranslated literal.

## Tests

```bash
.venv/bin/python -m pytest
```

| File | Covers |
|---|---|
| `test_scanner.py` | scanner rules, scoping, scoring, diff, maintainer change |
| `test_parsers.py` | pacman output, versions, name validation, tracker matching, news, sizes, install-candidate parsers (pacman -Sl/-Ss, pkgstats, AUR, Flathub), search-term restrictions, candidate security |
| `test_scheduler.py` | time calculation (1 h, 2 h, clock time, past), persistence without secrets, claim/release, deselected logic |
| `test_analysis.py` | update-together groups, soname links, split packages, foreign rebuilds, risk reasons |
| `test_executor.py` | update run with a fake process start: password only via stdin, dry run never uses sudo, aborts; install plans (`pacman -Syu --needed`, name validation, AUR new install, Flatpak arguments) |
| `test_hardening.py` | session secret, C locale, download size limit, server smoke test bound to 127.0.0.1 |
| `test_i18n.py`, `test_i18n_ui.py` | catalog completeness, placeholders, language detection, no raw UI literals |
