from datetime import datetime

from nicegui import ui

from .. import catalog as md
from .. import state as st
from .. import theme
from ..dialogs import start_update
from ..i18n import _
from ..theme import panel, single_value

RISK_CELL = r'''
<q-td :props="props">
  <span class="cell-num" :style="{background: props.value >= 60 ? '#dc4e41' : props.value >= 45 ? '#f1813f'
        : props.value >= 30 ? '#f8be34' : '#53a051'}">{{ props.value }}</span>
</q-td>
'''


def dashboard_header(title: str, desc: str, action=None) -> None:
    """Titelzeile eines Dashboards: Titel und Beschreibung links, optional eine Aktion rechts."""
    with ui.row().classes('w-full items-end justify-between no-wrap gap-4'):
        with ui.column().classes('gap-1'):
            ui.label(title).classes('dash-title')
            ui.label(desc + (_(' Es werden Demodaten angezeigt.') if md.STATUS.get('demo') else '')).classes('dash-desc')
        if action:
            action()


def loading_card() -> bool:
    """Zeigt Lade-/Fehlerzustand. True, wenn die Seite (noch) nichts anderes zeigen soll."""
    status = md.STATUS
    if status['state'] in ('idle', 'loading') and not md.VERSION:
        with panel(classes='w-full'):
            with ui.row().classes('items-center gap-3 py-6'):
                ui.spinner(size='28px')
                with ui.column().classes('gap-0'):
                    ui.label(_('Systemstand wird ermittelt …')).classes('panel-title')
                    ui.label(status['step'] or _('Starte …')).classes('muted')
        return True
    if status['state'] == 'error' and not md.VERSION:
        with panel(_('Systemstand konnte nicht geladen werden'), classes='w-full'):
            ui.label(status['step'])
            ui.button(_('Erneut versuchen'), icon='refresh', on_click=md.refresh).props('flat dense')
        return True
    for w in status['warnings']:
        with ui.row().classes('panel w-full items-stretch gap-0 no-wrap'):
            ui.element('div').classes('sev-stripe').style(f'background:{theme.YELLOW}')
            ui.label(w).classes('px-3 py-2')
    return False


def _days_since(raw: str | None) -> int | None:
    try:
        return (datetime.now() - datetime.fromisoformat(raw)).days if raw else None
    except ValueError:
        return None


def page() -> None:
    s = st.get()

    @ui.refreshable
    def content() -> None:
        def plan_button() -> None:
            ui.button(_('Update planen'), icon='system_update_alt', on_click=lambda: start_update(s)) \
                .props('unelevated color=primary').bind_enabled_from(md.STATUS, 'state', lambda v: v == 'ready')
        dashboard_header(_('Übersicht'), _('Zustand von Repo, AUR und Flatpak auf diesem Rechner.'), plan_button)
        if loading_card():
            return

        updates = s.visible_updates()
        pkgs = s.visible_packages()
        cves = md.CVES
        top = max(updates, key=lambda u: u['risk']) if updates else None
        severe = sum(1 for c in cves if c['severity'] in ('kritisch', 'hoch'))
        closed = sum(1 for c in cves if c['status'] == 'behoben durch Update')
        days = _days_since(md.SYSTEM.get('last_upgrade'))
        mib = sum(u['size'] for u in updates)

        # ---------- Single Values ----------
        with ui.grid().classes('w-full gap-3 grid-cols-2 md:grid-cols-3 xl:grid-cols-5'):
            single_value(_('Installierte Pakete'), len(pkgs), None,
                         ', '.join(f'{md.SOURCES[k]["label"]} {sum(1 for p in pkgs if p["source"] == k)}'
                                   for k in md.SOURCES if any(p['source'] == k for p in pkgs)))
            single_value(_('Anstehende Updates'), len(updates),
                         theme.GREEN if not updates else theme.BLUE, _('{mib} MiB Download', mib=f'{mib:.0f}'))
            single_value(_('Höchstes Risiko'), top['risk'] if top else 0, theme.threshold(top['risk'] if top else 0),
                         top['name'] if top else _('keine Updates'))
            single_value(_('Sicherheitslücken instl. Pakete'), len(cves),
                         theme.RED if severe else theme.YELLOW if cves else theme.GREEN,
                         _('{n} davon hoch oder kritisch', n=severe),
                         _('{n} davon mit Update geschlossen', n=closed), theme.GREEN if closed else None)
            single_value(_('Tage seit Systemupdate'), days if days is not None else '–',
                         theme.GREEN if days is not None and days < 7 else theme.YELLOW if days is not None and days < 21
                         else theme.RED, (md.SYSTEM.get('last_upgrade') or '')[:10])

        # ---------- Zeitverlauf und Risikoverteilung ----------
        history = md.SYSTEM.get('history') or []
        with ui.grid().classes('w-full gap-3 grid-cols-1 xl:grid-cols-3'):
            with panel(_('Paketänderungen pro Tag'), _('letzte 60 Tage, aus pacman.log'), classes='xl:col-span-2'):
                opts = theme.chart_base()
                opts['legend'] = {'top': 0, 'right': 0, 'textStyle': {'color': '#8d96a0'}, 'itemWidth': 10, 'itemHeight': 10}
                opts['grid']['top'] = 28
                opts['xAxis'].update(type='category', data=[h['date'][5:] for h in history])
                opts['yAxis'].update(type='value', minInterval=1)
                opts['series'] = [
                    {'name': _('aktualisiert'), 'type': 'bar', 'stack': 'a', 'data': [h['upgraded'] for h in history],
                     'itemStyle': {'color': theme.BLUE}, 'barMaxWidth': 14},
                    {'name': _('installiert'), 'type': 'bar', 'stack': 'a', 'data': [h['installed'] for h in history],
                     'itemStyle': {'color': theme.GREEN}},
                    {'name': _('entfernt'), 'type': 'bar', 'stack': 'a', 'data': [h['removed'] for h in history],
                     'itemStyle': {'color': theme.RED}},
                ]
                ui.echart(opts).classes('w-full').style('height:230px')
            with panel(_('Risiko der anstehenden Updates'), _('{n} Updates', n=len(updates))):
                buckets = [(_('niedrig'), 0, 30, theme.GREEN), (_('mittel'), 30, 60, theme.YELLOW), (_('hoch'), 60, 101, theme.RED)]
                opts = theme.chart_base()
                opts['tooltip'] = {'trigger': 'item', 'backgroundColor': '#0e1013', 'borderWidth': 0,
                                   'textStyle': {'color': '#e3e7eb'}}
                opts['grid'].update(left=64, top=8)
                opts['yAxis'] = {'type': 'category', 'data': [b[0] for b in buckets], 'axisLabel': {'color': '#8d96a0'},
                                 'axisLine': {'show': False}, 'axisTick': {'show': False}}
                opts['xAxis'] = {'type': 'value', 'minInterval': 1, 'axisLabel': {'color': '#8d96a0'},
                                 'splitLine': {'lineStyle': {'color': 'rgba(141,150,160,.18)'}}}
                opts['series'] = [{'type': 'bar', 'barWidth': 18, 'label': {'show': True, 'position': 'right', 'color': '#8d96a0'},
                                   'data': [{'value': sum(1 for u in updates if lo <= u['risk'] < hi),
                                             'itemStyle': {'color': c}} for _l, lo, hi, c in buckets]}]
                ui.echart(opts).classes('w-full').style('height:230px')

        # ---------- Tabellen ----------
        with ui.grid().classes('w-full gap-3 grid-cols-1 xl:grid-cols-3'):
            with panel(_('Riskanteste Updates'), _('Top 10 nach Risk Score'), classes='xl:col-span-2'):
                rows = [dict(id=u['name'], name=u['name'], src=md.SOURCES[u['source']]['label'],
                             version=f'{u["old"]} → {u["new"]}', risk=u['risk'])
                        for u in sorted(updates, key=lambda u: -u['risk'])[:10]]
                if not rows:
                    ui.label(_('System ist aktuell.')).classes('py-2')
                else:
                    table = ui.table(rows=rows, row_key='id', columns=[
                        {'name': 'name', 'label': _('Paket'), 'field': 'name', 'align': 'left'},
                        {'name': 'src', 'label': _('Quelle'), 'field': 'src', 'align': 'left'},
                        {'name': 'version', 'label': _('Version'), 'field': 'version', 'align': 'left'},
                        {'name': 'risk', 'label': _('Risiko'), 'field': 'risk', 'align': 'right'},
                    ]).classes('w-full').props('flat dense hide-pagination :rows-per-page-options="[0]"')
                    table.add_slot('body-cell-risk', RISK_CELL)
                    ui.link(_('Alle Updates anzeigen'), '/updates').classes('mt-1')
            with panel(_('System')):
                info = md.SYSTEM
                snap = 'snap-pac' if info.get('snap_pac') else 'Snapper' if info.get('snapper') else _('keine')
                for key, val in [(_('System'), info.get('os', 'Linux')), (_('Kernel'), info.get('kernel', '?')),
                                 (_('Dateisystem'), info.get('fs', '?')), (_('Snapshots'), snap),
                                 (_('Freier Speicher'), f'{info.get("free_gib", 0):.0f} GiB'),
                                 (_('Offene .pacnew'), len(info.get('pacnew') or [])),
                                 (_('Neustart ausstehend'), _('ja') if info.get('reboot_pending') else _('nein'))]:
                    with ui.row().classes('w-full justify-between no-wrap'):
                        ui.label(key).classes('muted')
                        ui.label(str(val)).classes('font-medium text-right')

        with panel(_('Arch und CachyOS News'), _('neueste Meldungen'), classes='w-full'):
            if not md.NEWS:
                ui.label(_('Keine News geladen. Ohne Netzverbindung bleibt dieser Bereich leer.')).classes('muted')
            for n in md.NEWS[:8]:
                with ui.row().classes('w-full items-baseline gap-3 no-wrap py-1').style('border-top:1px solid var(--line)'):
                    ui.label(n['date']).classes('muted shrink-0 w-24')
                    ui.label(n.get('source', '')).classes('muted shrink-0 w-16')
                    if n.get('link'):
                        ui.link(n['title'], n['link'], new_tab=True).classes('flex-1 min-w-0')
                    else:
                        ui.label(n['title']).classes('flex-1 min-w-0')
                    if n['manual']:
                        theme.badge(_('manueller Eingriff'), theme.ORANGE if n.get('new') else theme.GREY)

    content()
    s.on_change = content.refresh

