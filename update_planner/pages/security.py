from nicegui import ui

from .. import catalog as md
from .. import state as st
from .. import theme
from ..i18n import _
from ..theme import panel, single_value
from .overview import dashboard_header, loading_card

SEVERITIES = ['kritisch', 'hoch', 'mittel', 'niedrig', 'unbekannt']
ACTION = {
    'behoben durch Update': ('check_circle', theme.GREEN),
    'Fix im Repo – Update nicht verfügbar': ('schedule', theme.YELLOW),
    'offen – kein Fix': ('report', theme.RED),
}


def field(key: str, value: str, color: str | None = None) -> None:
    with ui.element('span'):
        ui.label(key).classes('field-key inline')
        ui.label(value).classes('field-val inline').style(f'color:{color}' if color else '')


def vuln_event(c: dict, s: st.State) -> None:
    sev_color = theme.SEVERITY_COLOR.get(c['severity'], theme.GREY)
    with ui.row().classes('event no-wrap items-stretch gap-3'):
        ui.element('div').classes('sev-stripe').style(f'background:{sev_color}')
        with ui.column().classes('gap-1 flex-1 min-w-0'):
            with ui.row().classes('w-full items-baseline gap-3 no-wrap'):
                ui.label(c['package']).classes('event-pkg')
                ui.label(c.get('installed', '')).classes('muted')
                ui.space()
                icon, color = ACTION.get(c['status'], ('help', theme.GREY))
                with ui.row().classes('items-center gap-1 no-wrap shrink-0'):
                    ui.icon(icon).style(f'color:{color}')
                    ui.label(_(c['status'])).style(f'color:{color}').classes('font-medium')
                if c['status'] == 'behoben durch Update' and md.update(c['package']):
                    def select(c=c):
                        s.selected.add(c['package'])
                        ui.notify(_('{pkg} zum Update ausgewählt', pkg=c['package']), type='positive', position='top-right')
                    ui.button(_('Update auswählen'), on_click=select).props('flat dense color=primary')
            with ui.row().classes('items-baseline gap-2'):
                if c.get('link'):
                    ui.link(c['id'], c['link'], new_tab=True).classes('event-title font-medium')
                else:
                    ui.label(c['id']).classes('event-title font-medium')
                ui.label(c['title'][:1].upper() + c['title'][1:]).classes('event-title')
            with ui.element('div').classes('fields'):
                field(_('Status'), c.get('tracker_status') or 'Unknown')
                field(_('Severity'), c.get('tracker_severity') or 'Unknown',
                      theme.TRACKER_SEVERITY_COLOR.get(c.get('tracker_severity')))
                field(_('Fixed'), c.get('fixed') or 'Unknown')
                if c.get('affected'):
                    field(_('Affected'), c['affected'])
                if c.get('installed'):
                    field(_('Installed'), c['installed'])
                if c.get('new'):
                    field(_('Update'), c['new'])
                if c.get('vector'):
                    field(_('Vector'), c['vector'])
            if c.get('cves'):
                with ui.row().classes('gap-x-3 gap-y-0 flex-wrap'):
                    for cve in c['cves'][:8]:
                        ui.link(cve, f'https://security.archlinux.org/{cve}', new_tab=True).classes('text-sm')
                    if len(c['cves']) > 8:
                        ui.label(_('und {n} weitere', n=len(c['cves']) - 8)).classes('muted text-sm')
            if c.get('description'):
                ui.label(c['description']).classes('desc mt-1')
            else:
                ui.label(_('Keine Beschreibung im Tracker hinterlegt.')).classes('muted mt-1')


def page() -> None:
    s = st.get()
    filters = {'severity': None, 'query': ''}

    @ui.refreshable
    def content() -> None:
        dashboard_header(_('Sicherheitslücken und Risiken'),
                         _('Einträge des Arch Linux Security Tracker, die installierte Paketversionen betreffen. '
                           'Flatpak-Apps werden über Flathub aktualisiert und sind hier nicht erfasst.'))
        if loading_card():
            return
        visible = {p['name'] for p in s.visible_packages()}
        cves = [c for c in md.CVES if c['package'] in visible]

        with ui.grid().classes('w-full gap-3 grid-cols-2 md:grid-cols-5'):
            for sev in SEVERITIES:
                n = sum(1 for c in cves if c['severity'] == sev)
                single_value(_(sev).capitalize(), n, theme.SEVERITY_COLOR[sev] if n else theme.GREY)

        # Eingaben wie in einem Splunk-Dashboard: Filter vor den Panels
        with ui.row().classes('w-full items-center gap-3'):
            ui.select({None: _('Alle Schweregrade'), **{k: _(k).capitalize() for k in SEVERITIES}},
                      value=filters['severity'], label=_('Schweregrad'),
                      on_change=lambda e: (filters.update(severity=e.value), events.refresh())) \
                .props('dense outlined options-dense').classes('w-48')
            ui.input(_('Paket oder CVE'), value=filters['query'],
                     on_change=lambda e: (filters.update(query=(e.value or '').strip().lower()), events.refresh())) \
                .props('dense outlined clearable debounce=250').classes('w-64')

        @ui.refreshable
        def events() -> None:
            shown = [c for c in cves
                     if (not filters['severity'] or c['severity'] == filters['severity'])
                     and (not filters['query'] or filters['query'] in c['package'].lower()
                          or any(filters['query'] in x.lower() for x in [c['id'], *c.get('cves', [])]))]
            with panel(_('Betroffene Pakete'), _('{n} von {total} Einträgen', n=len(shown), total=len(cves)), classes='w-full'):
                if not cves:
                    with ui.row().classes('items-center gap-2 py-2'):
                        ui.icon('verified_user').style(f'color:{theme.GREEN}')
                        ui.label(_('Keine bekannten Sicherheitslücken in installierten Paketen.'))
                elif not shown:
                    ui.label(_('Kein Eintrag passt zum Filter.')).classes('muted py-2')
                with ui.column().classes('w-full gap-0 -mx-3').style('width:calc(100% + 24px)'):
                    for c in shown:
                        vuln_event(c, s)
        events()

        with ui.grid().classes('w-full gap-3 grid-cols-1 lg:grid-cols-2'):
            if 'aur' in s.sources:
                flagged = sorted((r for r in md.SCANS.values() if r and r.score >= 15), key=lambda r: -r.score)
                with panel(_('AUR-Sicherheitsscanner'), _('Pakete mit Score ab 15')):
                    if not flagged:
                        ui.label(_('Keine auffälligen AUR-Pakete.')).classes('muted')
                    for r in flagged:
                        verdict, _color, _icon = r.verdict
                        with ui.row().classes('w-full items-baseline gap-3 no-wrap'):
                            theme.badge(str(r.score), theme.threshold(r.score, 15, 70))
                            ui.label(r.package).classes('font-medium')
                            ui.label(_(verdict)).classes('muted')
                    ui.link(_('Zum AUR-Scanner'), '/aur-scan')
            manual = [n for n in md.NEWS if n['manual']]
            with panel(_('News mit manuellem Eingriff'), _('Arch und CachyOS')):
                if not manual:
                    ui.label(_('Keine Meldungen mit manuellem Eingriff.')).classes('muted')
                for n in manual:
                    with ui.row().classes('w-full items-baseline gap-3 no-wrap'):
                        ui.label(n['date']).classes('muted shrink-0 w-24')
                        if n.get('link'):
                            ui.link(n['title'], n['link'], new_tab=True).classes('flex-1 min-w-0')
                        else:
                            ui.label(n['title']).classes('flex-1 min-w-0')
                        if n.get('new'):
                            theme.badge(_('neu seit letztem Update'), theme.ORANGE)

    content()
    s.on_change = content.refresh
