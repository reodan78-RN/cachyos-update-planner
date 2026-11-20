# Changelog

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
