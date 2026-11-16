# Architecture

```text
update_planner/
├── app.py            entry point, header, navigation, system panel, CLI
├── catalog.py        loads the system state in the background; demo mode
├── analysis.py       risk score, "update together" groups, foreign rebuilds
├── aur_scanner.py    static PKGBUILD/.install analysis, diff
├── executor.py       update run (sudo, makepkg, flatpak), dry run, log
├── dialogs.py        context, password, confirmation and progress dialogs
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
│   ├── news.py       Arch and CachyOS RSS
│   └── system.py     kernel, file system, snapper/snap-pac, free space, pending reboot
├── pages/            overview, packages, updates, conflicts, security, aur_scan
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
| `test_parsers.py` | pacman output, versions, name validation, tracker matching, news, sizes |
| `test_analysis.py` | update-together groups, soname links, split packages, foreign rebuilds, risk reasons |
| `test_executor.py` | update run with a fake process start: password only via stdin, dry run never uses sudo, aborts |
| `test_hardening.py` | session secret, C locale, download size limit, server smoke test bound to 127.0.0.1 |
| `test_i18n.py`, `test_i18n_ui.py` | catalog completeness, placeholders, language detection, no raw UI literals |
