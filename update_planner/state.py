"""Zustand pro Browser-Verbindung (Auswahl, Quellenfilter)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from nicegui import app

from . import catalog as md


def default_selection() -> set[str]:
    """Vollständiges Systemupdate ist der Arch-Normalfall – nur auffällige AUR-Pakete bleiben draußen."""
    sel = set()
    for u in md.UPDATES:
        scan = md.SCANS.get(u['name'])
        if u['source'] == 'aur' and scan and scan.score >= 40:
            continue
        if u['risk'] >= 80:
            continue
        sel.add(u['name'])
    return sel


@dataclass
class State:
    sources: set[str] = field(default_factory=lambda: {'repo', 'aur', 'flatpak'})
    selected: set[str] = field(default_factory=set)
    reviewed: set[str] = field(default_factory=set)  # im AUR-Scanner manuell geprüft
    seen_version: int = -1
    busy: bool = False                                # Update läuft
    on_change: Callable[[], None] | None = None

    def sync_catalog(self) -> bool:
        """Nach einem neuen Systemstand die Standardauswahl setzen. True, wenn sich etwas geändert hat."""
        if self.seen_version == md.VERSION:
            return False
        self.seen_version = md.VERSION
        self.selected = default_selection()
        return True

    def visible_updates(self) -> list[dict]:
        return [u for u in md.UPDATES if u['source'] in self.sources]

    def visible_packages(self) -> list[dict]:
        return [p for p in md.PACKAGES if p['source'] in self.sources]

    def selected_updates(self) -> list[dict]:
        return [u for u in self.visible_updates() if u['name'] in self.selected]

    def changed(self) -> None:
        if self.on_change:
            self.on_change()


def get() -> State:
    store = app.storage.client
    if 'state' not in store:
        store['state'] = State()
        store['state'].sync_catalog()
    return store['state']
