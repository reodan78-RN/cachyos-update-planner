from html import escape

from nicegui import ui

from .. import catalog as md
from .. import state as st
from ..i18n import _
from ..theme import card
from .overview import loading_card

MAX_NODES = 60
MAX_USERS = 20


def conflict_pkgs() -> set[str]:
    return {p for c in md.CONFLICTS if c['severity'] != 'niedrig' for p in c['packages']}


def graph_options(center: str) -> dict:
    """Abhängigkeiten (2 Ebenen) und direkte Nutzer eines Pakets als Kraft-Graph."""
    nodes: dict[str, int] = {center: 0}
    conflicts = conflict_pkgs()
    links: list[tuple[str, str]] = []

    def walk(name: str, depth: int) -> None:
        pkg = md.package(name)
        if not pkg or depth > 2 or len(nodes) >= MAX_NODES:
            return
        for dep in pkg['deps']:
            links.append((name, dep))
            if dep not in nodes:
                nodes[dep] = depth
                walk(dep, depth + 1)

    walk(center, 1)
    for user in md.dependents(center)[:MAX_USERS]:
        links.append((user, center))
        nodes.setdefault(user, -1)

    cats = list(md.SOURCES)
    data = []
    for name, depth in nodes.items():
        pkg = md.package(name) or {'source': 'repo'}
        upd = md.update(name)
        border = '#ef4444' if name in conflicts else '#f59e0b' if upd else 'rgba(0,0,0,0)'
        data.append({
            'name': name, 'category': cats.index(pkg['source']),
            'symbolSize': 46 if name == center else 30 if depth in (1, -1) else 20,
            'itemStyle': {'borderColor': border, 'borderWidth': 3 if border != 'rgba(0,0,0,0)' else 0,
                          'shadowBlur': 12, 'shadowColor': 'rgba(0,0,0,.18)'},
            'label': {'show': True, 'position': 'right', 'fontSize': 11, 'color': '#64748b',
                      'fontWeight': 700 if name == center else 400},
            'tooltip': {'formatter': f'{name}<br/>{pkg.get("version", "")}'
                                     + (_('<br/>Update → {new} (Risiko {risk})', new=upd['new'], risk=upd['risk']) if upd else '')
                                     + (_('<br/>⚠ potenzielle Inkompatibilität') if name in conflicts else '')},
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
    current = {'name': ''}

    @ui.refreshable
    def content() -> None:
        with ui.column().classes('gap-0'):
            ui.label(_('Installierte Software')).classes('text-2xl font-bold')
            ui.label(_('Paket anklicken, um seine Abhängigkeiten als Graph zu sehen (max. 2 Ebenen, gekürzt bei großen Bäumen)')).classes('muted text-sm')
        if loading_card():
            return
        pkgs = s.visible_packages()
        if not current['name']:
            current['name'] = next((p['name'] for p in pkgs if p['name'].startswith('linux-cachyos')), '')
        if current['name'] not in {p['name'] for p in pkgs} and pkgs:
            current['name'] = pkgs[0]['name']
        rows = [dict(p, id=p['name'], src=md.SOURCES[p['source']]['label'], ndeps=len(p['deps']),
                     upd='●' if md.update(p['name']) else '') for p in pkgs]

        with ui.grid().classes('w-full gap-4 grid-cols-1 xl:grid-cols-5'):
            with card('xl:col-span-2 p-2'):
                search = ui.input(placeholder=_('Paket suchen …')).props('dense outlined rounded clearable') \
                    .classes('w-full px-2 pt-2')
                with search.add_slot('prepend'):
                    ui.icon('search')
                table = ui.table(
                    rows=rows, row_key='id',
                    columns=[
                        {'name': 'name', 'label': _('Paket'), 'field': 'name', 'sortable': True, 'align': 'left'},
                        {'name': 'src', 'label': _('Quelle'), 'field': 'src', 'sortable': True, 'align': 'left'},
                        {'name': 'version', 'label': _('Version'), 'field': 'version', 'align': 'left'},
                        {'name': 'ndeps', 'label': _('Abh.'), 'field': 'ndeps', 'sortable': True},
                        {'name': 'upd', 'label': _('Upd.'), 'field': 'upd', 'classes': 'text-amber-500'},
                    ],
                    pagination={'rowsPerPage': 12},
                ).classes('w-full cursor-pointer').props('flat dense')
                search.bind_value_to(table, 'filter')

            with card('xl:col-span-3'):
                title = ui.label().classes('section-title')
                info = ui.label().classes('muted text-sm')
                chart = ui.echart(graph_options(current['name'])).classes('w-full').style('height:520px')
                with ui.row().classes('gap-4 text-xs muted'):
                    ui.html('<span style="color:#f59e0b">◯</span> ' + escape(_('Update verfügbar')), sanitize=False)
                    ui.html('<span style="color:#ef4444">◯</span> ' + escape(_('potenzielle Inkompatibilität')), sanitize=False)
                    ui.label(_('Mausrad: Zoom · Ziehen: Knoten verschieben'))

        def show(name: str) -> None:
            current['name'] = name
            pkg = md.package(name)
            if not pkg:
                return
            title.text = name
            info.text = (f'{pkg["desc"]} · {pkg["version"]} · {pkg["size"]:.1f} MiB · '
                         + _('{deps} Abhängigkeiten · {users} Nutzer', deps=len(pkg['deps']), users=len(md.dependents(name)))
                         + (_(' (Graph zeigt {n})', n=MAX_USERS) if len(md.dependents(name)) > MAX_USERS else ''))
            chart.options.clear()
            chart.options.update(graph_options(name))
            chart.update()

        table.on('rowClick', lambda e: show(e.args[1]['name']))
        chart.on_point_click(lambda e: md.package(e.name) and show(e.name))
        show(current['name'])

    content()
    s.on_change = content.refresh
