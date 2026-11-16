from nicegui import ui

from .. import catalog as md
from .. import state as st
from ..i18n import _
from ..theme import card
from .overview import loading_card

SEV = {'hoch': ('negative', 'error'), 'mittel': ('warning', 'warning'), 'niedrig': ('info', 'info')}
ORDER = ['hoch', 'mittel', 'niedrig']


def page() -> None:
    s = st.get()

    @ui.refreshable
    def content() -> None:
        with ui.column().classes('gap-0'):
            ui.label(_('Potenzielle Inkompatibilitäten')).classes('text-2xl font-bold')
            ui.label(_('Abhängigkeitsanalyse der neuen Versionen, Fremdpaket-Neubauten, Konfigurationsdateien, '
                       'News und Systemzustand')).classes('muted text-sm')
        if loading_card():
            return
        visible = {p['name'] for p in s.visible_packages()}
        items = sorted((c for c in md.CONFLICTS if not c['packages'] or visible & set(c['packages'])),
                       key=lambda c: ORDER.index(c['severity']))
        if not items:
            with card('w-full'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('verified', color='positive', size='26px')
                    ui.label(_('Keine Inkompatibilitäten erkannt.'))
            return

        with ui.grid().classes('w-full gap-4 grid-cols-1 lg:grid-cols-2'):
            for c in items:
                color, icon = SEV[c['severity']]
                with card():
                    with ui.row().classes('items-center gap-3 no-wrap'):
                        ui.icon(icon, size='26px', color=color)
                        ui.label(c['title']).classes('section-title flex-1')
                        ui.badge(_(c['severity']), color=color).props('rounded')
                    if c['packages']:
                        with ui.row().classes('gap-1 my-2'):
                            for p in c['packages'][:12]:
                                src = md.SOURCES[(md.package(p) or {'source': 'repo'})['source']]
                                ui.chip(p, icon=src['icon'], color=None).props('dense outline').style(f'color:{src["color"]}')
                            if len(c['packages']) > 12:
                                ui.label(f'+{len(c["packages"]) - 12}').classes('muted text-sm self-center')
                    ui.label(c['text']).classes('text-sm mt-2')
                    with ui.row().classes('items-start gap-2 mt-3 p-3 w-full no-wrap') \
                            .style('background:rgba(20,184,166,.10);border-radius:12px'):
                        ui.icon('lightbulb', color='primary')
                        ui.label(c['advice']).classes('text-sm')
                    updatable = [p for p in c['packages'] if md.update(p)]
                    with ui.row().classes('w-full justify-end gap-1 mt-2'):
                        if c.get('link'):
                            ui.link(_('Meldung öffnen'), c['link'], new_tab=True).classes('text-primary text-sm self-center mr-auto')
                        if updatable:
                            def hold(names=updatable):
                                s.selected -= set(names)
                                ui.notify(_('{n} Pakete zurückgehalten', n=len(names)), position='top-right')

                            def together(names=updatable):
                                s.selected |= set(names)
                                ui.notify(_('{n} Pakete gemeinsam ausgewählt', n=len(names)), type='positive', position='top-right')
                            ui.button(_('Alle zurückhalten'), icon='pause_circle', on_click=hold).props('flat rounded dense no-caps')
                            ui.button(_('Zusammen aktualisieren'), icon='link', on_click=together).props('flat rounded dense no-caps')

    content()
    s.on_change = content.refresh
