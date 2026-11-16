from nicegui import ui

from .. import catalog as md
from .. import state as st
from ..i18n import _
from ..theme import card
from .overview import loading_card

SEV_COLOR = {'kritisch': 'purple', 'hoch': 'negative', 'mittel': 'warning', 'niedrig': 'info', 'unbekannt': 'grey'}


def page() -> None:
    s = st.get()

    @ui.refreshable
    def content() -> None:
        with ui.column().classes('gap-0'):
            ui.label(_('Sicherheitslücken & Risiken')).classes('text-2xl font-bold')
            ui.label(_('Quelle: Arch Linux Security Tracker (security.archlinux.org), abgeglichen mit den installierten '
                       'Versionen · Flatpak-Apps werden über Flathub aktualisiert und hier nicht erfasst')).classes('muted text-sm')
        if loading_card():
            return
        visible = {p['name'] for p in s.visible_packages()}
        cves = [c for c in md.CVES if c['package'] in visible]

        with ui.grid().classes('w-full gap-4 grid-cols-2 md:grid-cols-5'):
            for sev in ('kritisch', 'hoch', 'mittel', 'niedrig', 'unbekannt'):
                with card('items-center'):
                    ui.label(str(sum(1 for c in cves if c['severity'] == sev))).classes('kpi')
                    ui.badge(_(sev), color=SEV_COLOR[sev]).props('rounded')

        with card('w-full'):
            if not cves:
                with ui.row().classes('items-center gap-2'):
                    ui.icon('verified_user', color='positive', size='26px')
                    ui.label(_('Keine bekannten Sicherheitslücken in installierten Paketen.'))
            for c in cves:
                with ui.row().classes('w-full items-center gap-4 py-2 no-wrap') \
                        .style('border-bottom:1px solid var(--line)'):
                    ui.badge(_(c['severity']), color=SEV_COLOR.get(c['severity'], 'grey')).props('rounded') \
                        .classes('min-w-[72px] justify-center')
                    with ui.column().classes('gap-0 flex-1 min-w-0'):
                        with ui.row().classes('items-center gap-2'):
                            if c.get('link'):
                                ui.link(c['id'], c['link'], new_tab=True).classes('font-medium text-primary')
                            else:
                                ui.label(c['id']).classes('font-medium')
                            ui.label(f'· {_(c["title"])}').classes('font-medium')
                        cve_ids = c.get('cves', [])
                        ui.label(f'{c["package"]} {c.get("installed", "")} · ' + _('behoben in {v}', v=c['fixed_in']) + ' · '
                                 + ', '.join(cve_ids[:4]) + (f' (+{len(cve_ids) - 4})' if len(cve_ids) > 4 else '')) \
                            .classes('muted text-xs truncate')
                    fixed = c['status'] == 'behoben durch Update'
                    ui.chip(_(c['status']), icon='check_circle' if fixed else 'report',
                            color='positive' if fixed else 'warning' if c['fixed_in'] != '—' else 'negative') \
                        .props('outline dense')
                    if fixed and md.update(c['package']):
                        def sel(c=c):
                            s.selected.add(c['package'])
                            ui.notify(_('{pkg} zum Update ausgewählt', pkg=c['package']), type='positive', position='top-right')
                        ui.button(icon='add_task', on_click=sel).props('flat round').tooltip(_('Update auswählen'))

        if 'aur' in s.sources:
            flagged = sorted((r for r in md.SCANS.values() if r and r.score >= 15), key=lambda r: -r.score)
            with card('w-full'):
                with ui.row().classes('w-full items-center justify-between'):
                    ui.label(_('AUR-Sicherheitsscanner')).classes('section-title')
                    ui.link(_('Zum Scanner →'), '/aur-scan').classes('text-primary text-sm')
                if not flagged:
                    ui.label(_('Keine auffälligen AUR-Pakete.')).classes('muted text-sm')
                for r in flagged:
                    verdict, color, icon = r.verdict
                    with ui.row().classes('items-center gap-2'):
                        ui.icon(icon, color=color)
                        ui.label(_('{pkg} · {verdict} · Score {score} · {n} Befunde', pkg=r.package, verdict=_(verdict), score=r.score, n=len(r.findings))).classes('text-sm')

        manual = [n for n in md.NEWS if n['manual']]
        if manual:
            with card('w-full'):
                ui.label(_('News mit manuellem Eingriff')).classes('section-title mb-1')
                for n in manual:
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('front_hand', color='accent' if n.get('new') else 'grey')
                        if n.get('link'):
                            ui.link(f'{n["date"]} · {n["title"]}', n['link'], new_tab=True).classes('text-sm') \
                                .style('color:var(--text)')
                        else:
                            ui.label(f'{n["date"]} · {n["title"]}').classes('text-sm')
                        if n.get('new'):
                            ui.badge(_('neu seit letztem Update'), color='accent').props('outline rounded')

    content()
    s.on_change = content.refresh
