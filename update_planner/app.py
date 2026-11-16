"""CachyOS Update Planner – Einstiegspunkt (eigenes Fenster oder Browser)."""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import signal
import sys
from collections.abc import Callable
from pathlib import Path

from nicegui import app, background_tasks, ui
from nicegui.native import find_open_port as native_port

from . import __version__, config, i18n, theme
from . import catalog as md
from . import state as st
from .i18n import _
from .pages import aur_scan, conflicts, overview, packages, security, updates

ICON = Path(__file__).parent / 'static' / 'icon.png'

NAV = [
    ('/', 'Übersicht', 'dashboard', overview.page),
    ('/packages', 'Pakete', 'account_tree', packages.page),
    ('/updates', 'Updates', 'system_update_alt', updates.page),
    ('/conflicts', 'Konflikte', 'call_split', conflicts.page),
    ('/security', 'Sicherheit', 'shield', security.page),
    ('/aur-scan', 'AUR-Scanner', 'policy', aur_scan.page),
]

THEME_CYCLE = {None: True, True: False, False: None}
THEME_ICON = {None: 'brightness_auto', True: 'dark_mode', False: 'light_mode'}
THEME_TIP = {None: 'Design: System', True: 'Design: Dunkel', False: 'Design: Hell'}  # Übersetzung bei Anzeige


def root() -> None:
    theme.apply()
    s = st.get()
    dark = ui.dark_mode(app.storage.user.get('dark'))
    nav_buttons: dict[str, ui.button] = {}

    # ---------- schwebende Kopfzeile ----------
    with ui.header().classes('px-4 pt-3 pb-0'):
        with ui.row().classes('glass w-full items-center px-4 py-2 gap-3 no-wrap'):
            ui.button(icon='menu', on_click=lambda: drawer.toggle()).props('flat round dense').classes('lg:hidden')
            ui.image(ICON).classes('w-10 h-10 shrink-0').props('no-spinner no-transition')
            with ui.column().classes('gap-0'):
                ui.label('CachyOS Update Planner').classes('font-bold text-base leading-tight') \
                    .style('color:var(--text)')
                ui.label('Repo · AUR · Flatpak').classes('muted text-xs leading-tight')
            ui.space()
            with ui.row().classes('gap-1 max-sm:hidden'):
                for key, src in md.SOURCES.items():
                    def toggle(e, key=key):
                        s.sources.symmetric_difference_update({key})
                        if not s.sources:  # mindestens eine Quelle aktiv lassen
                            s.sources.add(key)
                            e.sender.selected = True
                        s.changed()
                    ui.chip(src['label'], icon=src['icon'], selectable=True, selected=True,
                            on_selection_change=toggle).props('outline').classes('chip-src') \
                        .style(f'color:{src["color"]}')

            def check() -> None:
                if md.STATUS['state'] == 'loading':
                    return
                ui.notify(_('Prüfe Repos, AUR, Flathub und Sicherheitsdatenbank …'), position='top-right')
                background_tasks.create(md.refresh(), name='manual-refresh')
            sync_btn = ui.button(icon='sync', on_click=check).props('flat round').classes('text-primary') \
                .tooltip(_('Nach Updates suchen'))

            def cycle_theme() -> None:
                dark.value = THEME_CYCLE[dark.value]
                app.storage.user['dark'] = dark.value
                theme_btn.props(f'icon={THEME_ICON[dark.value]}')
                theme_tip.text = _(THEME_TIP[dark.value])
            theme_btn = ui.button(icon=THEME_ICON[dark.value], on_click=cycle_theme).props('flat round') \
                .style('color:var(--text)')
            with theme_btn:
                theme_tip = ui.tooltip(_(THEME_TIP[dark.value]))

    # ---------- Navigation + Systempanel ----------
    with ui.left_drawer(value=None, fixed=True).props('width=230 breakpoint=1024').classes('p-4 pt-6') as drawer:
        with ui.column().classes('glass w-full p-2 gap-1'):
            for path, label, icon, _fn in NAV:
                nav_buttons[path] = ui.button(_(label), icon=icon, on_click=lambda p=path: ui.navigate.to(p)) \
                    .props('flat no-caps align=left').classes('nav-btn').style('color:var(--text)')

        @ui.refreshable
        def system_panel() -> None:
            info = md.SYSTEM
            with ui.column().classes('glass w-full p-4 gap-1 mt-4'):
                ui.label('System').classes('muted text-xs')
                if not info:
                    ui.label(md.STATUS['step'] or _('wird geladen …')).classes('text-sm muted')
                    return
                ui.label(f'{info.get("os", "Linux")} · Kernel {info.get("kernel", "?")}').classes('text-sm')
                snap = 'snap-pac' if info.get('snap_pac') else 'Snapper' if info.get('snapper') else _('keine Snapshots')
                ui.label(f'{info.get("fs", "?")} · {snap}').classes('text-sm')
                ui.label(_('Letztes Update: {when}', when=md.last_upgrade_text())).classes('text-sm')
                ui.label(_('Frei: {gib} GiB', gib=f'{info.get("free_gib", 0):.0f}')).classes('text-sm')
                if info.get('reboot_pending'):
                    ui.badge(_('Neustart ausstehend'), color='negative').props('rounded')
            ui.label(f'v{__version__}' + (' · Demo' if md.STATUS.get('demo') else '')).classes('muted text-xs mt-2 self-center')
        system_panel()

    def wrap(path: str, builder: Callable[[], None]) -> Callable[[], None]:
        def build() -> None:
            for p, b in nav_buttons.items():
                b.classes(add='nav-active') if p == path else b.classes(remove='nav-active')
            with ui.column().classes('w-full max-w-[1400px] mx-auto gap-5 p-2'):
                builder()
        return build

    ui.sub_pages({path: wrap(path, fn) for path, _label, _icon, fn in NAV}).classes('w-full')

    # ---------- Systemstand beobachten (Laden im Hintergrund, Update fertig, Fehler) ----------
    seen = {'state': None, 'step': None}

    def poll() -> None:
        loading = md.STATUS['state'] == 'loading'
        if loading:
            sync_btn.props('loading')
        else:
            sync_btn.props(remove='loading')
        changed = s.sync_catalog()
        if changed or md.STATUS['state'] != seen['state'] or (not md.VERSION and md.STATUS['step'] != seen['step']):
            seen.update(state=md.STATUS['state'], step=md.STATUS['step'])
            system_panel.refresh()
            if not s.busy:
                s.changed()
    ui.timer(0.7, poll)


def main() -> None:
    parser = argparse.ArgumentParser(prog='update-planner', description='CachyOS Update Planner')
    parser.add_argument('--browser', action='store_true', help=_('im Browser statt im eigenen Fenster starten'))
    parser.add_argument('--port', type=int, default=None,
                        help=_('Port (Standard: 8765 im Browser-Modus, im Fenster-Modus ein freier Port)'))
    parser.add_argument('--demo', action='store_true', help=_('Demodaten statt des echten Systems anzeigen'))
    parser.add_argument('--lang', choices=i18n.SUPPORTED, help=_('Sprache erzwingen (Standard: Systemsprache)'))
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    parser.add_argument('-v', '--verbose', action='store_true', help=_('ausführliches Protokoll'))
    args = parser.parse_args()
    i18n.set_language(args.lang)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    if Path('/proc/self').exists() and __import__('os').geteuid() == 0:
        sys.exit(_('update-planner darf nicht als root gestartet werden (sudo wird bei Bedarf abgefragt).'))

    if args.demo:
        md.load_demo()
    else:
        app.on_startup(md.refresh)

    if args.browser:
        args.port = args.port or 8765
    else:
        # GTK/WebKit2 aus dem System (python-gobject, webkit2gtk-4.1) – vermeidet den Qt-Fehlversuch
        app.native.start_args['gui'] = 'gtk'
        app.native.window_args['min_size'] = (900, 600)
        args.port = args.port or native_port()

        def close_window_process() -> None:  # Fenster nicht verwaisen lassen, wenn der Server endet
            for child in multiprocessing.active_children():
                child.terminate()
        app.on_shutdown(close_window_process)
        signal.signal(signal.SIGTERM, lambda *_: app.shutdown())

    ui.run(
        root,
        title='CachyOS Update Planner',
        favicon=ICON,
        native=not args.browser,
        show=not args.browser,
        window_size=(1320, 860) if not args.browser else None,
        port=args.port,
        host='127.0.0.1',
        language=i18n.quasar_language(),
        dark=None,
        reload=False,
        storage_secret=config.storage_secret(),
        show_welcome_message=args.browser,
    )


if __name__ in {'__main__', '__mp_main__'}:
    main()
