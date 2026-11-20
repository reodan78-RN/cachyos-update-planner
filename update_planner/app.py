"""CachyOS Update Planner – Einstiegspunkt (eigenes Fenster oder Browser)."""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import signal
import sys
from collections.abc import Callable
from datetime import datetime
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
    dark = ui.dark_mode(app.storage.user.get('dark', True))
    nav_items: dict[str, ui.element] = {}

    # ---------- Systemleiste + App-Navigation (wie Splunk: zwei dunkle Leisten) ----------
    with ui.header().classes('p-0 gap-0').style('flex-direction:column;align-items:stretch;flex-wrap:nowrap'):
        with ui.row().classes('sysbar w-full items-center gap-3 no-wrap'):
            ui.image(ICON).classes('w-7 h-7 shrink-0').props('no-spinner no-transition')
            ui.label('CachyOS Update Planner').classes('app-name')
            ui.space()
            labels = {k: v['label'] for k, v in md.SOURCES.items()}

            def set_sources(e) -> None:
                chosen = set(e.value or [])
                if not chosen:                     # mindestens eine Quelle aktiv lassen
                    chosen = set(s.sources)
                    e.sender.value = sorted(chosen)
                s.sources = chosen
                s.changed()
            src = ui.select(labels, multiple=True, value=sorted(s.sources), on_change=set_sources) \
                .props('dense borderless options-dense dark').classes('src-select w-64 max-sm:hidden')
            src.props(f'prefix="{_("Quellen")}:"')

            checked = ui.label('').classes('meta max-sm:hidden')

            def check() -> None:
                if md.STATUS['state'] == 'loading':
                    return
                ui.notify(_('Prüfe Repos, AUR, Flathub und Sicherheitsdatenbank …'), position='top-right')
                background_tasks.create(md.refresh(), name='manual-refresh')
            sync_btn = ui.button(icon='refresh', on_click=check).props('flat dense').tooltip(_('Nach Updates suchen'))

            def cycle_theme() -> None:
                dark.value = THEME_CYCLE[dark.value]
                app.storage.user['dark'] = dark.value
                theme_btn.props(f'icon={THEME_ICON[dark.value]}')
                theme_tip.text = _(THEME_TIP[dark.value])
            theme_btn = ui.button(icon=THEME_ICON[dark.value], on_click=cycle_theme).props('flat dense')
            with theme_btn:
                theme_tip = ui.tooltip(_(THEME_TIP[dark.value]))
            ui.label(f'v{__version__}' + (' Demo' if md.STATUS.get('demo') else '')).classes('meta')

        with ui.row().classes('appnav w-full items-center gap-0 no-wrap overflow-x-auto'):
            for path, label, _icon, _fn in NAV:
                item = ui.label(_(label)).classes('nav-item').props('tabindex=0 role=link')
                item.on('click', lambda p=path: ui.navigate.to(p))
                item.on('keydown.enter', lambda p=path: ui.navigate.to(p))
                nav_items[path] = item

    def wrap(path: str, builder: Callable[[], None]) -> Callable[[], None]:
        def build() -> None:
            for p, item in nav_items.items():
                item.classes(add='active') if p == path else item.classes(remove='active')
            with ui.column().classes('w-full max-w-[1500px] mx-auto gap-3 px-4 pt-4 pb-8'):
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
        loaded = md.STATUS.get('loaded_at')
        checked.text = (md.STATUS['step'] if loading else
                        _('Geprüft {time}', time=datetime.fromtimestamp(loaded).strftime('%H:%M')) if loaded else '')
        changed = s.sync_catalog()
        if changed or md.STATUS['state'] != seen['state'] or (not md.VERSION and md.STATUS['step'] != seen['step']):
            seen.update(state=md.STATUS['state'], step=md.STATUS['step'])
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
