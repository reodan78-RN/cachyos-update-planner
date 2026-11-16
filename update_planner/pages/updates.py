from nicegui import ui

from .. import catalog as md
from .. import state as st
from ..dialogs import expand_holds, start_update
from ..i18n import _
from ..theme import card
from .overview import loading_card

RISK_SLOT = r'''
<q-td :props="props">
  <div class="row items-center no-wrap" style="gap:10px">
    <div class="risk-bar" style="flex:1">
      <div :style="{width: props.value + '%',
                    background: props.value >= 60 ? '#ef4444' : props.value >= 30 ? '#f59e0b' : '#22c55e'}"></div>
    </div>
    <b style="width:26px;text-align:right">{{ props.value }}</b>
  </div>
  <q-tooltip class="text-body2" max-width="340px">
    <div v-for="r in props.row.reasons" :key="r[0]">{{ r[1] > 0 ? '+' : '' }}{{ r[1] }} · {{ r[0] }}</div>
  </q-tooltip>
</q-td>
'''

SOURCE_SLOT = r'''
<q-td :props="props">
  <q-badge :style="{background: props.row.color}" rounded>{{ props.row.source_label }}</q-badge>
</q-td>
'''

VERSION_SLOT = r'''
<q-td :props="props">
  <span class="muted">{{ props.row.old }}</span>
  <q-icon name="east" size="14px" class="q-mx-xs"/>
  <b>{{ props.row.new }}</b>
</q-td>
'''


def page() -> None:
    s = st.get()

    @ui.refreshable
    def content() -> None:
        with ui.row().classes('w-full items-end justify-between'):
            with ui.column().classes('gap-0'):
                ui.label(_('Anstehende Updates')).classes('text-2xl font-bold')
                ui.label(_('Risk Score 0–100 · Maus über den Balken zeigt die Begründung')).classes('muted text-sm')
            summary = ui.label().classes('muted')
        if loading_card():
            return
        rows = [dict(u, color=md.SOURCES[u['source']]['color'], source_label=md.SOURCES[u['source']]['label'])
                for u in s.visible_updates()]
        partial = ui.row().classes('w-full items-center gap-2 p-3 no-wrap') \
            .style('background:rgba(245,158,11,.12);border-radius:12px')
        with partial:
            ui.icon('warning', color='warning')
            partial_text = ui.label('').classes('text-sm')

        with card('w-full p-2'):
            search = ui.input(placeholder=_('Update suchen …')).props('dense outlined rounded clearable').classes('w-72 px-2 pt-2')
            with search.add_slot('prepend'):
                ui.icon('search')
            table = ui.table(
                rows=rows, row_key='id', selection='multiple',
                columns=[
                    {'name': 'name', 'label': _('Paket'), 'field': 'name', 'sortable': True, 'align': 'left'},
                    {'name': 'source', 'label': _('Quelle'), 'field': 'source', 'sortable': True, 'align': 'left'},
                    {'name': 'version', 'label': _('Version'), 'field': 'new', 'align': 'left'},
                    {'name': 'size', 'label': _('Größe'), 'field': 'size', 'sortable': True,
                     ':format': 'v => v.toFixed(1) + " MiB"'},
                    {'name': 'risk', 'label': _('Risiko'), 'field': 'risk', 'sortable': True, 'align': 'left',
                     'style': 'min-width:180px'},
                ],
                pagination={'rowsPerPage': 25, 'sortBy': 'risk', 'descending': True},
            ).classes('w-full').props('flat')
            search.bind_value_to(table, 'filter')
            table.add_slot('body-cell-risk', RISK_SLOT)
            table.add_slot('body-cell-source', SOURCE_SLOT)
            table.add_slot('body-cell-version', VERSION_SLOT)
            table.selected = [r for r in rows if r['name'] in s.selected]

            def sync() -> None:
                visible = {r['name'] for r in rows}
                s.selected = (s.selected - visible) | {r['name'] for r in table.selected}
                sel = s.selected_updates()
                summary.text = _('{n} von {total} ausgewählt · {mib} MiB', n=len(sel), total=len(rows), mib=f'{sum(u["size"] for u in sel):.0f}')
                held = {u['name'] for u in md.UPDATES if u['source'] == 'repo' and u['name'] not in s.selected}
                held_all, extra = expand_holds(held)
                partial.set_visibility(bool(held) and len(held_all) < sum(u['source'] == 'repo' for u in md.UPDATES))
                partial_text.text = (_('{n} Repo-Pakete werden zurückgehalten (Teil-Update)', n=len(held_all))
                                     + (_(', davon {n} wegen gemeinsamer Abhängigkeiten', n=len(extra)) if extra else '')
                                     + _('. Arch unterstützt Teil-Updates nicht – nur kurzfristig nutzen.'))

            table.on_select(sync)
            sync()

        with ui.row().classes('w-full justify-between items-center'):
            with ui.row().classes('gap-2'):
                def pick(pred):
                    table.selected = [r for r in rows if pred(r)]
                    sync()
                ui.button(_('Nur niedriges Risiko'), icon='verified', on_click=lambda: pick(lambda r: r['risk'] < 30)).props('flat rounded')
                ui.button(_('Sicherheitsupdates'), icon='shield', on_click=lambda: pick(
                    lambda r: any(c['package'] == r['name'] for c in md.CVES))).props('flat rounded')
                ui.button(_('Alle'), icon='done_all', on_click=lambda: pick(lambda r: True)).props('flat rounded')
            ui.button(_('Ausgewählte aktualisieren'), icon='rocket_launch', on_click=lambda: start_update(s)) \
                .props('unelevated rounded size=lg')

    content()
    s.on_change = content.refresh
