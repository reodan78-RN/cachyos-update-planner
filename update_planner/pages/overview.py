from datetime import datetime

from nicegui import ui

from .. import catalog as md
from .. import state as st
from ..dialogs import start_update
from ..i18n import _
from ..theme import card, risk_bar


def loading_card() -> bool:
    """Zeigt Lade-/Fehlerzustand. True, wenn die Seite (noch) nichts anderes zeigen soll."""
    status = md.STATUS
    if status['state'] in ('idle', 'loading') and not md.VERSION:
        with card('w-full items-center py-10'):
            ui.spinner(size='42px')
            ui.label(_('Systemstand wird ermittelt …')).classes('text-lg font-semibold mt-3')
            ui.label(status['step'] or _('Starte …')).classes('muted text-sm')
        return True
    if status['state'] == 'error' and not md.VERSION:
        with card('w-full'):
            with ui.row().classes('items-center gap-3'):
                ui.icon('error', color='negative', size='28px')
                ui.label(_('Systemstand konnte nicht geladen werden')).classes('text-lg font-semibold')
            ui.label(status['step']).classes('text-sm')
            ui.button(_('Erneut versuchen'), icon='refresh', on_click=md.refresh).props('flat')
        return True
    for w in status['warnings']:
        with ui.row().classes('w-full items-center gap-2 p-3 glass').style('border-radius:14px'):
            ui.icon('warning', color='warning')
            ui.label(w).classes('text-sm')
    return False


def page() -> None:
    s = st.get()

    @ui.refreshable
    def content() -> None:
        loaded = md.STATUS.get('loaded_at')
        checked = datetime.fromtimestamp(loaded).strftime('%H:%M') if loaded else '—'
        with ui.row().classes('w-full items-end justify-between'):
            with ui.column().classes('gap-0'):
                ui.label(_('Übersicht')).classes('text-2xl font-bold')
                ui.label(_('Letzte Prüfung: heute, {time} · letztes Systemupdate: {last}', time=checked, last=md.last_upgrade_text())
                         + (_(' · DEMO-DATEN') if md.STATUS.get('demo') else '')).classes('muted text-sm')
            ui.button(_('Update planen'), icon='rocket_launch', on_click=lambda: start_update(s)) \
                .props('unelevated rounded size=lg').bind_enabled_from(md.STATUS, 'state', lambda v: v == 'ready')
        if loading_card():
            return

        updates = s.visible_updates()
        pkgs = s.visible_packages()
        avg = round(sum(u['risk'] for u in updates) / len(updates)) if updates else 0
        cves = md.CVES

        with ui.grid().classes('w-full gap-4 grid-cols-1 sm:grid-cols-2 xl:grid-cols-4'):
            with card():
                ui.label(_('Installierte Pakete')).classes('muted text-sm')
                ui.label(str(len(pkgs))).classes('kpi')
                with ui.row().classes('gap-1 mt-1'):
                    for key, src in md.SOURCES.items():
                        n = sum(1 for p in pkgs if p['source'] == key)
                        if n:
                            ui.badge(f'{src["label"]} {n}', color=None).style(f'background:{src["color"]}')
            with card():
                ui.label(_('Anstehende Updates')).classes('muted text-sm')
                ui.label(str(len(updates))).classes('kpi')
                with ui.row().classes('gap-1 mt-1'):
                    for key, src in md.SOURCES.items():
                        n = sum(1 for u in updates if u['source'] == key)
                        if n:
                            ui.badge(f'{src["label"]} {n}', color=None).style(f'background:{src["color"]}')
                ui.label(_('{mib} MiB Download', mib=f'{sum(u["size"] for u in updates):.0f}')).classes('muted text-xs mt-1')
            with card('items-center'):
                ui.label(_('Gesamt-Risiko')).classes('muted text-sm self-start')
                level, color = md.risk_level(avg)
                hexcolor = {'positive': '#22c55e', 'warning': '#f59e0b', 'negative': '#ef4444'}[color]
                ui.echart({
                    'series': [{
                        'type': 'gauge', 'startAngle': 200, 'endAngle': -20, 'min': 0, 'max': 100,
                        'radius': '100%', 'center': ['50%', '62%'],
                        'progress': {'show': True, 'width': 10, 'roundCap': True, 'itemStyle': {'color': hexcolor}},
                        'axisLine': {'lineStyle': {'width': 10, 'color': [[1, 'rgba(148,163,184,.25)']]}},
                        'axisTick': {'show': False}, 'splitLine': {'show': False}, 'axisLabel': {'show': False},
                        'pointer': {'show': False}, 'anchor': {'show': False},
                        'detail': {'valueAnimation': True, 'fontSize': 26, 'fontWeight': 700, 'offsetCenter': [0, '5%'],
                                   'color': hexcolor},
                        'data': [{'value': avg}],
                    }],
                }).classes('w-full h-28')
                ui.label(_('Durchschnitt · {level}', level=_(level))).classes('muted text-xs -mt-2')
            with card():
                ui.label(_('Sicherheitslücken')).classes('muted text-sm')
                ui.label(str(len(cves))).classes('kpi')
                crit = sum(1 for c in cves if c['severity'] in ('kritisch', 'hoch'))
                fixable = sum(1 for c in cves if c['status'] == 'behoben durch Update')
                ui.label(_('{crit} hoch/kritisch · {fixable} durch Update behoben', crit=crit, fixable=fixable)).classes('muted text-xs mt-1')

        with ui.grid().classes('w-full gap-4 grid-cols-1 lg:grid-cols-3'):
            with card('lg:col-span-2'):
                ui.label(_('Kritisch zuerst')).classes('section-title mb-2')
                if not updates:
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('verified', color='positive')
                        ui.label(_('System ist aktuell.'))
                for u in sorted(updates, key=lambda u: -u['risk'])[:6]:
                    src = md.SOURCES[u['source']]
                    with ui.row().classes('w-full items-center gap-3 py-1 no-wrap'):
                        ui.icon(src['icon']).style(f'color:{src["color"]}')
                        with ui.column().classes('gap-0 flex-1 min-w-0'):
                            ui.label(u['name']).classes('font-medium truncate')
                            ui.label(f'{u["old"]} → {u["new"]}').classes('muted text-xs truncate')
                        with ui.element().classes('w-32'):
                            risk_bar(u['risk'])
                        ui.label(str(u['risk'])).classes('w-8 text-right font-semibold')
                ui.link(_('Alle Updates anzeigen →'), '/updates').classes('text-primary text-sm mt-2')
            with card():
                ui.label('Arch / CachyOS News').classes('section-title mb-2')
                if not md.NEWS:
                    ui.label(_('Keine News geladen (offline?)')).classes('muted text-sm')
                for n in md.NEWS[:6]:
                    with ui.row().classes('items-start gap-2 no-wrap py-1'):
                        ui.icon('front_hand' if n['manual'] else 'campaign').classes(
                            'text-accent' if n['manual'] else 'muted')
                        with ui.column().classes('gap-0 min-w-0'):
                            if n.get('link'):
                                ui.link(n['title'], n['link'], new_tab=True).classes('text-sm').style('color:var(--text)')
                            else:
                                ui.label(n['title']).classes('text-sm')
                            ui.label(f'{n["date"]} · {n.get("source", "")}' + (_(' · manueller Eingriff') if n['manual'] else '')
                                     + (_(' · neu seit letztem Update') if n.get('new') else '')).classes('muted text-xs')

    content()
    s.on_change = content.refresh
