# Changelog

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
