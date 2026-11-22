from html import escape

from nicegui import ui

from .. import catalog as md
from .. import state as st
from .. import theme
from ..dialogs import is_protected, start_remove
from ..i18n import _
from ..theme import card
from .overview import dashboard_header, loading_card

MAX_NODES = 60
MAX_USERS = 20

SEC_SLOT = r'''
<q-td :props="props">
  <span v-if="props.row.sev" class="q-badge" :style="{background: props.row.sev_color,
        color: ['#f8be34', '#f1813f', '#53a051'].includes(props.row.sev_color) ? '#111' : '#fff'}">
    {{ props.row.sev_label }}<span v-if="props.row.sev_count > 1"> ×{{ props.row.sev_count }}</span>
  </span>
</q-td>
'''


def conflict_pkgs() -> set[str]:
    return {p for c in md.CONFLICTS if c['severity'] != 'niedrig' for p in c['packages']}


def graph_options(center: str) -> dict:
    """Abhängigkeiten (2 Ebenen) und direkte Nutzer eines Pakets als Kraft-Graph."""
    nodes: dict[str, int] = {center: 0}
    conflicts = conflict_pkgs()
    links: list[tuple[str, str]] = []

    def walk(name: str, depth: int) -> None:
        pkg = md.package(name)
        if not pkg or depth > 2:
            return
        for dep in pkg['deps']:
            if dep not in nodes:
                if len(nodes) >= MAX_NODES:   # Kanten nur zu Knoten, die auch im Graphen sind
                    continue
                nodes[dep] = depth
                links.append((name, dep))
                walk(dep, depth + 1)
            else:
                links.append((name, dep))

    walk(center, 1)
    for user in md.dependents(center)[:MAX_USERS]:
        if (user, center) not in links:
            links.append((user, center))
        nodes.setdefault(user, -1)

    cats = list(md.SOURCES)
    data = []
    for name, depth in nodes.items():
        pkg = md.package(name) or {'source': 'repo'}
        upd = md.update(name)
        vuln = md.vulnerability(name)
        # Rand: Sicherheitslücke (Farbe = Schweregrad) vor Inkompatibilität (gestrichelt) vor Update (gepunktet)
        if vuln:
            style = {'borderColor': theme.SEVERITY_COLOR[vuln['severity']], 'borderWidth': 4, 'borderType': 'solid'}
        elif name in conflicts:
            style = {'borderColor': theme.RED, 'borderWidth': 2, 'borderType': 'dashed'}
        elif upd:
            style = {'borderColor': theme.GREEN, 'borderWidth': 2, 'borderType': 'dotted'}
        else:
            style = {'borderWidth': 0}
        data.append({
            'name': name, 'category': cats.index(pkg['source']),
            'symbolSize': 46 if name == center else 30 if depth in (1, -1) else 20,
            'itemStyle': style,
            'label': {'show': True, 'position': 'right', 'fontSize': 11, 'color': '#64748b',
                      'fontWeight': 700 if name == center else 400},
            'tooltip': {'formatter': f'{name}<br/>{pkg.get("version", "")}'
                                     + (_('<br/>Update → {new} (Risiko {risk})', new=upd['new'], risk=upd['risk']) if upd else '')
                                     + (_('<br/>⚠ potenzielle Inkompatibilität') if name in conflicts else '')
                                     + (_('<br/>Sicherheitslücken: {n} ({sev})', n=vuln['count'], sev=_(vuln['severity']))
                                        if vuln else '')},
        })
    return {
        'backgroundColor': 'transparent',
        'tooltip': {},
        'legend': [{'data': [md.SOURCES[c]['label'] for c in cats], 'bottom': 0,
                    'textStyle': {'color': '#64748b'}}],
        'animationDurationUpdate': 600,
        'series': [{
            'type': 'graph', 'layout': 'force', 'roam': True, 'draggable': True,
            'categories': [{'name': md.SOURCES[c]['label'], 'itemStyle': {'color': md.SOURCES[c]['color']}} for c in cats],
            'force': {'repulsion': 260, 'edgeLength': [60, 120], 'gravity': 0.08},
            'edgeSymbol': ['none', 'arrow'], 'edgeSymbolSize': 7,
            'lineStyle': {'color': 'rgba(100,116,139,.45)', 'width': 1.4, 'curveness': 0.12},
            'emphasis': {'focus': 'adjacency', 'lineStyle': {'width': 2.5}},
            'data': data,
            'links': [{'source': a, 'target': b} for a, b in links],
        }],
    }


def page() -> None:
    s = st.get()
    current = {'name': '', 'picked': ''}     # angezeigtes Paket / per Auswahlfeld markiertes Paket (zum Entfernen)

    @ui.refreshable
    def content() -> None:
        dashboard_header(_('Installierte Software'),
                         _('Paket anklicken, um seine Abhängigkeiten als Graph zu sehen (max. 2 Ebenen, gekürzt bei großen Bäumen)'))
        if loading_card():
            return
        pkgs = s.visible_packages()
        if not current['name']:
            current['name'] = next((p['name'] for p in pkgs if p['name'].startswith('linux-cachyos')), '')
        if current['name'] not in {p['name'] for p in pkgs} and pkgs:
            current['name'] = pkgs[0]['name']
        rows = []
        for p in pkgs:
            v = md.vulnerability(p['name'])
            rows.append(dict(p, id=p['name'], src=md.SOURCES[p['source']]['label'], ndeps=len(p['deps']),
                             upd='●' if md.update(p['name']) else '',
                             sev=v['severity'] if v else '', sev_label=_(v['severity']) if v else '',
                             sev_rank=md.SEVERITY_RANK.get(v['severity'], 0) + 1 if v else 0,
                             sev_color=theme.SEVERITY_COLOR.get(v['severity'], theme.GREY) if v else '',
                             sev_count=v['count'] if v else 0))
        affected = sum(1 for r in rows if r['sev'])

        with ui.grid().classes('w-full gap-4 grid-cols-1 xl:grid-cols-5'):
            with card('xl:col-span-2 p-2'):
                with ui.row().classes('w-full items-center gap-3 px-2 pt-2 no-wrap'):
                    search = ui.input(placeholder=_('Paket suchen …')).props('dense outlined clearable') \
                        .classes('flex-1')
                    with search.add_slot('prepend'):
                        ui.icon('search')
                    only_vuln = ui.checkbox(_('Nur betroffene ({n})', n=affected), value=False).props('dense')
                table = ui.table(
                    rows=rows, row_key='id', selection='single',
                    columns=[
                        {'name': 'name', 'label': _('Paket'), 'field': 'name', 'sortable': True, 'align': 'left'},
                        {'name': 'src', 'label': _('Quelle'), 'field': 'src', 'sortable': True, 'align': 'left'},
                        {'name': 'version', 'label': _('Version'), 'field': 'version', 'align': 'left'},
                        {'name': 'ndeps', 'label': _('Abh.'), 'field': 'ndeps', 'sortable': True},
                        {'name': 'upd', 'label': _('Upd.'), 'field': 'upd', 'style': f'color:{theme.GREEN}'},
                        {'name': 'sec', 'label': _('Sicherheit'), 'field': 'sev_rank', 'sortable': True, 'align': 'left'},
                    ],
                    pagination={'rowsPerPage': 12},
                ).classes('w-full cursor-pointer').props('flat dense')
                table.add_slot('body-cell-sec', SEC_SLOT)
                table.selected = [r for r in rows if r['id'] == current['picked']]
                search.bind_value_to(table, 'filter')

                def apply_filter(e) -> None:
                    table.rows = [r for r in rows if r['sev']] if e.value else rows
                    if e.value:
                        table.pagination = {'rowsPerPage': 12, 'sortBy': 'sec', 'descending': True}
                    table.update()
                only_vuln.on_value_change(apply_filter)

            with ui.column().classes('xl:col-span-3 gap-2'):
                # Entfernen steht bewusst abseits der Tabelle und ist erst aktiv, wenn ein Paket per Auswahlfeld markiert ist
                with ui.row().classes('w-full items-center justify-end gap-3'):
                    remove_hint = ui.label().classes('muted text-sm')
                    remove_btn = ui.button(_('Ausgewähltes Paket entfernen …'), icon='delete') \
                        .props('outline color=negative')
                    remove_btn.disable()
                with card('w-full'):
                    title = ui.label().classes('section-title')
                    info = ui.label().classes('muted text-sm')
                    vuln_line = ui.label().classes('font-medium')
                    chart = ui.echart(graph_options(current['name'])).classes('w-full').style('height:520px')
                    with ui.row().classes('gap-4 text-xs muted'):
                        for color, label in [(theme.RED, _('Sicherheitslücke kritisch')), (theme.ORANGE, _('hoch')),
                                             (theme.YELLOW, _('mittel')), (theme.BLUE, _('niedrig'))]:
                            ui.html(f'<span style="color:{color}">●</span> ' + escape(label), sanitize=False)
                        ui.html(f'<span style="color:{theme.RED}">◌</span> ' + escape(_('potenzielle Inkompatibilität')),
                                sanitize=False)
                        ui.html(f'<span style="color:{theme.GREEN}">◌</span> ' + escape(_('Update verfügbar')),
                                sanitize=False)
                        ui.label(_('Mausrad: Zoom · Ziehen: Knoten verschieben'))

        def show(name: str) -> None:
            current['name'] = name
            pkg = md.package(name)
            if not pkg:
                return
            title.text = name
            v = md.vulnerability(name)
            info.text = (f'{pkg["desc"]} · {pkg["version"]} · {pkg["size"]:.1f} MiB · '
                         + _('{deps} Abhängigkeiten · {users} Nutzer', deps=len(pkg['deps']), users=len(md.dependents(name)))
                         + (_(' (Graph zeigt {n})', n=MAX_USERS) if len(md.dependents(name)) > MAX_USERS else ''))
            vuln_line.text = (_('Sicherheitslücken: {n}, höchster Schweregrad {sev} ({ids})', n=v['count'],
                                sev=_(v['severity']), ids=', '.join(v['ids'][:4])) if v else '')
            vuln_line.style(f'color:{theme.SEVERITY_COLOR[v["severity"]]}' if v else '')
            vuln_line.set_visibility(bool(v))
            chart.options.clear()
            chart.options.update(graph_options(name))
            chart.update()

        def update_remove_button() -> None:
            name = current['picked']
            if not name:
                remove_btn.text, hint = _('Ausgewähltes Paket entfernen …'), _('Zum Entfernen links ein Paket per Auswahlfeld markieren')
                remove_btn.disable()
            elif '//' in name:                       # Flatpak-Runtimes räumt `flatpak uninstall --unused` auf
                remove_btn.text, hint = _('„{name}“ entfernen …', name=name), _('Runtimes werden nicht einzeln entfernt')
                remove_btn.disable()
            elif is_protected(name):
                remove_btn.text, hint = _('„{name}“ entfernen …', name=name), _('Systemkomponente – wird nicht entfernt')
                remove_btn.disable()
            else:
                remove_btn.text, hint = _('„{name}“ entfernen …', name=name), ''
                remove_btn.set_enabled(md.ready())
            remove_hint.text = hint

        def on_pick() -> None:
            current['picked'] = table.selected[0]['id'] if table.selected else ''
            update_remove_button()
        table.on_select(on_pick)
        update_remove_button()

        async def remove() -> None:
            pkg = md.package(current['picked'])
            if pkg and await start_remove(s, pkg):
                current['picked'] = ''
                content.refresh()
        remove_btn.on_click(remove)

        table.on('rowClick', lambda e: show(e.args[1]['name']))
        chart.on_point_click(lambda e: md.package(e.name) and show(e.name))
        show(current['name'])

    content()
    s.on_change = content.refresh
