"""Neue Pakete suchen und installieren: Repo (CachyOS/Arch), AUR und Flatpak."""

import asyncio

from nicegui import ui

from .. import aur_scanner as sc
from .. import catalog as md
from .. import state as st
from .. import theme
from ..dialogs import start_install
from ..i18n import _
from ..sources import aur as aur_src
from ..sources import discover
from .overview import dashboard_header, loading_card

SEC_CELL = r'''
<q-td :props="props">
  <span v-if="props.row.sev" class="q-badge" :style="{background: props.row.sev_color,
        color: ['#f8be34', '#f1813f', '#53a051'].includes(props.row.sev_color) ? '#111' : '#fff'}">
    {{ props.row.sev_label }}<span v-if="props.row.sev_count > 1"> ×{{ props.row.sev_count }}</span>
  </span>
  <span v-else-if="props.row.flags" class="muted" style="font-size:12px">{{ props.row.flags }}</span>
  <span v-else-if="props.row.sec_note" class="muted" style="font-size:12px">{{ props.row.sec_note }}</span>
</q-td>
'''


def _row(c: dict) -> dict:
    sec = c.get('sec') or {}
    sev = sec.get('severity', '')
    return dict(id=f'{c["source"]}:{c["name"]}', name=c['name'], src=c.get('origin') or md.SOURCES[c['source']]['label'],
                repo=c['repo'], version=c['version'] or '–', pop=c['popularity'], pop_label=c['pop_label'] or '–',
                desc=c['desc'], sev=sev, sev_label=_(sev) if sev else '', sev_count=sec.get('count', 0),
                sev_rank=md.SEVERITY_RANK.get(sev, 0) + 1 if sev else 0,
                sev_color=theme.SEVERITY_COLOR.get(sev, theme.GREY) if sev else '',
                sec_note='' if sev or c['flags'] else (sec.get('note') or _('keine bekannten Lücken')),
                flags=' · '.join(c['flags']))


def page() -> None:
    s = st.get()
    # Zustand überlebt content.refresh() (neuer Systemstand), damit Suche und Auswahl nicht verloren gehen
    mem = {'source': 'repo', 'term': '', 'seq': 0, 'cands': [], 'message': '', 'chosen': {}, 'current': None}

    @ui.refreshable
    def content() -> None:
        dashboard_header(_('Neue Pakete installieren'),
                         _('Pakete aus Repo, AUR und Flathub nach Beliebtheit sortiert suchen. '
                           'Repo-Pakete werden mit einem vollständigen Systemupdate installiert.'))
        if loading_card():
            return
        by_id = {f'{c["source"]}:{c["name"]}': c for c in mem['cands']}

        with ui.row().classes('w-full items-center gap-3'):
            source = ui.toggle({'repo': _('CachyOS/Arch-Repo'), 'aur': 'AUR', 'flatpak': 'Flatpak'},
                               value=mem['source']).props('dense no-caps unelevated toggle-color=primary')
            search = ui.input(placeholder=_('Name oder Schlagwort suchen …'), value=mem['term']) \
                .props('dense outlined clearable debounce=600').classes('w-80')
            with search.add_slot('prepend'):
                ui.icon('search')
            spinner = ui.spinner(size='22px')
            spinner.set_visibility(False)
            ui.space()
            install_btn = ui.button(icon='download').props('unelevated color=primary')
        message = ui.label(mem['message']).classes('muted')

        with ui.grid().classes('w-full gap-4 grid-cols-1 xl:grid-cols-5'):
            with theme.card('xl:col-span-3 p-2'):
                table = ui.table(
                    rows=[_row(c) for c in mem['cands']], row_key='id', selection='multiple',
                    columns=[
                        {'name': 'name', 'label': _('Paket'), 'field': 'name', 'sortable': True, 'align': 'left'},
                        {'name': 'src', 'label': _('Quelle'), 'field': 'src', 'sortable': True, 'align': 'left'},
                        {'name': 'version', 'label': _('Version'), 'field': 'version', 'align': 'left'},
                        {'name': 'pop', 'label': _('Beliebtheit'), 'field': 'pop', 'sortable': True, 'align': 'left'},
                        {'name': 'sec', 'label': _('Sicherheit'), 'field': 'sev_rank', 'sortable': True, 'align': 'left'},
                        {'name': 'desc', 'label': _('Beschreibung'), 'field': 'desc', 'align': 'left',
                         'style': 'max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap',
                         'classes': 'muted'},
                    ],
                    pagination={'rowsPerPage': 15, 'sortBy': 'pop', 'descending': True},
                ).classes('w-full cursor-pointer').props('flat dense')
                table.add_slot('body-cell-sec', SEC_CELL)
                table.add_slot('body-cell-pop', '<q-td :props="props">{{ props.row.pop_label }}</q-td>')
                table.add_slot('no-data', '<div class="muted q-pa-md">' + _('Keine Treffer') + '</div>')
                table.selected = [r for r in table.rows if r['id'] in mem['chosen']]
            detail = ui.column().classes('xl:col-span-2 panel p-3 gap-2').style('padding:12px !important')

        # ---------- Verhalten ----------
        def refresh_button() -> None:
            n = len(mem['chosen'])
            install_btn.text = _('Installieren ({n})', n=n)
            install_btn.set_enabled(n > 0 and md.ready())
        refresh_button()

        def on_select() -> None:
            visible = {r['id'] for r in table.rows}
            chosen = {k: v for k, v in mem['chosen'].items() if k not in visible}
            chosen.update({r['id']: by_id[r['id']] for r in table.selected if r['id'] in by_id})
            mem['chosen'] = chosen
            refresh_button()
        table.on_select(on_select)

        async def run_search() -> None:
            mem['seq'] += 1
            seq = mem['seq']
            mem['source'], mem['term'] = source.value, (search.value or '').strip()
            spinner.set_visibility(True)
            warnings: list[str] = []
            text, cands = '', []
            try:
                cands = await discover.search(mem['source'], mem['term'], warn=warnings.append)
                if mem['source'] == 'aur' and not cands and len(mem['term']) < 2:
                    text = _('Suchbegriff eingeben (mindestens 2 Zeichen) – das AUR hat keine Gesamtliste.')
                elif not cands:
                    text = _('Keine Treffer')
            except ValueError as exc:
                text = str(exc)
            except Exception as exc:  # noqa: BLE001 – Netzfehler als Hinweis statt Absturz
                text = _('Suche fehlgeschlagen: {err}', err=exc)
            if seq != mem['seq']:                     # inzwischen neuere Suche gestartet
                return
            spinner.set_visibility(False)
            mem['cands'], mem['message'] = cands, ' '.join([text, *warnings]).strip()
            mem['current'] = None
            by_id.clear()
            by_id.update({f'{c["source"]}:{c["name"]}': c for c in cands})   # nur die Tabelle ändern: Fokus im Suchfeld bleibt
            table.rows = [_row(c) for c in cands]
            table.selected = [r for r in table.rows if r['id'] in mem['chosen']]
            table.update()
            message.text = mem['message']
            detail.clear()
            with detail:
                ui.label(_('Paket anklicken, um Details und Sicherheitsinfos zu sehen.')).classes('muted')

        source.on_value_change(run_search)
        search.on_value_change(run_search)
        search.on('keydown.enter', run_search)

        async def install() -> None:
            if await start_install(s, list(mem['chosen'].values())):
                mem['chosen'] = {}
                table.selected = []
                refresh_button()
                await run_search()                      # Installiertes verschwindet aus der Liste
        install_btn.on_click(install)

        async def show(cand: dict) -> None:
            mem['current'] = cand['name']
            detail.clear()
            with detail:
                ui.label(cand['name']).classes('event-pkg')
                ui.label(' · '.join(x for x in [cand.get('origin') or md.SOURCES[cand['source']]['label'],
                                                cand['repo'] if cand['source'] == 'repo' else '', cand['version']] if x)) \
                    .classes('muted')
                if cand['desc']:
                    ui.label(cand['desc']).classes('desc')
                ui.label(_('Beliebtheit: {pop}', pop=cand['pop_label'] or '–')).classes('text-sm')
                for flag in cand['flags']:
                    with ui.row().classes('items-center gap-2 no-wrap'):
                        ui.icon('warning', color='warning', size='18px')
                        ui.label(flag).classes('text-sm')
                sec = cand.get('sec') or {}
                ui.separator()
                ui.label(_('Sicherheit')).classes('panel-title')
                if sec.get('items'):
                    ui.label(_('Offene Lücken in der verfügbaren Version {v}', v=cand['version'])).classes('text-sm')
                    for it in sec['items'][:8]:
                        with ui.row().classes('items-center gap-2 no-wrap'):
                            theme.badge(_(it['severity']), theme.SEVERITY_COLOR.get(it['severity'], theme.GREY))
                            ui.link(it['id'], it['link'], new_tab=True)
                            ui.label(f'{it["title"]} · {_(it["status"])}').classes('text-sm muted')
                else:
                    ui.label(sec.get('note') or _('Keine offenen Lücken im Security Tracker')).classes('text-sm muted')
                deps = ui.label().classes('text-sm')
                scan_box = ui.column().classes('w-full gap-1')
                if cand['source'] == 'aur':
                    scan_btn = ui.button(_('Im AUR-Scanner prüfen'), icon='policy').props('flat dense')

                    async def scan() -> None:
                        scan_btn.props('loading')
                        try:
                            metas = await aur_src.scan_inputs([cand['name']])
                        except Exception as exc:  # noqa: BLE001
                            metas = {}
                            ui.notify(_('AUR nicht erreichbar: {err}', err=exc), type='negative', position='top-right')
                        scan_btn.props(remove='loading')
                        if cand['name'] not in metas:
                            ui.notify(_('{pkg} nicht im AUR gefunden', pkg=cand['name']), type='warning')
                            return
                        md.AUR_META[cand['name']] = metas[cand['name']] | {'lookup': True}
                        result = md.SCANS[cand['name']] = sc.scan_package(cand['name'], md.AUR_META[cand['name']])
                        verdict, color, icon = result.verdict
                        scan_box.clear()
                        with scan_box, ui.row().classes('items-center gap-2'):
                            ui.icon(icon, color=color, size='22px')
                            ui.label(f'{_(verdict)} · Score {result.score}').classes('font-semibold')
                            ui.link(_('Details'), '/aur-scan')
                    scan_btn.on_click(scan)
            if cand['source'] == 'repo':
                try:
                    info = await asyncio.to_thread(discover.repo_info, cand['name'])
                    dep = ' '.join(info.get('Depends On', '').split()) if info.get('Depends On', '') != 'None' else ''
                    deps.text = _('Abhängigkeiten: {deps}', deps=dep or _('keine'))
                except Exception as exc:  # noqa: BLE001 – Zusatzinformation
                    deps.text = _('Abhängigkeiten nicht lesbar: {err}', err=exc)

        table.on('rowClick', lambda e: show(by_id[e.args[1]['id']]) if e.args[1]['id'] in by_id else None)
        if mem['current'] and f'{mem["source"]}:{mem["current"]}' in by_id:
            background_show = by_id[f'{mem["source"]}:{mem["current"]}']
            ui.timer(0.05, lambda: show(background_show), once=True)
        else:
            with detail:
                ui.label(_('Paket anklicken, um Details und Sicherheitsinfos zu sehen.')).classes('muted')
        if not mem['cands'] and not mem['message'] and not mem['seq']:
            ui.timer(0.05, run_search, once=True)       # Startansicht: Top 50 nach Beliebtheit

    content()
    s.on_change = content.refresh
